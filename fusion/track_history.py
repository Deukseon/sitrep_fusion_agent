"""
트랙 이력 저장소 (Phase 3)

지금까지 continuous_monitor.py는 매 사이클마다 완전히 새로운 thread_id로 시작해서,
같은 항공기를 여러 번의 폴링에 걸쳐 추적하지 못했다. 이 모듈은 그 문제를 해결한다:

  - track_id(icao24)별로 (시각, 위경도) 관측 이력을 파일(JSON)에 누적 저장한다.
    파일 기반이라 continuous_monitor.py 프로세스를 껐다 켜도 이력이 유지된다.
  - 최근 두 관측점의 실제 이동 거리 ÷ 걸린 시간으로 속도를, 두 점을 잇는 방위각으로
    방향을 계산한다. API가 주는 순간 속도값(reported_speed_ms)과 달리, 이건 실제로
    "움직인 궤적"에서 역산한 값이라 더 신뢰할 수 있다.
  - 오래 안 보인 트랙(기본 1시간)은 이력에서 자동 정리해서 파일이 무한히 커지지
    않게 한다.

파일 형식 (JSON):
{
  "<icao24>": [
    {"timestamp": 1723344000.0, "lat": 37.46, "lon": 126.44,
     "altitude_m": 9500.0, "reported_speed_ms": 230.0, "reported_heading_deg": 90.0},
    ...
  ],
  ...
}
"""
import json
import os
import sys
import time
import logging
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fusion.geofence import distance_km, bearing_deg

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "track_history.json")

MAX_POINTS_PER_TRACK = 20      # 트랙당 최대 보관 이력 개수 (오래된 것부터 제거)
STALE_SECONDS = 3600           # 이 시간 이상 재관측 안 되면 이력에서 제거 (1시간)


def load_history(path: str = DEFAULT_HISTORY_PATH) -> dict:
    """이력 파일을 읽어온다. 파일이 없거나 손상됐으면 빈 이력으로 시작한다."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("이력 파일 로드 실패, 빈 이력으로 새로 시작: %s", e)
        return {}


def save_history(history: dict, path: str = DEFAULT_HISTORY_PATH) -> None:
    """이력을 파일에 저장한다. 실패해도 파이프라인이 죽지 않도록 예외를 삼킨다."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error("이력 파일 저장 실패: %s", e)


def _prune_stale_tracks(history: dict, now: float) -> dict:
    """마지막 관측 이후 STALE_SECONDS 이상 지난 트랙은 이력에서 제거."""
    pruned = {
        track_id: points
        for track_id, points in history.items()
        if points and (now - points[-1]["timestamp"]) <= STALE_SECONDS
    }
    removed = len(history) - len(pruned)
    if removed:
        logger.info("오래된(1시간 이상 미관측) 트랙 이력 %d건 정리", removed)
    return pruned


def update_history(history: dict, tracks: list, timestamp: Optional[float] = None) -> dict:
    """
    이번 사이클에서 관측된 트랙들을 이력에 추가하고, 오래된 트랙은 정리한 새 이력을 반환.

    Args:
        history: load_history()로 불러온 기존 이력
        tracks: raw_tracks 형태의 dict 리스트 (track_id, lat, lon 필수)
        timestamp: 관측 시각(epoch). 생략하면 현재 시각 사용 (테스트 시 명시적으로 넘기면 유용)
    """
    now = timestamp if timestamp is not None else time.time()

    for t in tracks:
        if t.get("lat") is None or t.get("lon") is None:
            continue  # 위치 결측치는 이력에 넣지 않는다 (궤적 계산이 왜곡되는 걸 방지)

        track_id = t["track_id"]
        point = {
            "timestamp": now,
            "lat": t["lat"],
            "lon": t["lon"],
            "altitude_m": t.get("altitude_m"),
            "reported_speed_ms": t.get("speed_ms"),
            "reported_heading_deg": t.get("heading_deg"),
        }
        history.setdefault(track_id, []).append(point)
        if len(history[track_id]) > MAX_POINTS_PER_TRACK:
            history[track_id] = history[track_id][-MAX_POINTS_PER_TRACK:]

    return _prune_stale_tracks(history, now)


def compute_velocity_from_history(points: list) -> dict:
    """
    최근 두 관측점으로 실제 이동 벡터(속도·방향)를 계산한다.

    점이 2개 미만이면(이번이 첫 관측이면) 계산 불가 -> None 반환.
    이 경우 호출하는 쪽에서 API가 준 순간 속도값(reported_speed_ms)으로 폴백하면 된다.
    """
    if len(points) < 2:
        return {"computed_speed_ms": None, "computed_heading_deg": None, "history_points": len(points)}

    p1, p2 = points[-2], points[-1]
    dt = p2["timestamp"] - p1["timestamp"]
    if dt <= 0:
        return {"computed_speed_ms": None, "computed_heading_deg": None, "history_points": len(points)}

    dist_km = distance_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
    speed_ms = (dist_km * 1000.0) / dt
    heading = bearing_deg(p1["lat"], p1["lon"], p2["lat"], p2["lon"])

    return {
        "computed_speed_ms": round(speed_ms, 1),
        "computed_heading_deg": round(heading, 1),
        "history_points": len(points),
    }


def get_track_history(history: dict, track_id: str) -> list:
    """특정 트랙의 이력만 조회 (없으면 빈 리스트)."""
    return history.get(track_id, [])


if __name__ == "__main__":
    # 간단한 자체 테스트: 가상의 트랙이 3번 관측됐다고 가정하고 속도/방향 계산 검증
    logging.basicConfig(level=logging.INFO, format="%(name)s - %(message)s")

    history: dict = {}
    base_t = 1000.0

    # 서울(37.5665, 126.9780)에서 정동쪽으로 약 0.01도씩(대략 900m) 이동, 30초 간격
    cycles = [
        (base_t + 0, [{"track_id": "TEST01", "lat": 37.5665, "lon": 126.9780, "speed_ms": 200.0, "heading_deg": 90.0}]),
        (base_t + 30, [{"track_id": "TEST01", "lat": 37.5665, "lon": 126.9880, "speed_ms": 200.0, "heading_deg": 90.0}]),
        (base_t + 60, [{"track_id": "TEST01", "lat": 37.5665, "lon": 126.9980, "speed_ms": 200.0, "heading_deg": 90.0}]),
    ]

    for ts, tracks in cycles:
        history = update_history(history, tracks, timestamp=ts)
        pts = get_track_history(history, "TEST01")
        result = compute_velocity_from_history(pts)
        print(f"t={ts}: 이력 {result['history_points']}개, 계산 속도={result['computed_speed_ms']}m/s, "
              f"계산 방향={result['computed_heading_deg']}도")

    # 위도 37.57도에서는 경도 1도가 111km가 아니라 111*cos(37.57도)≈88km이다
    # (지구가 구형이라 고위도로 갈수록 경도선 간격이 좁아짐 - 위선 vs 경선 차이).
    # 0.01도 * 88km/도 * 1000 / 30s ≈ 29.4 m/s -> 계산된 값과 일치해야 함
    print("\n예상: 방향은 90도(정동) 근처, 속도는 약 29.4 m/s 근처여야 함")
