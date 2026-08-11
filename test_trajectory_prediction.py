"""
Phase 4 (경로 예측) 통합 테스트 - 목데이터로 검증

검증 항목:
  1. fusion.threat_scoring.score_track()이 예측 위치가 보호구역에 들어오면
     "예측 경로 기준 N분 후 ... 진입 예상(+20)" 근거를 추가하는가
  2. 이미 현재 위치가 구역 안이면(7번 항목) 8번 항목이 중복 가산하지 않는가
  3. 파이프라인 전체(agent/graph.py)를 목데이터로 돌렸을 때, 이력 없이도(API 원본값
     폴백) 예측이 정상 동작하고 assessments에 predicted_lat/lon이 채워지는가
"""
import os
import sys
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fusion.trajectory_prediction import predict_position, destination_point
from fusion.threat_scoring import score_track
from config import PROTECTED_ZONES, PREDICTION_MINUTES

INCHEON = next(z for z in PROTECTED_ZONES if "인천" in z["name"])


def test_predicted_zone_entry_adds_score():
    print("=== 테스트 1: 예측 위치가 보호구역에 들어오면 +20 가산 ===")
    # 인천공항 중심에서 남쪽 30km 지점에서 정북(0도)으로 이동하는 트랙을 하나 만든다.
    start_lat, start_lon = destination_point(INCHEON["lat"], INCHEON["lon"], bearing_deg=180, distance_km=30)
    speed_ms = 25_000 / (PREDICTION_MINUTES * 60)  # 5분 동안 정확히 25km 이동 -> 구역 중심 5km 지점 도착

    track = {
        "track_id": "APPROACH01", "label": "MOCK", "lat": start_lat, "lon": start_lon,
        "altitude_m": 9000, "speed_ms": speed_ms, "heading_deg": 0.0,
        "radar": {}, "thermal": {}, "sigint": {},
    }
    pred = predict_position(start_lat, start_lon, speed_ms, 0.0, PREDICTION_MINUTES)
    track.update(pred)

    result = score_track(track, visibility_ok=True)
    assert result.predicted_zone_name == "인천국제공항 관제권", f"예측 진입 구역이 인천이어야 하는데 {result.predicted_zone_name}"
    assert any("진입 예상" in r for r in result.reasons), f"진입 예상 근거가 없습니다: {result.reasons}"
    assert result.zone_name is None or result.zone_distance_km > 20, "현재 위치가 이미 구역 안이면 이 테스트 시나리오가 아닙니다"
    print(f"✅ 예측 위치가 '{result.predicted_zone_name}'에 진입 -> 점수 {result.score}, 근거: {[r for r in result.reasons if '진입 예상' in r]}")


def test_no_double_count_when_already_inside():
    print("\n=== 테스트 2: 이미 현재 위치가 구역 안이면 8번 항목 중복 가산 안 함 ===")
    track = {
        "track_id": "INSIDE01", "label": "MOCK", "lat": INCHEON["lat"], "lon": INCHEON["lon"],
        "altitude_m": 500, "speed_ms": 50.0, "heading_deg": 90.0,
        "radar": {}, "thermal": {}, "sigint": {},
    }
    pred = predict_position(INCHEON["lat"], INCHEON["lon"], 50.0, 90.0, PREDICTION_MINUTES)
    track.update(pred)

    result = score_track(track, visibility_ok=True)
    zone_reasons = [r for r in result.reasons if "인천" in r]
    assert any("반경 내 위치" in r for r in zone_reasons), "현재 위치 기준 반경 내 근거가 없습니다"
    assert not any("진입 예상" in r for r in zone_reasons), f"이미 안에 있는데 진입 예상까지 중복 가산됐습니다: {zone_reasons}"
    print(f"✅ 중복 가산 없음, 근거: {zone_reasons}")


def test_pipeline_end_to_end_without_history():
    print("\n=== 테스트 3: 파이프라인 전체 - 이력 없이(API 원본값 폴백) 예측 동작 ===")
    from data_sources.flight_tracker import AirTrack

    start_lat, start_lon = destination_point(INCHEON["lat"], INCHEON["lon"], bearing_deg=180, distance_km=30)
    speed_ms = 25_000 / (PREDICTION_MINUTES * 60)

    mock_tracks = [
        AirTrack(icao24="E2E01", callsign="TEST", longitude=start_lon, latitude=start_lat,
                  altitude_m=9000, velocity_ms=speed_ms, heading_deg=0.0, vertical_rate_ms=0),
    ]

    with patch("agent.graph.fetch_tracks", return_value=mock_tracks), \
         patch("agent.graph.fetch_weather", return_value=None):
        from agent.graph import build_graph, PipelineState

        app = build_graph()
        config = {"configurable": {"thread_id": "test-phase4-e2e"}}
        initial_state: PipelineState = {
            "bbox": (33.0, 124.0, 39.0, 132.0), "center_lat": 36.5, "center_lon": 127.8,
            "raw_tracks": [], "enriched_tracks": [], "visibility_ok": True,
            "assessments": [], "briefing_text": None,
            "analyst_decision": None, "alert_sent": False,
            "track_history": {},   # 이력 없음 -> API 원본값(velocity_ms/heading_deg)으로 폴백해야 함
        }
        result = app.invoke(initial_state, config=config)
        if "__interrupt__" in result:
            from agent.graph import Command
            result = app.invoke(Command(resume="reject"), config=config)

        assessment = next(a for a in result["assessments"] if a["track_id"] == "E2E01")
        assert assessment["predicted_lat"] is not None, "이력 없이도 API 원본값으로 예측이 됐어야 합니다!"
        assert assessment["predicted_zone_name"] == "인천국제공항 관제권", \
            f"예측 진입 구역이 인천이어야 하는데 {assessment['predicted_zone_name']}"
        print(f"✅ 이력 없이도(폴백) 예측 정상 동작: predicted=({assessment['predicted_lat']}, {assessment['predicted_lon']}), "
              f"진입 예상 구역={assessment['predicted_zone_name']}, 최종 점수={assessment['score']}")


if __name__ == "__main__":
    test_predicted_zone_entry_adds_score()
    test_no_double_count_when_already_inside()
    test_pipeline_end_to_end_without_history()
    print("\n🎉 Phase 4 경로 예측 통합 테스트 전체 통과")
