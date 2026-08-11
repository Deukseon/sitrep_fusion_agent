"""
LangGraph 파이프라인: 수집 -> 융합 -> 스코어링 -> 브리핑

노드 구성:
  fetch_data      : OpenSky 항적 + 기상 데이터 수집
  fuse_sensors    : 합성 레이더/열화상/SIGINT 병합
  assess_threats  : 위협 스코어링 및 우선순위화
  generate_brief  : Claude API로 자연어 브리핑 생성
"""
import sys
import os
import time
import logging
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

from data_sources.flight_tracker import fetch_tracks
from data_sources.weather_api import fetch_weather
from data_sources.synthetic_sensors import enrich_track_with_synthetic_sensors
from fusion.threat_scoring import score_track, rank_tracks, ThreatAssessment
from fusion.track_history import get_track_history, compute_velocity_from_history
from fusion.trajectory_prediction import predict_position
from config import ANALYST_REVIEW_THRESHOLD, ANTHROPIC_API_KEY, PREDICTION_MINUTES
import audit_log

logger = logging.getLogger(__name__)


class PipelineState(TypedDict):
    bbox: tuple                       # 감시 구역 좌표 박스
    center_lat: float
    center_lon: float
    raw_tracks: list                  # dict 형태의 항적 리스트
    enriched_tracks: list              # 합성 센서 병합된 항적
    visibility_ok: bool
    assessments: list                  # ThreatAssessment 리스트
    briefing_text: Optional[str]
    analyst_decision: Optional[str]    # "approve" / "reject" / None(대기중)
    alert_sent: bool
    track_history: dict                # Phase 3 이력 (선택) - 없으면 이번 관측치만으로 예측 (Phase 4)


def fetch_data(state: PipelineState) -> PipelineState:
    """1. 실시간 항적 + 기상 수집"""
    tracks = fetch_tracks(bbox=state["bbox"])
    weather = fetch_weather(lat=state["center_lat"], lon=state["center_lon"])

    state["raw_tracks"] = [t.to_radar_like_dict() for t in tracks]
    state["visibility_ok"] = weather.visibility_ok if weather else True
    logger.info("항적 %d건 수집, 시정 양호: %s", len(state['raw_tracks']), state['visibility_ok'])
    return state


def fuse_sensors(state: PipelineState) -> PipelineState:
    """2. 합성 레이더/열화상/SIGINT 병합"""
    enriched = [enrich_track_with_synthetic_sensors(t) for t in state["raw_tracks"]]
    state["enriched_tracks"] = enriched
    logger.info("%d건 센서 융합 완료", len(enriched))
    return state


def predict_trajectory(state: PipelineState) -> PipelineState:
    """
    2.5. 경로 예측 (Phase 4)

    트랙 이력(track_history)이 있으면 Phase 3에서 계산한 "실제 이동 벡터"(이력 기반 속도/방향)를,
    없으면(처음 관측된 트랙, 또는 continuous_monitor.py 없이 main.py 단독 실행 시) API가 준
    순간 속도/방향을 그대로 써서 N분 뒤 위치를 선형 외삽으로 예측한다.
    """
    history = state.get("track_history", {})
    now = time.time()

    for t in state["enriched_tracks"]:
        if t.get("lat") is None or t.get("lon") is None:
            t["predicted_lat"] = None
            t["predicted_lon"] = None
            t["prediction_minutes"] = PREDICTION_MINUTES
            t["velocity_source"] = "no_position"
            continue

        prior_points = get_track_history(history, t["track_id"])
        speed, heading, source = None, None, "reported_fallback"

        if prior_points:
            current_point = {"timestamp": now, "lat": t["lat"], "lon": t["lon"]}
            vel = compute_velocity_from_history(prior_points + [current_point])
            if vel["computed_speed_ms"] is not None:
                speed, heading, source = vel["computed_speed_ms"], vel["computed_heading_deg"], "history"

        if speed is None:  # 이력 없음/부족 -> API 원본 순간값으로 폴백
            speed, heading = t.get("speed_ms"), t.get("heading_deg")

        pred = predict_position(t["lat"], t["lon"], speed, heading, PREDICTION_MINUTES)
        t.update(pred)
        t["velocity_source"] = source if pred["predicted_lat"] is not None else "unavailable"

    logger.info("%d건 경로 예측 완료 (%.0f분 뒤 예상 위치 계산)", len(state["enriched_tracks"]), PREDICTION_MINUTES)
    return state


def assess_threats(state: PipelineState) -> PipelineState:
    """3. 위협 스코어링 + 우선순위 정렬"""
    assessments = [
        score_track(t, visibility_ok=state["visibility_ok"])
        for t in state["enriched_tracks"]
    ]
    ranked = rank_tracks(assessments)
    # checkpointer 직렬화 호환을 위해 dataclass -> dict로 변환해서 상태에 저장
    state["assessments"] = [a.to_dict() for a in ranked]
    logger.info("%d건 평가 완료", len(state['assessments']))
    return state


def route_after_assessment(state: PipelineState) -> str:
    """
    위협 평가 결과에 따라 분기.
    HIGH(55점) 이상 트랙이 하나라도 있으면 분석관 확인 노드로,
    없으면 바로 브리핑 생성으로 보낸다.
    """
    if any(a["score"] >= ANALYST_REVIEW_THRESHOLD for a in state["assessments"]):
        return "analyst_review"
    return "generate_brief"


def log_pending_review(state: PipelineState) -> PipelineState:
    """
    3.4. 분석관 확인 "대기 시작" 로그 (analyst_review와 분리된 별도 노드)

    [버그 수정, 2026-08-11] 처음엔 이 로그를 analyst_review 노드 안, interrupt() 호출
    "직전"에 뒀었는데, LangGraph의 interrupt()는 재개(resume)될 때 노드 함수 전체를
    처음부터 다시 실행하고 interrupt() 호출 지점에서만 저장된 값을 즉시 반환하는
    방식이라, interrupt() 이전의 코드가 (최초 대기 시) 1번 + (재개 시 재실행) 1번,
    총 2번 실행돼서 로그가 중복 기록되는 문제가 있었다. interrupt()가 있는 노드만
    재개 시 재실행되고 그 "앞" 노드는 재실행되지 않으므로, 이 로그를 별도 노드로
    분리해서 정확히 한 번만 기록되게 했다.
    """
    high_priority = [a for a in state["assessments"] if a["score"] >= ANALYST_REVIEW_THRESHOLD]
    for t in high_priority:
        audit_log.log_decision(
            track_id=t["track_id"], label=t["label"], score=t["score"], level=t["level"],
            identity=t.get("identity", "UNKNOWN"), decision="pending", alert_sent=False,
            zone_name=t.get("zone_name"),
        )
    return state


def analyst_review(state: PipelineState) -> PipelineState:
    """
    3.5. 분석관 확인 대기 (그래프 실행을 여기서 멈춘다)

    interrupt()가 호출되면 그래프 실행이 중단되고,
    payload가 호출자(사람/UI)에게 반환된다.
    사람이 Command(resume="approve" 또는 "reject")로 재개하면
    아래 코드가 이어서 실행된다.

    [버그 수정, 2026-08-11] "대기 시작" 로그는 log_pending_review 노드로 분리했다
    (이 노드 자체는 재개 시 재실행되므로 여기엔 재실행돼도 안전한 코드만 남겨야 함).
    """
    high_priority = [a for a in state["assessments"] if a["score"] >= ANALYST_REVIEW_THRESHOLD]
    payload = {
        "message": "다음 고위협 트랙에 대해 경보 발령 승인이 필요합니다.",
        "tracks": high_priority,  # 이미 dict이므로 그대로 전달
    }
    decision = interrupt(payload)   # 여기서 실행이 멈춘다
    state["analyst_decision"] = decision
    logger.info("분석관 결정 수신: %s", decision)

    # 감사 로그: 실제 결정 결과를 기록. interrupt() "이후" 코드는 재개 시 딱 한 번만
    # 실행되므로(재실행되는 건 interrupt() 이전 부분뿐) 여기는 원래부터 중복 문제가 없었다.
    alert_sent = (decision == "approve")
    for t in high_priority:
        audit_log.log_decision(
            track_id=t["track_id"], label=t["label"], score=t["score"], level=t["level"],
            identity=t.get("identity", "UNKNOWN"), decision=decision, alert_sent=alert_sent,
            zone_name=t.get("zone_name"),
        )

    return state


def route_after_review(state: PipelineState) -> str:
    """분석관이 승인했으면 경보 발령, 거부했으면 브리핑만 생성"""
    if state.get("analyst_decision") == "approve":
        return "send_alert"
    return "generate_brief"


def send_alert(state: PipelineState) -> PipelineState:
    """4. 경보 발령 (분석관 승인 후에만 도달)"""
    state["alert_sent"] = True
    logger.warning("경보 발령됨 (분석관 승인 완료)")
    return state


def generate_brief(state: PipelineState) -> PipelineState:
    """4. 자연어 브리핑 생성 (Claude API 사용)"""
    top = state["assessments"][:5]
    if not top:
        state["briefing_text"] = "감시 구역 내 포착된 항적이 없습니다."
        return state

    summary_lines = []
    for a in top:
        zone_info = f", 보호구역: {a['zone_name']}({a['zone_distance_km']}km)" if a.get("zone_name") else ""
        summary_lines.append(
            f"- 트랙 {a['track_id']} ({a['label']}): 점수 {a['score']}, 등급 {a['level']}, "
            f"식별: {a.get('identity', 'UNKNOWN')}{zone_info}, 근거: {', '.join(a['reasons'])}"
        )
    raw_summary = "\n".join(summary_lines)

    status_note = ""
    if state.get("analyst_decision") == "approve":
        status_note = "\n[상태: 분석관 승인 완료, 경보 발령됨]"
    elif state.get("analyst_decision") == "reject":
        status_note = "\n[상태: 분석관이 경보 발령을 보류함]"
    raw_summary += status_note

    try:
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(model="claude-sonnet-5", max_tokens=500)
        prompt = (
            "당신은 방공 관제 상황실 분석관입니다. 아래 위협 평가 데이터를 바탕으로 "
            "지휘관에게 보고할 간결한 한국어 브리핑을 작성하세요. "
            "우선순위 상위 항목부터 언급하고, 권고 조치를 포함하세요.\n\n"
            f"{raw_summary}"
        )
        response = llm.invoke(prompt)
        state["briefing_text"] = response.content
    except Exception as e:
        # API 키 미설정 등으로 실패해도 파이프라인이 죽지 않도록 폴백 제공
        state["briefing_text"] = f"[LLM 호출 실패 - 원시 데이터로 대체]\n{raw_summary}"
        logger.error("LLM 호출 실패: %s", e)

    return state


def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("fetch_data", fetch_data)
    graph.add_node("fuse_sensors", fuse_sensors)
    graph.add_node("predict_trajectory", predict_trajectory)
    graph.add_node("assess_threats", assess_threats)
    graph.add_node("log_pending_review", log_pending_review)
    graph.add_node("analyst_review", analyst_review)
    graph.add_node("send_alert", send_alert)
    graph.add_node("generate_brief", generate_brief)

    graph.set_entry_point("fetch_data")
    graph.add_edge("fetch_data", "fuse_sensors")
    graph.add_edge("fuse_sensors", "predict_trajectory")
    graph.add_edge("predict_trajectory", "assess_threats")

    # 조건부 분기 1: 고위협 트랙 존재 여부에 따라 분석관 확인(대기 로그 먼저) or 바로 브리핑
    graph.add_conditional_edges(
        "assess_threats",
        route_after_assessment,
        {"analyst_review": "log_pending_review", "generate_brief": "generate_brief"},
    )
    graph.add_edge("log_pending_review", "analyst_review")

    # 조건부 분기 2: 분석관 승인 여부에 따라 경보 발령 or 브리핑만
    graph.add_conditional_edges(
        "analyst_review",
        route_after_review,
        {"send_alert": "send_alert", "generate_brief": "generate_brief"},
    )

    graph.add_edge("send_alert", "generate_brief")
    graph.add_edge("generate_brief", END)

    # interrupt를 쓰려면 checkpointer가 반드시 필요하다 (멈춘 지점의 상태를 기억해야 함)
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s", datefmt="%H:%M:%S")
    from config import MONITOR_BBOX, CENTER_LAT, CENTER_LON

    app = build_graph()

    # checkpointer를 쓰는 그래프는 실행마다 thread_id가 필요하다
    # (같은 thread_id로 다시 invoke하면 멈췄던 지점부터 재개된다)
    config = {"configurable": {"thread_id": "demo-session-1"}}

    initial_state: PipelineState = {
        "bbox": MONITOR_BBOX,
        "center_lat": CENTER_LAT,
        "center_lon": CENTER_LON,
        "raw_tracks": [],
        "enriched_tracks": [],
        "visibility_ok": True,
        "assessments": [],
        "briefing_text": None,
        "analyst_decision": None,
        "alert_sent": False,
    }

    result = app.invoke(initial_state, config=config)

    # interrupt가 걸리면 result에 "__interrupt__" 키가 담겨 반환된다
    if "__interrupt__" in result:
        interrupt_info = result["__interrupt__"][0]
        print("\n=== ⏸ 분석관 확인 대기 중 ===")
        print(interrupt_info.value["message"])
        for t in interrupt_info.value["tracks"]:
            print(f"  - {t['track_id']} ({t['label']}) [{t['level']}] {t['score']}점")
            print(f"    근거: {', '.join(t['reasons'])}")

        # 실전에서는 여기서 사람 입력(웹 UI 버튼, CLI input 등)을 받는다.
        # 데모에서는 콘솔 입력으로 승인/거부를 받는다.
        answer = input("\n경보를 발령할까요? (approve/reject): ").strip().lower()
        decision = "approve" if answer == "approve" else "reject"

        # 멈췄던 지점부터 재개
        result = app.invoke(Command(resume=decision), config=config)

    print("\n=== 최종 브리핑 ===")
    print(result["briefing_text"])
    print(f"\n경보 발령 여부: {result.get('alert_sent', False)}")
    print("\n=== 전체 트랙 ===")
    for a in result["assessments"]:
        print(f"  [{a['level']:8}] {a['track_id']} ({a['label']}) - {a['score']}점")
