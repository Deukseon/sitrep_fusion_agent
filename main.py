"""
실행 진입점

사용법:
  (선택) ANTHROPIC_API_KEY, OPENSKY_CLIENT_ID/SECRET을 .env 파일에 넣어두면 자동 로드됨
  python main.py

설정값(감시 구역, 임계값 등)은 config.py에서 관리합니다.
"""
import logging
from agent.graph import build_graph, PipelineState, Command
from config import MONITOR_BBOX, CENTER_LAT, CENTER_LON

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s", datefmt="%H:%M:%S")


def main():
    app = build_graph()
    # checkpointer가 상태를 이 thread_id로 구분해서 저장한다.
    # 세션(사용자, 감시 구역)별로 다른 thread_id를 쓰면 여러 파이프라인을 동시에 운용할 수 있다.
    graph_config = {"configurable": {"thread_id": "prod-session-1"}}

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

    result = app.invoke(initial_state, config=graph_config)

    # 고위협 트랙이 있으면 분석관 확인을 위해 여기서 멈춰 있다
    if "__interrupt__" in result:
        info = result["__interrupt__"][0]
        print("\n" + "=" * 50)
        print("⏸ 분석관 확인 필요")
        print("=" * 50)
        print(info.value["message"])
        for t in info.value["tracks"]:
            print(f"  - {t['track_id']} ({t['label']}) [{t['level']}] {t['score']}점")
            print(f"    근거: {', '.join(t['reasons'])}")

        answer = input("\n경보를 발령할까요? (approve/reject): ").strip().lower()
        decision = "approve" if answer == "approve" else "reject"
        result = app.invoke(Command(resume=decision), config=graph_config)

    print("\n" + "=" * 50)
    print("상황 브리핑")
    print("=" * 50)
    print(result["briefing_text"])
    print(f"\n경보 발령 여부: {result.get('alert_sent', False)}")

    print("\n" + "=" * 50)
    print(f"전체 트랙 수: {len(result['assessments'])}")
    for a in result["assessments"]:
        print(f"  [{a['level']:8}] {a['track_id']} ({a['label']}) - {a['score']}점")


if __name__ == "__main__":
    main()
