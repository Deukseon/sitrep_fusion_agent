"""
폴링 간격 vs 속도별 '탐지 놓침' 검증 스크립트

가설: "폴링 간격이 길고 속도가 빠르면, 보호구역을 통과하는 항적을
아예 한 번도 관측 못 하고 놓칠 수 있다."

시나리오: 보호구역 중심을 정확히 관통하는 직선 경로(가장 관대한 조건 -
실제로는 구역 가장자리만 스치는 경로가 훨씬 더 놓치기 쉬움. 이건 최선의
경우만 확인하는 것이므로, 여기서도 놓치면 실제 상황은 더 나쁘다고 봐야 함).

사용법:
  탐색용 표 전체 출력: python test_polling_detection.py
  회귀 테스트만 실행: pytest test_polling_detection.py -v
    (Phase 3에서 실제로 내렸던 결론 - "POLL_INTERVAL_SECONDS=30이면 최악의
    경우(가장자리 스침+초음속)도 탐지된다"를 고정된 assert로 못박아둔다.
    나중에 크레딧 절약 등의 이유로 이 값을 다시 늘리면 이 테스트가 깨져서
    바로 알아챌 수 있다.)
"""
import math

import pytest

from synthetic_flight_path import SyntheticFlightPath, bearing_to
from fusion.geofence import distance_km
from config import PROTECTED_ZONES, POLL_INTERVAL_SECONDS

# 검증할 속도 목록 (m/s)
SPEED_SCENARIOS = {
    "여객기(220m/s)": 220,
    "아음속 전투기(250m/s)": 250,
    "초음속 전투기(400m/s)": 400,
    "초음속 전투기(600m/s)": 600,
}

# 검증할 폴링 간격 목록 (초)
POLL_INTERVALS = [300, 120, 60, 30, 10]

APPROACH_DISTANCE_KM = 60  # 구역 중심 기준 전후 60km 구간을 비행


def run_scenario(zone: dict, speed_ms: float, poll_interval: float,
                  offset_km: float = 0.0) -> dict:
    """
    zone 중심에서 offset_km만큼 떨어진 지점을 스치는 직선 경로를 speed_ms로 비행시키고,
    poll_interval마다 관측했을 때 구역 반경 내에서 몇 번이나 잡히는지 확인.

    offset_km=0: 정중앙 관통 (가장 관대한 조건, 구역 내 체류시간이 가장 김)
    offset_km=zone['radius_km']에 가까울수록: 가장자리만 살짝 스침 (체류시간 매우 짧음, 가장 놓치기 쉬운 조건)
    """
    # 구역에서 남쪽 APPROACH_DISTANCE_KM 지점을, 동쪽으로 offset_km만큼 밀어서 출발점으로 삼는다.
    # (완벽한 지리 정밀도보다 '중심에서 얼마나 벗어나 스치는가' 개념 검증이 목적)
    lat_offset = APPROACH_DISTANCE_KM / 111.0
    lon_offset = offset_km / (111.0 * math.cos(math.radians(zone["lat"])))
    start_lat = zone["lat"] - lat_offset
    start_lon = zone["lon"] + lon_offset

    # 목표점도 반대편(북쪽)에서 같은 offset만큼 벗어난 지점으로 - 직선이 zone을 offset_km 거리로 스치도록
    end_lat = zone["lat"] + lat_offset
    end_lon = zone["lon"] + lon_offset

    heading = bearing_to(start_lat, start_lon, end_lat, end_lon)
    path = SyntheticFlightPath(start_lat=start_lat, start_lon=start_lon,
                                heading_deg=heading, speed_ms=speed_ms)

    total_distance_km = APPROACH_DISTANCE_KM * 2
    total_duration_s = (total_distance_km * 1000) / speed_ms

    t = 0.0
    samples = 0
    hits = 0
    min_distance_sampled = float("inf")
    while t <= total_duration_s:
        lat, lon = path.position_at(t)
        d = distance_km(lat, lon, zone["lat"], zone["lon"])
        min_distance_sampled = min(min_distance_sampled, d)
        samples += 1
        if d <= zone["radius_km"]:
            hits += 1
        t += poll_interval

    # 진짜 최근접 거리(ground truth): 폴링과 무관하게 1초 간격으로 촘촘히 계산.
    # '우리가 관측한 것'과 '실제로 일어난 것'의 차이를 보여주기 위한 진단용 값.
    true_min_distance = float("inf")
    tt = 0.0
    while tt <= total_duration_s:
        lat, lon = path.position_at(tt)
        d = distance_km(lat, lon, zone["lat"], zone["lon"])
        true_min_distance = min(true_min_distance, d)
        tt += 1.0

    return {
        "detected": hits > 0,
        "samples": samples,
        "hits": hits,
        "min_distance_sampled_km": round(min_distance_sampled, 1),
        "true_min_distance_km": round(true_min_distance, 1),
        "true_entered_zone": true_min_distance <= zone["radius_km"],
        "total_duration_min": round(total_duration_s / 60, 1),
    }


def _print_exploration_tables():
    """탐색용: 속도x간격x오프셋 조합 전체를 표로 눈으로 확인 (pytest 대상 아님)"""
    zone = PROTECTED_ZONES[0]  # 인천국제공항 관제권
    radius = zone["radius_km"]
    print(f"검증 대상 보호구역: {zone['name']} (반경 {radius}km)\n")

    # offset 시나리오: 0=정중앙 관통, radius의 50%=절반 지점 스침, radius의 90%=가장자리 아슬아슬
    offset_scenarios = {
        "중앙 관통(offset 0km)": 0.0,
        f"중간 스침(offset {radius*0.5:.0f}km)": radius * 0.5,
        f"가장자리 스침(offset {radius*0.9:.0f}km)": radius * 0.9,
    }

    for offset_label, offset_km in offset_scenarios.items():
        print(f"\n=== {offset_label} ===")
        header = f"{'속도':<22}" + "".join(f"{iv}s".rjust(10) for iv in POLL_INTERVALS)
        print(header)
        print("-" * len(header))
        for label, speed in SPEED_SCENARIOS.items():
            row = f"{label:<22}"
            for interval in POLL_INTERVALS:
                result = run_scenario(zone, speed, interval, offset_km=offset_km)
                mark = "탐지" if result["detected"] else "놓침"
                row += mark.rjust(10)
            print(row)

    # 가장 극단적 조건 상세 출력: 가장자리 스침 + 초음속 + 5분 간격
    print("\n--- 상세 예시: 가장자리 스침(offset 18km), 600m/s, 폴링 300초 ---")
    detail = run_scenario(zone, 600, 300, offset_km=radius * 0.9)
    print(f"실제 최근접 거리(ground truth, 1초 단위 정밀계산): {detail['true_min_distance_km']}km "
          f"→ 실제로 구역(반경 {radius}km) 진입 여부: {detail['true_entered_zone']}")
    print(f"우리 시스템이 '관측한' 시점들 중 최소 거리: {detail['min_distance_sampled_km']}km "
          f"(관측 시도 {detail['samples']}회, 그중 구역 내 포착 {detail['hits']}회)")
    print(f"→ 탐지 성공 여부: {detail['detected']}")
    if detail["true_entered_zone"] and not detail["detected"]:
        print("⚠ 실제로는 구역에 진입했지만, 폴링 시점이 안 맞아 시스템은 전혀 눈치채지 못함")


# ============ 회귀 테스트 (Phase 3에서 실제로 내린 설계 결론을 못박음) ============

def test_current_poll_interval_catches_worst_case():
    """
    현재 설정(POLL_INTERVAL_SECONDS=30)이면, 가장자리를 스치는 초음속(600m/s) 위협도
    탐지된다는 게 Phase 3의 결론이었다. 이 값이 나중에 다시 늘어나면 이 테스트가 깨진다.
    """
    zone = PROTECTED_ZONES[0]
    result = run_scenario(zone, speed_ms=600, poll_interval=POLL_INTERVAL_SECONDS,
                           offset_km=zone["radius_km"] * 0.9)
    assert result["true_entered_zone"], "테스트 시나리오 자체가 실제로 구역에 진입 안 함 - 시나리오 설계 오류"
    assert result["detected"], (
        f"현재 폴링 간격({POLL_INTERVAL_SECONDS}초)으로는 가장자리 스침+초음속 위협을 놓칩니다! "
        f"Phase 3에서 이 값을 30초로 정한 이유가 무너졌으니 config.py를 재검토해야 합니다."
    )


def test_slow_poll_interval_misses_edge_scrape():
    """
    반대로, 예전에 쓰던 느린 간격(300초=5분)으로는 같은 시나리오를 실제로 놓친다는 걸
    보여준다 - "왜 30초로 좁혔는지"의 근거를 재현 가능한 테스트로 남겨둠.
    """
    zone = PROTECTED_ZONES[0]
    result = run_scenario(zone, speed_ms=600, poll_interval=300,
                           offset_km=zone["radius_km"] * 0.9)
    assert result["true_entered_zone"], "테스트 시나리오 자체가 실제로 구역에 진입 안 함 - 시나리오 설계 오류"
    assert not result["detected"], "5분 간격이면 놓쳐야 하는 시나리오인데 탐지됐습니다 - 예상과 다름, 재검토 필요"


@pytest.mark.parametrize("speed_label,speed_ms", list(SPEED_SCENARIOS.items()))
def test_center_pass_always_detected_at_current_interval(speed_label, speed_ms):
    """정중앙 관통(가장 관대한 조건)은 어떤 속도라도 현재 폴링 간격에서 항상 탐지돼야 함"""
    zone = PROTECTED_ZONES[0]
    result = run_scenario(zone, speed_ms=speed_ms, poll_interval=POLL_INTERVAL_SECONDS, offset_km=0.0)
    assert result["detected"], f"{speed_label}이 정중앙 관통인데도 놓쳤습니다 - 심각한 회귀"


def main():
    _print_exploration_tables()


if __name__ == "__main__":
    main()
