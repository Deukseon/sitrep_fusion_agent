"""
다중 센서 융합 위협 스코어링

입력: synthetic_sensors.enrich_track_with_synthetic_sensors() 결과 딕셔너리 +
      weather_api.WeatherSnapshot (환경 신뢰도 가중치용)
출력: 0~100 위협 점수 + 등급(LOW/MEDIUM/HIGH/CRITICAL) + 근거 설명 +
      피아식별(identity) + 보호구역 근접 정보(geofence)

가중치는 임의 설정한 규칙 기반(rule-based) 예시입니다.
실무에서는 라벨링된 데이터로 학습한 모델로 대체 가능합니다.
"""
import sys
import os
from dataclasses import dataclass, asdict
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PROTECTED_ZONES
from fusion.identification import classify_identity
from fusion.geofence import check_nearest_zone


@dataclass
class ThreatAssessment:
    track_id: str
    label: str
    score: float          # 0~100
    level: str            # LOW / MEDIUM / HIGH / CRITICAL
    reasons: list[str]
    identity: str          # FRIEND / HOSTILE / NEUTRAL / UNKNOWN (위협등급과 별개의 축)
    identity_basis: str
    zone_name: Optional[str] = None
    zone_distance_km: Optional[float] = None
    predicted_lat: Optional[float] = None       # Phase 4: N분 뒤 예상 위도
    predicted_lon: Optional[float] = None       # Phase 4: N분 뒤 예상 경도
    prediction_minutes: Optional[float] = None
    predicted_zone_name: Optional[str] = None   # 예측 위치가 보호구역 안에 들어올 경우만 채워짐

    def to_dict(self) -> dict:
        """
        LangGraph checkpointer는 msgpack으로 상태를 직렬화하는데,
        커스텀 dataclass를 그대로 저장하면 경고/향후 버전에서 에러가 난다.
        interrupt/checkpoint에 태울 상태에는 항상 dict로 변환해서 넣는다.
        """
        return asdict(self)


def _level_from_score(score: float) -> str:
    if score >= 80:
        return "CRITICAL"
    if score >= 55:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def score_track(enriched_track: dict, visibility_ok: bool = True) -> ThreatAssessment:
    reasons = []
    score = 0.0

    # 1) 식별 여부 - callsign(신원)이 없으면 미확인 물체로 가중치 상승
    if enriched_track.get("label") in (None, "UNKNOWN", ""):
        score += 20
        reasons.append("호출부호 미확인(+20)")

    # 2) 고도/속도 패턴 - 저고도 고속 접근은 위협 신호로 간주(단순 예시 규칙)
    altitude = enriched_track.get("altitude_m") or 0
    speed = enriched_track.get("speed_ms") or 0
    if altitude < 1500 and speed > 100:
        score += 25
        reasons.append(f"저고도 고속 접근 패턴(고도 {altitude:.0f}m, 속도 {speed:.0f}m/s)(+25)")

    # 3) 레이더 반사 신뢰도
    radar = enriched_track.get("radar", {})
    if radar.get("detection_confidence", 0) > 0.85 and radar.get("rcs_dbsm", 0) < 5:
        score += 15
        reasons.append("소형 고신뢰 레이더 반사(스텔스성 소형체 가능성)(+15)")

    # 4) 열 신호
    thermal = enriched_track.get("thermal", {})
    if thermal.get("is_hot"):
        score += 15
        reasons.append("고열원 신호 감지(엔진 고출력 추정)(+15)")

    # 5) SIGINT - 미상 주파수 대역 신호
    sigint = enriched_track.get("sigint", {})
    if sigint.get("emission_detected") and sigint.get("frequency_band") == "unknown":
        score += 20
        reasons.append("미상 주파수 대역 전파 신호 포착(+20)")
    elif sigint.get("emission_detected"):
        score += 5
        reasons.append(f"{sigint.get('frequency_band')} 대역 신호 포착(+5)")

    # 6) 기상 조건 - 시정 불량 시 판단 신뢰도를 낮추는 방향으로 소폭 감점(과탐 방지)
    if not visibility_ok:
        score = score * 0.9
        reasons.append("기상 불량으로 신뢰도 보정(x0.9)")

    # 7) 보호구역 근접/접근 여부
    lat, lon = enriched_track.get("lat"), enriched_track.get("lon")
    heading = enriched_track.get("heading_deg")
    zone_name, zone_distance = None, None
    geofence = None
    if lat is not None and lon is not None:
        geofence = check_nearest_zone(lat, lon, heading, PROTECTED_ZONES)
        zone_name, zone_distance = geofence.zone_name, geofence.distance_km
        if geofence.inside_zone:
            score += 15
            reasons.append(f"보호구역 '{geofence.zone_name}' 반경 내 위치(거리 {geofence.distance_km}km)(+15)")
        if geofence.approaching and geofence.distance_km is not None and geofence.distance_km <= 100:
            score += 10
            reasons.append(f"보호구역 '{geofence.zone_name}' 방향으로 접근 중(+10)")

    # 8) 예측 경로 기반 보호구역 진입 예상 (Phase 4 - TrackHistory 선형 외삽 결과 활용)
    # 7번의 "접근 중" 판정은 현재 헤딩과 보호구역 방위각의 각도 차이(±60도)로만 근사한 것이라
    # 정확도에 한계가 있다. 이제 실제 궤적을 외삽한 예측 위치가 있으니, 그 위치가 보호구역
    # 안에 들어오는지로 훨씬 직접적으로 판단할 수 있다. 이미 현재 위치가 구역 안이면(7번에서
    # +15 처리됨) 중복 가산하지 않는다.
    predicted_lat = enriched_track.get("predicted_lat")
    predicted_lon = enriched_track.get("predicted_lon")
    prediction_minutes = enriched_track.get("prediction_minutes")
    velocity_source = enriched_track.get("velocity_source")   # "history" or "reported_fallback" (agent/graph.py의 predict_trajectory 노드가 채워줌)
    predicted_zone_name = None
    predicted_inside = False
    if predicted_lat is not None and predicted_lon is not None:
        predicted_geofence = check_nearest_zone(predicted_lat, predicted_lon, None, PROTECTED_ZONES)
        currently_inside = bool(geofence and geofence.inside_zone)
        if predicted_geofence.inside_zone and not currently_inside:
            score += 20
            predicted_zone_name = predicted_geofence.zone_name
            predicted_inside = True
            minutes_label = f"{prediction_minutes:.0f}" if prediction_minutes is not None else "N"
            reasons.append(
                f"예측 경로 기준 {minutes_label}분 후 '{predicted_geofence.zone_name}' 진입 예상(+20)"
            )

    # 9) 규칙 기반 오버라이드 (Phase 5 준비 - 대시보드 우선순위 왜곡 문제 발견 후 추가, 2026-08-11)
    #
    # 문제: 지금까지의 1~8번은 전부 "점수를 더하는" 방식이라, 성격이 다른 두 증거가
    # 같은 저울 위에 놓인다 - "보호구역 안에 실제로 들어와 있다"는 직접 관측된 사실(fact)과
    # "레이더 반사가 작고 신뢰도가 높다"는 정황상 추정 증거(inference)가 똑같이 점수로
    # 환산된다. 그 결과, 실제로 구역 침범 중인 미확인 물체가 75점(HIGH)에 머무는데
    # 구역과 무관하게 우연히 센서 3종이 겹친 물체가 95점(CRITICAL)로 더 급해 보이는
    # 역전 현상이 생길 수 있다.
    #
    # 1차 해법: "미확인 + 보호구역 내부에 실제로 위치"는 점수 누적과 별개로 CRITICAL 강제.
    # 실제 방공 체계에서도 비행금지구역(NFZ) 실제 침범은 점수 누적이 아니라 하드 트리거로
    # 다루는 것과 같은 논리.
    #
    # 추가 논의(같은 세션, 이어서): 그럼 "진입 *예정*"(아직 안 들어왔지만 예측상 곧 들어옴)도
    # 위험 신호로 봐야 하지 않냐는 질문이 나왔다. 맞는 지적이지만, 예측은 "지금 속도·방향을
    # 유지한다"는 가정의 선형 외삽이라 선회하면 바로 틀린다 - 무조건 CRITICAL로 올리면
    # 오탐이 늘어 "경보 피로"(계속 틀리면 나중엔 무시하게 되는 현상)로 이어질 위험이 있다.
    # 그렇다고 완전히 무시하면 애초에 Phase 4(경로 예측)를 만든 의미가 반감된다.
    #
    # 절충안: predict_trajectory 노드가 이미 velocity_source를 "history"(TrackHistory로
    # 2회 이상 실관측해서 뒷받침된 궤적) / "reported_fallback"(API 순간값 1회성 추정)으로
    # 구분해서 넘겨주고 있었으므로, 이 값을 재사용한다. "여러 번 지켜봤는데도 일관되게
    # 구역으로 향하고 있다"(velocity_source == "history")일 때만 확정 취급해서 오버라이드
    # 대상에 포함하고, "방금 처음 본 트랙의 순간값 계산"(reported_fallback)일 때는 여전히
    # 8번의 +20점만 준다 - 신뢰도가 낮은 추정치로 최고 등급을 확정하지 않기 위함.
    is_unidentified = enriched_track.get("label") in (None, "UNKNOWN", "")
    currently_inside_zone = bool(geofence and geofence.inside_zone)
    prediction_confirmed_by_history = predicted_inside and velocity_source == "history"

    if is_unidentified and score < 80 and (currently_inside_zone or prediction_confirmed_by_history):
        score = 80.0
        if currently_inside_zone:
            reasons.append(
                f"규칙 기반 오버라이드: 미확인 물체가 보호구역 '{geofence.zone_name}' 내부에 위치 -> CRITICAL 강제 지정"
            )
        else:
            reasons.append(
                f"규칙 기반 오버라이드: 미확인 물체가 이력 기반 궤적상 보호구역 '{predicted_zone_name}' 진입이 "
                f"확실시됨(2회 이상 실관측 뒷받침) -> CRITICAL 강제 지정"
            )

    # 10) 피아식별(IFF) - 위협 점수에는 반영하지 않는 별개의 축. 표시/필터링용 정보로만 사용.
    identification = classify_identity(enriched_track.get("label"))

    score = round(min(100.0, score), 1)
    return ThreatAssessment(
        track_id=enriched_track["track_id"],
        label=enriched_track.get("label", "UNKNOWN"),
        score=score,
        level=_level_from_score(score),
        reasons=reasons,
        identity=identification.identity,
        identity_basis=identification.basis,
        zone_name=zone_name,
        zone_distance_km=zone_distance,
        predicted_lat=predicted_lat,
        predicted_lon=predicted_lon,
        prediction_minutes=prediction_minutes,
        predicted_zone_name=predicted_zone_name,
    )


def rank_tracks(assessments: list[ThreatAssessment]) -> list[ThreatAssessment]:
    """위협 점수 내림차순 정렬"""
    return sorted(assessments, key=lambda a: a.score, reverse=True)


if __name__ == "__main__":
    sample = {
        "track_id": "abc123", "label": "UNKNOWN",
        "altitude_m": 900, "speed_ms": 180,
        "radar": {"rcs_dbsm": 3.2, "detection_confidence": 0.91},
        "thermal": {"heat_intensity": 0.7, "is_hot": True},
        "sigint": {"emission_detected": True, "frequency_band": "unknown", "signal_strength_db": -40},
    }
    result = score_track(sample, visibility_ok=True)
    print(result)
