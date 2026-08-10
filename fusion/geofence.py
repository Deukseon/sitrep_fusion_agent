"""
보호구역(geofence) 기반 위협 판단

트랙의 현재 위치가 보호구역(공항, 발전소 등)에 얼마나 가까운지,
그리고 그 방향으로 접근 중인지를 계산한다.

거리만 볼 게 아니라 '접근 중인가'까지 봐야 하는 이유:
같은 거리라도 보호구역 쪽으로 다가오는 항적과, 스쳐 지나가거나 멀어지는
항적은 위협도가 다르다. 이건 아직 궤적 이력(Phase 3)이 없는 상태에서
'현재 위치 + 방위각(heading)'만으로 근사한 것이라 완벽하지 않다 -
진짜 접근 여부는 여러 시점의 위치를 비교해야 정확히 알 수 있다.
"""
import math
from dataclasses import dataclass
from typing import Optional

EARTH_RADIUS_KM = 6371.0


@dataclass
class GeofenceResult:
    zone_name: Optional[str]
    distance_km: Optional[float]
    inside_zone: bool
    approaching: bool


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 간 대권거리(km) 계산"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """_haversine_km의 공개 버전. 다른 모듈(예: 시뮬레이터)에서 거리 계산이 필요할 때 재사용."""
    return _haversine_km(lat1, lon1, lat2, lon2)


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """lat1,lon1 지점에서 lat2,lon2 지점을 바라보는 방위각(0~360, 북쪽=0)"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def check_nearest_zone(lat: float, lon: float, heading_deg: Optional[float],
                        zones: list[dict]) -> GeofenceResult:
    """
    주어진 위치에서 가장 가까운 보호구역까지의 거리와, 그쪽으로 접근 중인지 판단.

    접근 판정: 트랙의 실제 이동방향(heading)과, 트랙에서 보호구역을 바라보는
    방위각의 차이가 60도 이내면 '그 방향으로 가고 있다'고 근사 판정한다.
    heading 정보가 없으면 접근 여부는 판단 불가(False) 처리.
    """
    if not zones:
        return GeofenceResult(zone_name=None, distance_km=None, inside_zone=False, approaching=False)

    nearest = min(zones, key=lambda z: _haversine_km(lat, lon, z["lat"], z["lon"]))
    distance = _haversine_km(lat, lon, nearest["lat"], nearest["lon"])
    inside = distance <= nearest["radius_km"]

    approaching = False
    if heading_deg is not None:
        bearing_to_zone = _bearing_deg(lat, lon, nearest["lat"], nearest["lon"])
        diff = abs((heading_deg - bearing_to_zone + 180) % 360 - 180)
        approaching = diff <= 60

    return GeofenceResult(
        zone_name=nearest["name"],
        distance_km=round(distance, 1),
        inside_zone=inside,
        approaching=approaching,
    )
