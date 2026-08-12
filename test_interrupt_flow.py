"""
analyst_review interrupt 흐름 테스트 (네트워크 API 없이 목데이터로 검증)

실행: pytest test_interrupt_flow.py -v
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from data_sources.flight_tracker import AirTrack

# 고위협 트랙 1개(저고도 고속 미확인체) 목데이터
MOCK_TRACKS = [
    AirTrack(icao24="danger01", callsign=None, longitude=128.0, latitude=36.1,
              altitude_m=800, velocity_ms=220, heading_deg=45, vertical_rate_ms=-3),
]


def test_interrupt_flow_end_to_end():
    with patch("agent.graph.fetch_tracks", return_value=MOCK_TRACKS), \
         patch("agent.graph.fetch_weather", return_value=None):

        from agent.graph import build_graph, PipelineState, Command

        app = build_graph()
        config = {"configurable": {"thread_id": "test-session"}}

        initial_state: PipelineState = {
            "bbox": (33.0, 124.0, 39.0, 132.0), "center_lat": 36.5, "center_lon": 127.8,
            "raw_tracks": [], "enriched_tracks": [], "visibility_ok": True,
            "assessments": [], "briefing_text": None,
            "analyst_decision": None, "alert_sent": False,
        }

        result = app.invoke(initial_state, config=config)

        assert "__interrupt__" in result, "고위협 트랙인데 interrupt가 안 걸렸습니다!"
        print("✅ interrupt 정상 발생 확인")
        interrupt_info = result["__interrupt__"][0]
        print("   대기 메시지:", interrupt_info.value["message"])
        print("   대상 트랙:", [t["track_id"] for t in interrupt_info.value["tracks"]])
        print("   (직렬화 경고 없이 dict로 안전하게 전달됨)")

        # 분석관이 "approve"로 재개했다고 가정
        final = app.invoke(Command(resume="approve"), config=config)
        assert final.get("alert_sent") is True, "승인했는데 alert_sent가 True가 아닙니다!"
        print("✅ 승인 후 경보 발령 확인 (alert_sent=True)")
        print("\n--- 최종 브리핑 ---")
        print(final["briefing_text"])
