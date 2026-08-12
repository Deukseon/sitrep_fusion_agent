"""
무작위 시나리오 테스트 생성기 — 실제 OpenSky API를 호출하지 않고
위협 스코어링/지도 시각화 로직만 빠르게 검증하고 싶을 때 사용.

API 크레딧을 전혀 소모하지 않으므로, 로직을 반복적으로 테스트하거나
지도에 CRITICAL/HIGH(빨강/주황) 마커가 실제로 어떻게 찍히는지 보고 싶을 때 유용.

사용법:
  지도까지 눈으로 확인: python test_random_scenarios.py
  회귀 테스트만 실행: pytest test_random_scenarios.py -v
"""
import random
import logging
import os

import pytest

from data_sources.synthetic_sensors import enrich_track_with_synthetic_sensors
from fusion.threat_scoring import score_track, rank_tracks
from visualize_map import build_map
from config import MONITOR_BBOX

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# 한반도 근해 bbox 범위 안에서 무작위 좌표 생성 (config의 MONITOR_BBOX와 동일 범위 사용)
LAT_RANGE = (MONITOR_BBOX[0], MONITOR_BBOX[2])
LON_RANGE = (MONITOR_BBOX[1], MONITOR_BBOX[3])

N_TRACKS = 30


def generate_random_track(i: int, rng: random.Random = random) -> dict:
    """
    무작위 항적 1건 생성.
    의도적으로 '위협처럼 보이는' 조합(저고도+고속+미확인)이 일정 확률로 섞이도록
    분포를 조정해서, 실행할 때마다 MEDIUM/HIGH/CRITICAL이 골고루 나오게 한다.

    rng: 기본은 전역 random 모듈(매번 다른 결과, 눈으로 탐색할 때 용도).
         테스트에서는 random.Random(seed)를 넘겨서 재현 가능하게 만든다.
    """
    is_suspicious = rng.random() < 0.35  # 35% 확률로 '수상한' 항적으로 생성

    if is_suspicious:
        altitude = rng.uniform(200, 1400)      # 저고도
        speed = rng.uniform(150, 300)           # 고속
        label = None if rng.random() < 0.6 else f"UNK{i:03d}"
    else:
        altitude = rng.uniform(3000, 12000)     # 일반 여객기 고도
        speed = rng.uniform(180, 260)
        label = f"KAL{100 + i}"

    return {
        "track_id": f"sim{i:04d}",
        "label": label,
        "lat": rng.uniform(*LAT_RANGE),
        "lon": rng.uniform(*LON_RANGE),
        "altitude_m": round(altitude),
        "speed_ms": round(speed),
        "heading_deg": rng.uniform(0, 360),
        "climb_rate_ms": rng.uniform(-5, 5),
        "source": "SIMULATED",
    }


def _run_pipeline(rng: random.Random = random) -> tuple[list, list]:
    """raw_tracks와 최종 assessments를 반환 (탐색용 main()과 회귀 테스트가 공유하는 핵심 로직)"""
    raw_tracks = [generate_random_track(i, rng) for i in range(N_TRACKS)]
    enriched = [enrich_track_with_synthetic_sensors(t) for t in raw_tracks]
    assessments = rank_tracks([score_track(t, visibility_ok=True) for t in enriched])
    return raw_tracks, assessments


# ============ 회귀 테스트 (재현 가능한 시드로 파이프라인 정합성만 확인) ============

def test_pipeline_runs_without_error_and_produces_valid_levels():
    """30건 무작위 생성 -> 융합 -> 스코어링까지 예외 없이 돌고, 등급이 전부 유효한 값인지 확인"""
    rng = random.Random(42)  # 고정 시드 - 실행할 때마다 같은 결과 (재현 가능성)
    raw_tracks, assessments = _run_pipeline(rng)

    assert len(raw_tracks) == N_TRACKS
    assert len(assessments) == N_TRACKS, "일부 트랙이 스코어링 중 유실됐습니다"

    valid_levels = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    for a in assessments:
        assert a.level in valid_levels, f"알 수 없는 등급: {a.level}"
        assert 0.0 <= a.score <= 100.0, f"점수가 0~100 범위를 벗어남: {a.score}"


def test_map_generation_succeeds(tmp_path):
    """생성된 결과로 지도 HTML까지 실제로 만들어지는지 확인 (build_map이 예외 없이 도는지)"""
    rng = random.Random(7)
    raw_tracks, assessments = _run_pipeline(rng)
    result = {"raw_tracks": raw_tracks, "assessments": [a.to_dict() for a in assessments]}

    m = build_map(result)
    out_path = tmp_path / "random_test_map.html"
    m.save(str(out_path))

    assert out_path.exists(), "지도 HTML 파일이 생성되지 않았습니다"
    assert out_path.stat().st_size > 1000, "지도 HTML 파일이 비정상적으로 작습니다"


def main():
    raw_tracks, assessments = _run_pipeline(random)

    # 등급별 분포 집계
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for a in assessments:
        counts[a.level] += 1

    logger.info("총 %d건 생성 완료", N_TRACKS)
    logger.info("등급 분포: CRITICAL %d / HIGH %d / MEDIUM %d / LOW %d",
                counts['CRITICAL'], counts['HIGH'], counts['MEDIUM'], counts['LOW'])
    print()
    for a in assessments[:10]:
        print(f"  [{a.level:8}] {a.track_id} ({a.label}) - {a.score}점")

    # visualize_map.build_map()은 raw_tracks + assessments(dict 리스트)를 받는 형태라 맞춰준다
    result = {
        "raw_tracks": raw_tracks,
        "assessments": [a.to_dict() for a in assessments],
    }
    m = build_map(result)
    m.save("random_test_map.html")
    print("\n지도 저장 완료: random_test_map.html")


if __name__ == "__main__":
    main()
