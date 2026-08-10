"""
실시간 항공기 위치 데이터 소스 (OpenSky Network API)

- 익명 사용자: 하루 400 크레딧 / 등록 사용자: 하루 4000 크레딧
  (config.py의 OPENSKY_CLIENT_ID/SECRET이 설정되어 있으면 자동으로 등록 사용자 인증을 사용한다)
- 레이더가 포착한 '미확인 항적(unknown track)'을 시뮬레이션하는 용도로 사용
- 실제 문서: https://openskynetwork.github.io/opensky-api/rest.html
"""
import time
import sys
import os
import logging
import requests
from dataclasses import dataclass
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET

logger = logging.getLogger(__name__)

OPEN_SKY_URL = "https://opensky-network.org/api/states/all"
OPEN_SKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)

# 발급받은 액세스 토큰을 모듈 안에 캐시해서, 매 호출마다 새로 로그인하지 않게 한다.
# (토큰은 30분간 유효 - 문서 기준)
_token_cache = {"access_token": None, "expires_at": 0.0}


@dataclass
class AirTrack:
    """레이더 트랙처럼 다룰 항공기 상태 하나"""
    icao24: str            # 항공기 고유 식별자 (트랙 ID처럼 사용)
    callsign: Optional[str]
    longitude: Optional[float]
    latitude: Optional[float]
    altitude_m: Optional[float]   # 고도 (미터)
    velocity_ms: Optional[float]  # 속도 (m/s)
    heading_deg: Optional[float]  # 방위각
    vertical_rate_ms: Optional[float]

    def to_radar_like_dict(self) -> dict:
        """레이더 트랙 포맷처럼 변환 (다른 센서 데이터와 스키마 통일용)"""
        return {
            "track_id": self.icao24,
            "label": self.callsign or "UNKNOWN",
            "lat": self.latitude,
            "lon": self.longitude,
            "altitude_m": self.altitude_m,
            "speed_ms": self.velocity_ms,
            "heading_deg": self.heading_deg,
            "climb_rate_ms": self.vertical_rate_ms,
            "source": "ADS-B(OpenSky)",
        }


def _get_access_token(timeout: float = 5.0) -> Optional[str]:
    """
    등록된 OpenSky 계정(Client ID/Secret)이 config에 설정되어 있으면
    OAuth2 client credentials flow로 액세스 토큰을 발급받는다.
    설정이 없으면 None을 반환하고, 이 경우 익명 요청으로 자동 전환된다.
    """
    if not OPENSKY_CLIENT_ID or not OPENSKY_CLIENT_SECRET:
        return None

    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    try:
        resp = requests.post(
            OPEN_SKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": OPENSKY_CLIENT_ID,
                "client_secret": OPENSKY_CLIENT_SECRET,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        token_data = resp.json()
        _token_cache["access_token"] = token_data["access_token"]
        # 만료 30초 전에 미리 갱신하도록 여유를 둔다
        _token_cache["expires_at"] = now + token_data.get("expires_in", 1800) - 30
        logger.info("OpenSky 등록 계정 인증 성공 (하루 4000 크레딧 적용)")
        return _token_cache["access_token"]
    except requests.RequestException as e:
        logger.warning("OpenSky 인증 실패, 익명 요청으로 전환: %s", e)
        return None


def fetch_tracks(bbox: Optional[tuple] = None, timeout: float = 5.0) -> list[AirTrack]:
    """
    실시간 항적 조회

    Args:
        bbox: (lamin, lomin, lamax, lomax) - 관심 구역 좌표 박스.
              예: 한반도 근해 = (33.0, 124.0, 39.0, 132.0)
        timeout: 요청 타임아웃(초)

    Returns:
        AirTrack 리스트. API 실패 시 빈 리스트 반환(에이전트가 죽지 않도록).
    """
    params = {}
    if bbox:
        lamin, lomin, lamax, lomax = bbox
        params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}

    headers = {}
    token = _get_access_token(timeout=timeout)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(OPEN_SKY_URL, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            retry_after = resp.headers.get("X-Rate-Limit-Retry-After-Seconds", "알 수 없음")
            logger.warning("API 요청 한도 초과(429). %s초 후 재시도 권장. 빈 리스트 반환.", retry_after)
            return []
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("API 호출 실패, 빈 리스트 반환: %s", e)
        return []

    states = data.get("states") or []
    tracks = []
    for s in states:
        # OpenSky 응답은 고정된 인덱스 순서의 배열이다
        tracks.append(AirTrack(
            icao24=s[0],
            callsign=(s[1] or "").strip() or None,
            longitude=s[5],
            latitude=s[6],
            altitude_m=s[7],
            velocity_ms=s[9],
            heading_deg=s[10],
            vertical_rate_ms=s[11],
        ))
    return tracks


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s - %(message)s")
    # 테스트: 한반도 근해 구역
    korea_bbox = (33.0, 124.0, 39.0, 132.0)
    tracks = fetch_tracks(bbox=korea_bbox)
    print(f"수신된 항적 수: {len(tracks)}")
    for t in tracks[:5]:
        print(t.to_radar_like_dict())
