"""
Phase 3 TrackHistory 통합 테스트 (네트워크 API 없이 목데이터로 검증)

검증 항목:
  1. 같은 트랙이 여러 사이클에 걸쳐 이력에 누적되는가
  2. 2회 이상 관측되면 이력 기반 속도/방향이 계산되는가 (API 원본과 비슷한 범위인가)
  3. 파일로 저장 후 다시 로드해도 이력이 유지되는가 (영속성)
  4. STALE_SECONDS를 지난 트랙은 정리되는가
"""
import os
import sys
import time
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from data_sources.flight_tracker import AirTrack
import fusion.track_history as th

TEST_HISTORY_PATH = "/tmp/test_track_history.json"


def _mock_tracks_for_cycle(step: int) -> list:
    """서울 인근에서 정동쪽으로 계속 이동하는 트랙 하나를 사이클마다 반환"""
    return [
        AirTrack(
            icao24="TESTTRACK",
            callsign="MOCK01",
            longitude=126.9780 + step * 0.01,
            latitude=37.5665,
            altitude_m=9500,
            velocity_ms=200.0,
            heading_deg=90.0,
            vertical_rate_ms=0,
        )
    ]


def test_accumulation_and_recompute():
    print("=== 테스트 1~2: 이력 누적 + 속도/방향 재계산 ===")
    if os.path.exists(TEST_HISTORY_PATH):
        os.remove(TEST_HISTORY_PATH)

    with patch("agent.graph.fetch_weather", return_value=None):
        import continuous_monitor as cm

        history = {}
        base_t = time.time()
        for step in range(3):
            with patch("agent.graph.fetch_tracks", return_value=_mock_tracks_for_cycle(step)):
                # run_one_cycle 내부에서 time.time()을 쓰므로, 사이클 간 30초 간격을
                # 흉내내기 위해 update_history를 직접 호출하는 대신 run_one_cycle을 쓰되
                # timestamp는 time.time() 몽키패치로 통제한다.
                with patch("time.time", return_value=base_t + step * 30):
                    history = cm.run_one_cycle(cycle_num=step, history=history)

        assert "TESTTRACK" in history, "트랙이 이력에 기록되지 않았습니다!"
        points = th.get_track_history(history, "TESTTRACK")
        assert len(points) == 3, f"3번 관측했는데 이력이 {len(points)}개입니다!"
        print(f"✅ 3사이클 모두 이력에 누적됨 (points={len(points)})")

        vel = th.compute_velocity_from_history(points)
        assert vel["computed_speed_ms"] is not None, "2회 이상 관측했는데 속도 계산이 안 됐습니다!"
        # 위도 37.5665도에서 경도 0.01도 ≈ 880m, 30초 간격 -> 약 29.4 m/s
        assert 25 <= vel["computed_speed_ms"] <= 35, f"계산 속도가 예상 범위를 벗어남: {vel['computed_speed_ms']}"
        assert 80 <= vel["computed_heading_deg"] <= 100, f"계산 방향이 정동(90도)에서 너무 벗어남: {vel['computed_heading_deg']}"
        print(f"✅ 이력 기반 재계산: 속도={vel['computed_speed_ms']}m/s, 방향={vel['computed_heading_deg']}도 (정동 근처, 정상 범위)")

    return history


def test_persistence(history: dict):
    print("\n=== 테스트 3: 파일 저장/로드 영속성 ===")
    th.save_history(history, path=TEST_HISTORY_PATH)
    reloaded = th.load_history(path=TEST_HISTORY_PATH)
    assert "TESTTRACK" in reloaded, "저장 후 다시 불러왔는데 트랙이 사라졌습니다!"
    assert len(reloaded["TESTTRACK"]) == 3, "저장/로드 후 이력 개수가 달라졌습니다!"
    print("✅ 파일 저장 -> 로드 후 이력 3개 그대로 유지됨")
    os.remove(TEST_HISTORY_PATH)


def test_stale_pruning():
    print("\n=== 테스트 4: 오래된 트랙 자동 정리 (STALE_SECONDS) ===")
    history = {}
    now = time.time()
    old_tracks = [{"track_id": "OLDTRACK", "lat": 36.0, "lon": 127.0, "speed_ms": 100, "heading_deg": 0}]
    history = th.update_history(history, old_tracks, timestamp=now - th.STALE_SECONDS - 10)

    fresh_tracks = [{"track_id": "FRESHTRACK", "lat": 36.0, "lon": 127.0, "speed_ms": 100, "heading_deg": 0}]
    history = th.update_history(history, fresh_tracks, timestamp=now)

    assert "OLDTRACK" not in history, "1시간 넘게 안 보인 트랙이 정리되지 않았습니다!"
    assert "FRESHTRACK" in history, "방금 관측된 트랙이 잘못 정리됐습니다!"
    print("✅ STALE_SECONDS(1시간) 초과 트랙만 정확히 정리됨")


if __name__ == "__main__":
    history = test_accumulation_and_recompute()
    test_persistence(history)
    test_stale_pruning()
    print("\n🎉 Phase 3 TrackHistory 통합 테스트 전체 통과")
