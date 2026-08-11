"""
Phase 5 - 대시보드(COP) 백엔드 (FastAPI)

`continuous_monitor.py`는 CLI 단독 실행용으로 그대로 남겨뒀다. 이 파일은 대시보드
전용 서버로 별도로 만든 이유가 있다:

LangGraph의 interrupt()/Command(resume=...)는 "같은 프로세스, 같은 메모리
(MemorySaver)" 안에서만 재개할 수 있다. continuous_monitor.py를 별도 프로세스로
띄워두고 FastAPI가 다른 프로세스에서 그 그래프에 승인 신호를 보내는 구조는 애초에
불가능하다 (체크포인트가 프로세스 메모리에만 있어서). 그래서 이 서버가 폴링 루프
자체를 내장해서, 그래프 인스턴스 하나를 프로세스 생애주기 내내 유지한다.

엔드포인트:
  GET  /status          감시 상태(마지막 갱신 시각, 분석관 확인 대기 여부 등)
  GET  /tracks           현재 항적 + 위협 평가 (지도/패널용)
  POST /approve           분석관 승인/거부 (interrupt 재개)
  GET  /alerts/recent     최근 발령된 경보 이력 (audit_log.jsonl 기반)

실행: uvicorn dashboard_api:app --reload --port 8000
"""
import asyncio
import time
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os
from pydantic import BaseModel

from agent.graph import build_graph, PipelineState, Command
from config import MONITOR_BBOX, CENTER_LAT, CENTER_LON, POLL_INTERVAL_SECONDS
from fusion.track_history import load_history, save_history, update_history
import audit_log

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


class DashboardState:
    """
    이 서버 프로세스 안에서만 유효한 전역 상태 (재시작하면 초기화됨 - track_history.json은
    파일로 남아있어서 이력만은 재시작해도 이어짐, 나머지 화면용 캐시는 초기화).
    """
    def __init__(self):
        self.graph = build_graph()
        self.thread_id = "dashboard-session"
        self.history: dict = load_history()
        self.raw_tracks: list = []
        self.assessments: list = []
        self.briefing_text: Optional[str] = None
        self.awaiting_review: bool = False
        self.pending_tracks: list = []
        self.last_updated: Optional[float] = None
        self.cycle_count: int = 0
        self.lock = asyncio.Lock()   # 사이클 실행과 승인 처리가 같은 그래프 인스턴스를 동시에 건드리지 않게


dash = DashboardState()


def _graph_config() -> dict:
    return {"configurable": {"thread_id": dash.thread_id}}


async def run_cycle() -> None:
    """continuous_monitor.py의 run_one_cycle과 같은 로직을, 대시보드 전역 상태에 반영하도록 재작성."""
    async with dash.lock:
        if dash.awaiting_review:
            # 이전 사이클이 아직 분석관 승인을 기다리는 중이면 새 사이클을 시작하지 않는다.
            # (승인 전에 근거 데이터가 계속 바뀌면 "무슨 근거로 그 판단을 했는지"가 흔들린다)
            logger.info("분석관 확인 대기 중 - 이번 사이클 건너뜀")
            return

        initial_state: PipelineState = {
            "bbox": MONITOR_BBOX, "center_lat": CENTER_LAT, "center_lon": CENTER_LON,
            "raw_tracks": [], "enriched_tracks": [], "visibility_ok": True,
            "assessments": [], "briefing_text": None,
            "analyst_decision": None, "alert_sent": False,
            "track_history": dash.history,
        }

        # graph.invoke는 동기(blocking) 함수라 이벤트 루프를 막지 않도록 스레드풀에서 실행
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: dash.graph.invoke(initial_state, config=_graph_config())
        )
        dash.cycle_count += 1

        if "__interrupt__" in result:
            info = result["__interrupt__"][0]
            dash.awaiting_review = True
            dash.pending_tracks = info.value["tracks"]
            dash.raw_tracks = result["raw_tracks"]
            dash.assessments = result["assessments"]
            dash.last_updated = time.time()
            logger.warning("고위협 트랙 %d건 - 분석관 확인 대기", len(dash.pending_tracks))
            return   # 승인 전까지는 다음 폴링에서도 계속 대기 상태 유지

        # interrupt 없이 끝난 사이클 - Phase 3 이력 갱신 + 저장
        now = time.time()
        dash.history = update_history(dash.history, result["raw_tracks"], timestamp=now)
        save_history(dash.history)

        dash.raw_tracks = result["raw_tracks"]
        dash.assessments = result["assessments"]
        dash.briefing_text = result.get("briefing_text")
        dash.last_updated = now
        logger.info("사이클 완료 (트랙 %d건)", len(dash.raw_tracks))


async def polling_loop() -> None:
    while True:
        try:
            await run_cycle()
        except Exception as e:   # 사이클 하나가 실패해도 서버 전체가 죽지 않게
            logger.error("사이클 실행 중 오류: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(polling_loop())
    logger.info("대시보드 서버 시작 - %d초 간격 폴링 루프 가동", POLL_INTERVAL_SECONDS)
    yield
    task.cancel()


app = FastAPI(title="다중 센서 위협 융합 상황실 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 로컬 상황실 데모용 - 외부 배포 시 실제 프론트엔드 도메인으로 제한 필요
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApproveRequest(BaseModel):
    decision: str   # "approve" 또는 "reject"


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/")
async def get_dashboard():
    """대시보드 화면(static/dashboard.html)을 직접 서빙 - 같은 오리진이라 CORS 문제가 아예 없다."""
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))


@app.get("/status")
async def get_status():
    return {
        "last_updated": dash.last_updated,
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "awaiting_review": dash.awaiting_review,
        "cycle_count": dash.cycle_count,
        "track_count": len(dash.raw_tracks),
    }


@app.get("/tracks")
async def get_tracks():
    return {
        "raw_tracks": dash.raw_tracks,
        "assessments": dash.assessments,
        "awaiting_review": dash.awaiting_review,
        "pending_tracks": dash.pending_tracks if dash.awaiting_review else [],
        "last_updated": dash.last_updated,
    }


@app.post("/approve")
async def approve_decision(req: ApproveRequest):
    if req.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision은 'approve' 또는 'reject'여야 합니다")
    if not dash.awaiting_review:
        raise HTTPException(status_code=409, detail="현재 분석관 확인 대기 중인 트랙이 없습니다")

    async with dash.lock:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: dash.graph.invoke(Command(resume=req.decision), config=_graph_config())
        )
        dash.awaiting_review = False
        dash.pending_tracks = []
        dash.raw_tracks = result["raw_tracks"]
        dash.assessments = result["assessments"]
        dash.briefing_text = result.get("briefing_text")

        now = time.time()
        dash.history = update_history(dash.history, result["raw_tracks"], timestamp=now)
        save_history(dash.history)
        dash.last_updated = now

    return {
        "decision": req.decision,
        "alert_sent": result.get("alert_sent", False),
        "briefing_text": dash.briefing_text,
    }


@app.get("/alerts/recent")
async def get_recent_alerts(limit: int = 10):
    return {"alerts": audit_log.read_recent_alerts(limit=limit)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
