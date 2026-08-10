"""
합성 비행 경로 시뮬레이터 (검증 전용 도구)

목적: "5분 간격 폴링으로 고속 침투체를 놓칠 수 있다"는 가설을 실제로
검증하기 위해, '진짜 위치(ground truth)'를 우리가 직접 아는 가상 항적을
만든다. 실제 API 데이터는 우리가 진짜 위치를 모르기 때문에 이 검증을
할 수 없다 - 이게 이 시뮬레이터가 API 대신 존재하는 이유다.

이 모듈은 프로덕션 파이프라인(main.py, continuous_monitor.py)에 연결되지
않는다. 순수하게 "우리 탐지 로직이 실제로 놓치는지"를 확인하는 테스트 도구.
"""
import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0


@dataclass
class SyntheticFlightPath:
    """
    출발점 + 방위각(heading) + 속도로 정의되는 직선 이동 가상 항적.
    position_at(t)로 t초 후의 정확한 위치를 계산한다 (우리가 '진짜 정답'을 아는 상태).
    """
    start_lat: float
    start_lon: float
    heading_deg: float   # 이동 방향 (0=북쪽, 90=동쪽)
    speed_ms: float       # 속도 (m/s)

    def position_at(self, elapsed_seconds: float) -> tuple[float, float]:
        """
        elapsed_seconds초 후의 (위도, 경도)를 계산.
        '출발점에서 특정 방위각·거리만큼 떨어진 지점'을 구하는 표준 공식(직접 측지 문제) 사용.
        """
        distance_km = (self.speed_ms * elapsed_seconds) / 1000.0
        angular_distance = distance_km / EARTH_RADIUS_KM

        lat1 = math.radians(self.start_lat)
        lon1 = math.radians(self.start_lon)
        brng = math.radians(self.heading_deg)

        lat2 = math.asin(
            math.sin(lat1) * math.cos(angular_distance)
            + math.cos(lat1) * math.sin(angular_distance) * math.cos(brng)
        )
        lon2 = lon1 + math.atan2(
            math.sin(brng) * math.sin(angular_distance) * math.cos(lat1),
            math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
        )
        return math.degrees(lat2), math.degrees(lon2)


def bearing_to(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """lat1,lon1에서 lat2,lon2를 향하는 방위각(0~360). 시뮬레이션 시나리오를
    '보호구역을 정확히 향해 날아오는 경로'로 설정할 때 사용."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


if __name__ == "__main__":
    # 간단 동작 확인: 북쪽으로 250m/s(아음속 전투기급)로 60초 이동
    path = SyntheticFlightPath(start_lat=36.0, start_lon=127.0, heading_deg=0, speed_ms=250)
    for t in [0, 30, 60]:
        lat, lon = path.position_at(t)
        print(f"t={t}s: ({lat:.4f}, {lon:.4f})")
