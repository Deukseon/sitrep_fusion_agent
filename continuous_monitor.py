"""
동적 감시 스크립트 — 일정 주기로 실제 API를 폴링하며 고위협 트랙을 감지한다.

main.py와의 차이:
  - main.py: 1회 실행, HIGH 이상이면 사람이 콘솔에서 approve/reject 입력 후 종료
  - 이 스크립트: 계속 반복 실행, 사람 승인 대기 없이 HIGH 이상 발견 시 로그만 남기고 계속 진행
    (터미널 앞에 사람이 계속 붙어있지 않아도 되도록 설계. 실제 '승인 버튼'은
     나중에 웹 서비스로 만들 때 자연스럽게 추가하는 게 적합하다.)

주의:
  - OpenSky 익명 사용자는 하루 400 크레딧 제한이 있으므로, POLL_INTERVAL_SECONDS를
    너무 짧게 잡지 않는다. .env에 OPENSKY_CLIENT_ID/SECRET을 넣어두면 자동으로
    등록 계정 인증을 써서 4000 크레딧까지 확보된다.
  - Ctrl+C로 언제든 중단 가능.
  - 모든 상태 로그는 logging 모듈을 통해 출력된다 (운영 시스템처럼 타임스탬프 포함).

사용법:
  python continuous_monitor.py
"""
import time
import logging
from agent.graph import build_graph, PipelineState, Command, ANALYST_REVIEW_THRESHOLD
from config import MONITOR_BBOX, CENTER_LAT, CENTER_LON, POLL_INTERVAL_SECONDS
from fusion.track_history import load_history, save_history, update_history, get_track_history, compute_velocity_from_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def run_one_cycle(cycle_num: int, history: dict) -> dict:
    """
    한 사이클을 실행하고, 갱신된 트랙 이력(history)을 반환한다.

    Phase 3: 사이클마다 새 thread_id로 시작하는 건 그대로다 (LangGraph 파이프라인
    자체의 상태 지속성은 아직 다음 범위). 다만 이 함수가 별도로 track_history.py를
    통해 raw_tracks를 이력에 누적시켜서, "같은 항공기를 여러 사이클에 걸쳐 추적"하는
    문제를 파이프라인 밖에서 먼저 해결한다.
    """
    app = build_graph()
    graph_config = {"configurable": {"thread_id": f"monitor-cycle-{cycle_num}"}}

    initial_state: PipelineState = {
        "bbox": MONITOR_BBOX, "center_lat": CENTER_LAT, "center_lon": CENTER_LON,
        "raw_tracks": [], "enriched_tracks": [], "visibility_ok": True,
        "assessments": [], "briefing_text": None,
        "analyst_decision": None, "alert_sent": False,
        "track_history": history,   # Phase 4: 이력을 파이프라인에 넘겨 경로 예측에 사용
    }

    result = app.invoke(initial_state, config=graph_config)

    if "__interrupt__" in result:
        # 사람이 없어도 감시가 멈추지 않도록, 여기서는 자동으로 'reject'(경보 미발령)하고
        # 대신 로그로 고위협 발견 사실을 명확히 남긴다.
        # -> 진짜 승인 워크플로우는 웹 서비스(FastAPI) 단계에서 다시 다룬다.
        info = result["__interrupt__"][0]
        logger.warning("고위협 트랙 %d건 발견 (자동 로그만, 경보 미발령)", len(info.value["tracks"]))
        for t in info.value["tracks"]:
            logger.warning("  - %s (%s) [%s] %s점", t["track_id"], t["label"], t["level"], t["score"])
        result = app.invoke(Command(resume="reject"), config=graph_config)

    total = len(result["assessments"])
    high_count = sum(1 for a in result["assessments"] if a["score"] >= ANALYST_REVIEW_THRESHOLD)
    logger.info("총 %d건 평가, 고위협(HIGH 이상) %d건", total, high_count)

    # --- Phase 3: 트랙 이력 갱신 + 이력 기반 속도/방향 재계산 ---
    now = time.time()
    history = update_history(history, result["raw_tracks"], timestamp=now)

    recomputed = 0
    for t in result["raw_tracks"]:
        points = get_track_history(history, t["track_id"])
        vel = compute_velocity_from_history(points)
        if vel["computed_speed_ms"] is None:
            continue  # 이번이 첫 관측이라 아직 계산 불가
        recomputed += 1
        reported_speed = t.get("speed_ms")
        altitude = t.get("altitude_m")
        speed_diff = None
        if reported_speed is not None:
            speed_diff = round(vel["computed_speed_ms"] - reported_speed, 1)
        logger.info(
            "  이력 기반 재계산 - %s: 고도=%sm, 이력 %d점, 계산속도=%sm/s(API보고=%sm/s, 차이=%s), 계산방향=%s도",
            t["track_id"], altitude, vel["history_points"], vel["computed_speed_ms"],
            reported_speed, speed_diff, vel["computed_heading_deg"],
        )
    if recomputed:
        logger.info("이력 기반 속도/방향 재계산: %d건 (2회 이상 관측된 트랙만 가능)", recomputed)

    return history


def main():
    logger.info("동적 감시 시작 (폴링 주기: %d초, Ctrl+C로 중단)", POLL_INTERVAL_SECONDS)
    history = load_history()
    logger.info("트랙 이력 로드: 기존 %d개 트랙 이력 발견", len(history))
    cycle = 0
    try:
        while True:
            cycle += 1
            logger.info("--- 사이클 %d 시작 ---", cycle)
            history = run_one_cycle(cycle, history)
            save_history(history)
            logger.info("%d초 대기 중...", POLL_INTERVAL_SECONDS)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        save_history(history)
        logger.info("감시 종료 (Ctrl+C 입력됨, 이력 저장 완료)")


if __name__ == "__main__":
    main()
