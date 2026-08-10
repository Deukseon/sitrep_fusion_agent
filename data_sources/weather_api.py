"""
실시간 기상 데이터 소스 (Open-Meteo API)

- 인증/API키 불필요, 완전 무료
- 문서: https://open-meteo.com/en/docs
- 작전 환경 판단(가시거리, 풍속에 따른 센서 신뢰도 가중치 등)에 사용
"""
import logging
import requests
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class WeatherSnapshot:
    latitude: float
    longitude: float
    temperature_c: Optional[float]
    windspeed_ms: Optional[float]
    winddirection_deg: Optional[float]
    weathercode: Optional[int]
    visibility_ok: bool  # 가시거리 기반 센서 신뢰도 판단용 플래그


def _weathercode_to_visibility_ok(code: Optional[int]) -> bool:
    """
    WMO 날씨 코드 기준 간이 판정.
    45,48(안개), 51~67(강수), 71~86(강설/우박) 등은 시각/열화상 센서 신뢰도 저하로 간주.
    """
    if code is None:
        return True
    bad_codes = set([45, 48] + list(range(51, 68)) + list(range(71, 87)) + [95, 96, 99])
    return code not in bad_codes


def fetch_weather(lat: float, lon: float, timeout: float = 5.0) -> Optional[WeatherSnapshot]:
    """지정 좌표의 현재 기상 상태를 조회한다."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": "true",
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("API 호출 실패: %s", e)
        return None

    cw = data.get("current_weather", {})
    code = cw.get("weathercode")
    return WeatherSnapshot(
        latitude=lat,
        longitude=lon,
        temperature_c=cw.get("temperature"),
        windspeed_ms=cw.get("windspeed"),
        winddirection_deg=cw.get("winddirection"),
        weathercode=code,
        visibility_ok=_weathercode_to_visibility_ok(code),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s - %(message)s")
    # 테스트: 서울 좌표
    w = fetch_weather(lat=37.5665, lon=126.9780)
    print(w)
