"""
합성 군사 센서 데이터 생성기

실제 레이더/열화상/SIGINT 공개 데이터가 없으므로,
OpenSky에서 받은 실제 항적(AirTrack)을 '씨앗(seed)'으로 삼아
그럴듯한 부가 센서 신호를 확률적으로 합성한다.

포인트: 완전 무작위가 아니라 실제 트랙의 고도/속도에 상관된 값을 만들어서
'다중 센서가 같은 물체를 다르게 관측한다'는 상황을 재현한다.
"""
import random
from dataclasses import dataclass
from typing import Optional


@dataclass
class RadarReturn:
    track_id: str
    rcs_dbsm: float          # 레이더 단면적(추정) - 값이 클수록 큰 물체
    detection_confidence: float  # 0~1


@dataclass
class ThermalSignature:
    track_id: str
    heat_intensity: float    # 0~1, 엔진열 등 추정치
    is_hot: bool


@dataclass
class SigintEmission:
    track_id: str
    emission_detected: bool
    frequency_band: Optional[str]   # 예: "X-band", "S-band", None(무신호)
    signal_strength_db: Optional[float]


def generate_radar_return(track_id: str, altitude_m: Optional[float], seed_offset: int = 0) -> RadarReturn:
    rng = random.Random(hash(track_id) + seed_offset)
    # 고도가 낮을수록 레이더 클러터(잡음) 영향으로 신뢰도가 떨어진다고 가정
    base_conf = 0.9 if (altitude_m or 0) > 3000 else 0.65
    rcs = rng.uniform(1.0, 25.0)
    conf = max(0.1, min(1.0, base_conf + rng.uniform(-0.1, 0.1)))
    return RadarReturn(track_id=track_id, rcs_dbsm=round(rcs, 2), detection_confidence=round(conf, 2))


def generate_thermal_signature(track_id: str, velocity_ms: Optional[float], seed_offset: int = 0) -> ThermalSignature:
    rng = random.Random(hash(track_id) + seed_offset + 1)
    # 속도가 빠를수록 엔진 출력이 높다고 가정 -> 열 신호 강함
    base_heat = min(1.0, (velocity_ms or 0) / 300.0)
    heat = max(0.0, min(1.0, base_heat + rng.uniform(-0.1, 0.15)))
    return ThermalSignature(track_id=track_id, heat_intensity=round(heat, 2), is_hot=heat > 0.55)


def generate_sigint_emission(track_id: str, seed_offset: int = 0) -> SigintEmission:
    rng = random.Random(hash(track_id) + seed_offset + 2)
    detected = rng.random() < 0.35  # 35% 확률로 전파 신호 포착
    if not detected:
        return SigintEmission(track_id=track_id, emission_detected=False, frequency_band=None, signal_strength_db=None)
    band = rng.choice(["X-band", "S-band", "Ku-band", "unknown"])
    strength = round(rng.uniform(-90, -30), 1)
    return SigintEmission(track_id=track_id, emission_detected=True, frequency_band=band, signal_strength_db=strength)


def enrich_track_with_synthetic_sensors(radar_like_dict: dict) -> dict:
    """
    flight_tracker.AirTrack.to_radar_like_dict() 결과를 입력받아
    합성 레이더/열화상/SIGINT 신호를 덧붙인 통합 딕셔너리를 반환한다.
    """
    track_id = radar_like_dict["track_id"]
    radar = generate_radar_return(track_id, radar_like_dict.get("altitude_m"))
    thermal = generate_thermal_signature(track_id, radar_like_dict.get("speed_ms"))
    sigint = generate_sigint_emission(track_id)

    merged = dict(radar_like_dict)
    merged["radar"] = radar.__dict__
    merged["thermal"] = thermal.__dict__
    merged["sigint"] = sigint.__dict__
    return merged


if __name__ == "__main__":
    sample = {
        "track_id": "abc123",
        "label": "TEST01",
        "lat": 36.5, "lon": 127.8,
        "altitude_m": 8500, "speed_ms": 230, "heading_deg": 90,
        "climb_rate_ms": 0, "source": "ADS-B(OpenSky)",
    }
    result = enrich_track_with_synthetic_sensors(sample)
    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))
