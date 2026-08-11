"""
경로 예측 (Phase 4) — 선형 외삽

Phase 3(TrackHistory)이 계산한 "실제 이동 벡터"(속도+방향)를 그대로 유지한다고 가정하고,
N분 뒤 위치를 직선으로 추정한다. 칼만 필터 같은 정교한 기법(관측 노이즈 보정, 가속도 반영)은
다음 단계 후보로 남겨두고, 지금은 "최근 속도로 계속 직진한다"는 가장 단순한 가정부터 시작한다.

이렇게 단순하게 시작하는 이유: 항공기는 짧은 시간(수 분) 안에는 방향을 급격히 안 바꾸는 게
일반적이라, 선형 외삽만으로도 "이 트랙이 보호구역에 접근하고 있는가"를 판단하는 용도로는
충분히 쓸모 있다. 급기동(선회 등)은 이 가정이 깨지는 대표적 예외 상황.
"""
import math
from typing import Optional

EARTH_RADIUS_KM = 6371.0


def destination_point(lat: float, lon: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    """
    출발점(lat, lon)에서 방위각(bearing_deg)으로 distance_km만큼 대권(great circle)을 따라
    이동했을 때의 도착 좌표를 계산한다 (구면 삼각법 표준 공식 - "given start point, bearing,
    distance, find destination").
    """
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)
    theta = math.radians(bearing_deg)
    delta = distance_km / EARTH_RADIUS_KM   # 이동거리를 지구 반지름 대비 각거리(라디안)로 환산

    phi2 = math.asin(
        math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )

    lon2 = (math.degrees(lambda2) + 540) % 360 - 180  # 경도를 -180~180 범위로 정규화
    return math.degrees(phi2), lon2


def predict_position(
    lat: float, lon: float, speed_ms: Optional[float], heading_deg: Optional[float], minutes_ahead: float
) -> dict:
    """
    현재 위치에서 주어진 속도·방향을 minutes_ahead분 동안 유지한다고 가정했을 때의
    예상 위치를 계산한다. 속도/방향 중 하나라도 없으면 예측 불가 -> None 반환.
    """
    if speed_ms is None or heading_deg is None:
        return {"predicted_lat": None, "predicted_lon": None, "prediction_minutes": minutes_ahead}

    distance_km = (speed_ms * minutes_ahead * 60.0) / 1000.0
    pred_lat, pred_lon = destination_point(lat, lon, heading_deg, distance_km)
    return {
        "predicted_lat": round(pred_lat, 5),
        "predicted_lon": round(pred_lon, 5),
        "prediction_minutes": minutes_ahead,
    }


if __name__ == "__main__":
    # 자체 테스트: 방향별 기본 동작이 직관과 일치하는지 확인
    print("=== 정동(90도) 이동: 위도는 그대로, 경도만 증가해야 함 ===")
    r1 = predict_position(lat=0.0, lon=0.0, speed_ms=100.0, heading_deg=90.0, minutes_ahead=10)
    print(r1)
    assert abs(r1["predicted_lat"]) < 0.01, "적도에서 정동 이동인데 위도가 변했습니다!"
    assert r1["predicted_lon"] > 0, "정동 이동인데 경도가 증가하지 않았습니다!"
    print("✅ 통과")

    print("\n=== 정북(0도) 이동: 위도만 증가, 경도는 그대로여야 함 ===")
    r2 = predict_position(lat=36.0, lon=127.0, speed_ms=100.0, heading_deg=0.0, minutes_ahead=10)
    print(r2)
    assert r2["predicted_lat"] > 36.0, "정북 이동인데 위도가 증가하지 않았습니다!"
    assert abs(r2["predicted_lon"] - 127.0) < 0.001, "정북 이동인데 경도가 변했습니다!"
    print("✅ 통과")

    print("\n=== 속도/방향 정보 없음: 예측 불가 -> None 반환해야 함 ===")
    r3 = predict_position(lat=36.0, lon=127.0, speed_ms=None, heading_deg=90.0, minutes_ahead=5)
    print(r3)
    assert r3["predicted_lat"] is None
    print("✅ 통과")

    print("\n=== 거리 검산: 200m/s로 5분(300초) 이동 = 60km여야 함 ===")
    import sys, os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from fusion.geofence import distance_km

    r4 = predict_position(lat=37.5665, lon=126.9780, speed_ms=200.0, heading_deg=90.0, minutes_ahead=5.0)
    actual_km = distance_km(37.5665, 126.9780, r4["predicted_lat"], r4["predicted_lon"])
    print(f"{r4} -> 실제 이동거리 계산값: {actual_km:.2f}km")
    assert abs(actual_km - 60.0) < 0.1, f"200m/s*5분=60km여야 하는데 {actual_km}km가 나왔습니다!"
    print("✅ 통과")

    print("\n🎉 trajectory_prediction.py 자체 테스트 전체 통과")
