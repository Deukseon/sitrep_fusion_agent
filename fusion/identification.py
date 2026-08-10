"""
피아식별(IFF, Identification Friend or Foe) 간이 판정

실제 군용 IFF는 Mode 5/S 암호화 트랜스폰더 질의-응답으로 이뤄지지만,
공개 ADS-B 데이터(OpenSky)에는 그런 신호가 없다. 대신 민간 항공기가
송출하는 호출부호(callsign) 유무만으로 아주 단순화된 식별을 시뮬레이션한다.

이 모듈이 매기는 값은 실제 군사 표준(NATO APP-6 / MIL-STD-2525)의
FRIEND / HOSTILE / NEUTRAL / UNKNOWN 4분류를 흉내낸 것이지,
실제 신뢰할 수 있는 피아식별이 아니다 - 그 점을 코드/문서 어디서든
과장하지 않는다.
"""
from dataclasses import dataclass


# NATO 표준 심볼 색상 관례: FRIEND=파랑, HOSTILE=빨강, NEUTRAL=초록, UNKNOWN=노랑
IDENTITY_COLOR = {
    "FRIEND": "blue",
    "HOSTILE": "red",
    "NEUTRAL": "green",
    "UNKNOWN": "amber",
}


@dataclass
class Identification:
    identity: str        # FRIEND / HOSTILE / NEUTRAL / UNKNOWN
    basis: str            # 판정 근거 (사람이 읽을 수 있는 설명)


def classify_identity(label: str | None) -> Identification:
    """
    간이 피아식별 판정.

    실제 체계에서는 Mode 5 암호 질의응답으로 FRIEND를 확정하고,
    응답이 없거나 비정상이면 UNKNOWN/HOSTILE로 넘어간다.
    여기서는 그런 신호가 없으므로:
      - 호출부호(callsign)가 있는 민간 항공편 -> NEUTRAL (민간 교통으로 추정, 군사적 FRIEND는 아님)
      - 호출부호가 없는 미확인체 -> UNKNOWN
    'FRIEND'와 'HOSTILE'은 실제 IFF 질의응답이나 정보당국 판단 없이는
    공개 데이터만으로 확정할 수 없으므로 이 함수는 절대 반환하지 않는다.
    """
    if label and label != "UNKNOWN":
        return Identification(identity="NEUTRAL", basis=f"호출부호 확인됨({label}), 민간 교통으로 추정")
    return Identification(identity="UNKNOWN", basis="호출부호 미확인, IFF 질의응답 데이터 없음")
