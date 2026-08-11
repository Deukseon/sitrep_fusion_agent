"""
CRITICAL 규칙 기반 오버라이드 테스트 (2026-08-11, Phase 5 준비 중 발견한 이슈 해결)

배경: 점수 누적 방식만으로는 "미확인 물체가 보호구역 안에 실제로 있는" 경우가
75~70점(HIGH)에 머무를 수 있는데, 구역과 무관하게 우연히 센서 신호 3종이 겹친
물체는 95점(CRITICAL)이 나올 수 있어서 대시보드 우선순위가 실제 위험도와
어긋날 수 있었다. "미확인 + 구역 내부 실제 위치"는 점수와 무관하게 CRITICAL을
강제하는 규칙을 추가해서 해결.

검증 항목:
  1. 미확인 물체가 구역 내부에 있으면, 원래 점수가 얼마든 CRITICAL(80점)로 강제되는가
  2. 식별된(호출부호 있는) 물체는 구역 내부에 있어도 오버라이드가 안 걸리는가
     (정상 항공기까지 강제로 CRITICAL 만들면 과탐 폭증하므로 중요한 안전장치)
  3. 예측 경로상 "진입 예상"이 API 순간값 폴백(velocity_source="reported_fallback")뿐이면
     오버라이드 대상이 아닌가 (신뢰도 낮은 1회성 추정만으로 최고 등급을 확정하지 않음)
  4. 원래도 80점 이상이던 경우 오버라이드 문구가 중복으로 안 붙는가
  5. 이력(TrackHistory)으로 2회 이상 실관측해서 뒷받침된 예측 진입은 CRITICAL로 강제되는가
     (2026-08-11 세션 후반 추가 - "진입 예정 + 미식별도 위험 요소로 봐야 하지 않냐"는 논의 반영)
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fusion.threat_scoring import score_track
from fusion.trajectory_prediction import predict_position, destination_point
from config import PROTECTED_ZONES, PREDICTION_MINUTES

INCHEON = next(z for z in PROTECTED_ZONES if "인천" in z["name"])


def test_unidentified_inside_zone_forces_critical():
    print("=== 테스트 1: 미확인 + 구역 내부 -> 점수 무관하게 CRITICAL 강제 ===")
    track = {
        "track_id": "OVERRIDE01", "label": "UNKNOWN", "lat": INCHEON["lat"], "lon": INCHEON["lon"],
        "altitude_m": 900, "speed_ms": 180, "heading_deg": 90,
        "radar": {}, "thermal": {}, "sigint": {},   # 다른 센서 신호는 아무것도 없음
    }
    result = score_track(track, visibility_ok=True)
    assert result.level == "CRITICAL", f"오버라이드가 걸려야 하는데 {result.level}입니다"
    assert result.score >= 80.0, f"점수가 80 이상이어야 하는데 {result.score}입니다"
    assert any("오버라이드" in r for r in result.reasons), "오버라이드 근거 문구가 없습니다"
    print(f"✅ 점수={result.score}, 등급={result.level}, 근거에 오버라이드 문구 포함 확인")


def test_identified_track_not_overridden():
    print("\n=== 테스트 2: 식별된(호출부호 있는) 물체는 구역 내부에 있어도 오버라이드 안 걸림 ===")
    track = {
        "track_id": "SAFE01", "label": "KAL001", "lat": INCHEON["lat"], "lon": INCHEON["lon"],
        "altitude_m": 900, "speed_ms": 180, "heading_deg": 90,
        "radar": {}, "thermal": {}, "sigint": {},
    }
    result = score_track(track, visibility_ok=True)
    assert result.level != "CRITICAL", f"식별된 항공기까지 CRITICAL이 되면 과탐입니다! (등급={result.level})"
    assert not any("오버라이드" in r for r in result.reasons), "식별된 항공기에 오버라이드가 걸렸습니다!"
    print(f"✅ 점수={result.score}, 등급={result.level} (오버라이드 없음, 정상)")


def test_predicted_entry_not_overridden():
    print("\n=== 테스트 3: 예측 진입(API 순간값 폴백)은 오버라이드 대상 아님 - 기존 점수 방식 유지 ===")
    start_lat, start_lon = destination_point(INCHEON["lat"], INCHEON["lon"], bearing_deg=180, distance_km=30)
    speed_ms = 25_000 / (PREDICTION_MINUTES * 60)
    track = {
        "track_id": "PREDICT01", "label": "UNKNOWN", "lat": start_lat, "lon": start_lon,
        "altitude_m": 9000, "speed_ms": speed_ms, "heading_deg": 0.0,   # 저고도 조건(1500m) 안 걸리게 고고도로 설정
        "radar": {}, "thermal": {}, "sigint": {},
        "velocity_source": "reported_fallback",   # 방금 처음 본 트랙 - 이력 없이 API 순간값으로만 추정
    }
    pred = predict_position(start_lat, start_lon, speed_ms, 0.0, PREDICTION_MINUTES)
    track.update(pred)

    result = score_track(track, visibility_ok=True)
    assert result.level != "CRITICAL", f"신뢰도 낮은 폴백 예측만으로 오버라이드가 걸리면 안 됩니다 (등급={result.level})"
    assert not any("오버라이드" in r for r in result.reasons), "폴백 예측에 오버라이드 문구가 붙었습니다!"
    print(f"✅ 점수={result.score}, 등급={result.level} (예측 진입은 +20점만, 오버라이드 없음)")


def test_history_backed_prediction_forces_critical():
    print("\n=== 테스트 5: 이력(2회 이상 실관측)으로 뒷받침된 예측 진입은 CRITICAL 강제 ===")
    start_lat, start_lon = destination_point(INCHEON["lat"], INCHEON["lon"], bearing_deg=180, distance_km=30)
    speed_ms = 25_000 / (PREDICTION_MINUTES * 60)
    track = {
        "track_id": "PREDICT02", "label": "UNKNOWN", "lat": start_lat, "lon": start_lon,
        "altitude_m": 9000, "speed_ms": speed_ms, "heading_deg": 0.0,
        "radar": {}, "thermal": {}, "sigint": {},
        "velocity_source": "history",   # TrackHistory로 여러 번 실관측해서 뒷받침된 궤적
    }
    pred = predict_position(start_lat, start_lon, speed_ms, 0.0, PREDICTION_MINUTES)
    track.update(pred)

    result = score_track(track, visibility_ok=True)
    assert result.level == "CRITICAL", f"이력 뒷받침 예측인데 오버라이드가 안 걸렸습니다 (등급={result.level})"
    assert result.score >= 80.0
    assert any("이력 기반 궤적상" in r for r in result.reasons), "이력 기반 오버라이드 문구가 없습니다"
    print(f"✅ 점수={result.score}, 등급={result.level}, 근거: {[r for r in result.reasons if '오버라이드' in r]}")


def test_no_duplicate_override_text():
    print("\n=== 테스트 4: 원래도 80점 넘는 경우 오버라이드 문구 중복 안 붙음 ===")
    track = {
        "track_id": "ALREADYHIGH01", "label": "UNKNOWN", "lat": INCHEON["lat"], "lon": INCHEON["lon"],
        "altitude_m": 900, "speed_ms": 180, "heading_deg": 90,
        "radar": {"rcs_dbsm": 3.2, "detection_confidence": 0.91},
        "thermal": {"is_hot": True},
        "sigint": {"emission_detected": True, "frequency_band": "unknown", "signal_strength_db": -40},
    }
    result = score_track(track, visibility_ok=True)
    override_mentions = [r for r in result.reasons if "오버라이드" in r]
    assert len(override_mentions) == 0, f"이미 80점 넘는 경우 오버라이드 문구가 붙으면 안 되는데: {override_mentions}"
    assert result.level == "CRITICAL"
    print(f"✅ 점수={result.score}(원래도 CRITICAL), 오버라이드 문구 없음 확인")


if __name__ == "__main__":
    test_unidentified_inside_zone_forces_critical()
    test_identified_track_not_overridden()
    test_predicted_entry_not_overridden()
    test_no_duplicate_override_text()
    test_history_backed_prediction_forces_critical()
    print("\n🎉 CRITICAL 오버라이드 규칙 테스트 전체 통과")
