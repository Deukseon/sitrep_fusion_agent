"""
Phase 6: 실제 컴퓨터비전 기반 객체탐지 (EO/IR 표적탐지)

지금까지 `synthetic_sensors.py`의 "열상(thermal)"은 트랙 하나당 True/False 랜덤값이었다.
이 모듈은 그 자리를 대체한다 - 실제 항공/위성 이미지에 YOLO26-OBB(방향성 바운딩박스)
모델을 돌려서, 비행기·선박·차량 등을 진짜로 탐지한다.

YOLO26-OBB는 DOTA(Dataset for Object deTection in Aerial images) 데이터셋으로
이미 사전학습되어 나오기 때문에, 별도 학습 없이 바로 쓸 수 있다 (Ultralytics 공식
문서, docs.ultralytics.com/tasks/obb 확인, 2026-08-12).

핵심 개념 - 지리참조(georeferencing):
CV 모델은 "이미지 안 픽셀 좌표"로 결과를 준다. 우리 시스템은 "위경도"로 트랙을
다루므로, 이미지가 실제로 커버하는 지리적 범위(경계 상자)를 알고 있다는 전제 하에
픽셀 좌표를 선형 보간해서 위경도로 변환한다. 실제 위성/드론 영상에는 이 경계
정보(지오태그)가 메타데이터로 따라오는 게 보통이다.
"""
import os
import sys
from dataclasses import dataclass
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_model_cache = {}


@dataclass
class GeoBounds:
    """이미지가 실제로 커버하는 지리적 범위 (실제로는 위성/드론 메타데이터에서 옴)"""
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


@dataclass
class CVDetection:
    object_class: str        # 예: "ship", "plane", "large vehicle"
    confidence: float        # 0~1
    lat: float
    lon: float
    source: str = "YOLO26-OBB(DOTA)"


def _get_model(weights: str = "yolo26n-obb.pt"):
    """모델은 한 번만 로드해서 캐시 (매번 새로 로드하면 느림)"""
    if weights not in _model_cache:
        from ultralytics import YOLO
        _model_cache[weights] = YOLO(weights)
    return _model_cache[weights]


def _pixel_to_latlon(px: float, py: float, img_w: int, img_h: int, bounds: GeoBounds) -> tuple[float, float]:
    """
    이미지 픽셀 좌표(px, py)를 위경도로 선형 변환.
    이미지 좌상단이 (lat_max, lon_min), 우하단이 (lat_min, lon_max)라고 가정
    (일반적인 위성영상 좌표계 - y가 아래로 갈수록 위도가 낮아짐).
    """
    frac_x = px / img_w
    frac_y = py / img_h
    lon = bounds.lon_min + frac_x * (bounds.lon_max - bounds.lon_min)
    lat = bounds.lat_max - frac_y * (bounds.lat_max - bounds.lat_min)
    return lat, lon


def detect_objects(image_path: str, bounds: GeoBounds, conf_threshold: float = 0.4,
                    weights: str = "yolo26n-obb.pt") -> list[CVDetection]:
    """
    이미지 한 장에서 객체를 탐지하고, 각 탐지 결과를 위경도로 지리참조해서 반환.

    conf_threshold: 이 신뢰도 미만인 탐지는 버림 (오탐 필터링, YOLO 기본값 0.25보다
    보수적으로 0.4를 씀 - 우리 시스템에서 이 결과가 위협 판정 증거로 쓰이기 때문에
    확실한 것만 반영하고 싶어서)
    """
    model = _get_model(weights)
    results = model(image_path, conf=conf_threshold, verbose=False)

    detections = []
    for r in results:
        img_h, img_w = r.orig_shape
        if r.obb is None or len(r.obb) == 0:
            continue
        for i in range(len(r.obb)):
            cls_id = int(r.obb.cls[i].item())
            conf = float(r.obb.conf[i].item())
            cx, cy = r.obb.xywhr[i][:2].tolist()   # 중심 픽셀 좌표
            lat, lon = _pixel_to_latlon(cx, cy, img_w, img_h, bounds)
            detections.append(CVDetection(
                object_class=r.names[cls_id],
                confidence=round(conf, 3),
                lat=round(lat, 6),
                lon=round(lon, 6),
            ))
    return detections


def find_nearby_detection(track_lat: float, track_lon: float, detections: list[CVDetection],
                            max_distance_km: float = 2.0) -> Optional[CVDetection]:
    """
    트랙 위경도 근처(기본 2km 이내)에 CV가 탐지한 객체가 있으면 가장 가까운 것 하나를 반환.

    [알려진 단순화, 2026-08-12 발견] max_distance_km=2.0은 엄밀한 근거 없이 정한 값이다.
    제대로 하려면 "표적 속도 × (지금 - 이미지 촬영 시각)"으로 계산해야 한다 - 이미지가
    찍힌 후 시간이 지날수록 표적이 실제로 더 멀리 이동했을 수 있기 때문.

    실제 수치로 검산해보면 2km가 얼마나 타이트한 가정인지 드러난다: 한화시스템이 개발
    중인 소형 SAR(Synthetic Aperture Radar) 위성 군집의 목표 재방문주기는 30분 이하인데,
    선박이 6~8m/s로 움직인다고 가정하면 30분 동안 최대 11~15km 이동할 수 있다 - 2km는
    이것보다 훨씬 빠른 재방문(6~8분 수준, 군사용 초긴급표적 탐지에 쓰는 48기급 SAR 군집
    위성 수준)을 전제해야 말이 되는 값이다. (방산_프로젝트_문헌조사_전략.md 2차 조사 참고)

    또한 위성은 한 지점을 실시간으로 계속 보지 못한다 - 실제로는 HF(High Frequency)
    레이더+AIS(Automatic Identification System) 상시감시 + 위성/무인기 이벤트성 투입의
    다층 구조가 표준이다. 이 함수가 "이미지가 항상 존재한다"고 가정하는 것 자체가
    데모용 단순화라는 걸 명시해둔다.
    """
    from fusion.geofence import distance_km

    best, best_dist = None, max_distance_km
    for d in detections:
        dist = distance_km(track_lat, track_lon, d.lat, d.lon)
        if dist <= best_dist:
            best, best_dist = d, dist
    return best


def enrich_track_with_cv_detection(radar_like_dict: dict, detections: list[CVDetection]) -> dict:
    """
    synthetic_sensors.enrich_track_with_synthetic_sensors()의 "thermal" 자리를 대체.
    트랙 위경도 근처에 실제 CV 탐지 결과가 있으면 그걸 증거로 쓰고, 없으면
    "탐지 안 됨"으로 명시한다 (합성 데이터처럼 확률적으로 True/False를 굴리지 않음 -
    이게 핵심 차이: 진짜로 있으면 있다고, 없으면 없다고 한다).
    """
    merged = dict(radar_like_dict)
    match = find_nearby_detection(radar_like_dict.get("lat"), radar_like_dict.get("lon"), detections)

    if match:
        merged["cv_detection"] = {
            "detected": True,
            "object_class": match.object_class,
            "confidence": match.confidence,
            "source": match.source,
        }
    else:
        merged["cv_detection"] = {"detected": False, "object_class": None, "confidence": None, "source": None}

    return merged


if __name__ == "__main__":
    # 자체 테스트: 실제 항공 사진(선착장, 선박 다수)으로 탐지 -> 지리참조 -> 트랙 매칭까지 확인
    import time

    # 이 테스트 이미지가 특정 위경도 범위를 촬영했다고 "가정" (실제로는 메타데이터에서 옴)
    test_bounds = GeoBounds(lat_min=35.10, lat_max=35.12, lon_min=129.05, lon_max=129.08)

    print("=== 이미지에서 객체 탐지 ===")
    t0 = time.time()
    dets = detect_objects("/home/claude/cv_test/boats_real.jpg", test_bounds)
    print(f"탐지 {len(dets)}건, {time.time()-t0:.2f}초")
    for d in dets[:5]:
        print(f"  {d.object_class} (신뢰도 {d.confidence}) @ ({d.lat}, {d.lon})")

    print("\n=== 탐지된 객체 근처에 있는 가상 트랙과 매칭 ===")
    near_track = {"track_id": "T1", "lat": dets[0].lat, "lon": dets[0].lon}
    enriched = enrich_track_with_cv_detection(near_track, dets)
    print(f"근처 트랙: {enriched['cv_detection']}")
    assert enriched["cv_detection"]["detected"] is True

    print("\n=== 탐지 안 된 먼 곳의 트랙 ===")
    far_track = {"track_id": "T2", "lat": 30.0, "lon": 120.0}
    enriched2 = enrich_track_with_cv_detection(far_track, dets)
    print(f"먼 트랙: {enriched2['cv_detection']}")
    assert enriched2["cv_detection"]["detected"] is False

    print("\n🎉 CV 탐지 모듈 자체 테스트 통과")
