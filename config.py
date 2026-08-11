"""
프로젝트 전역 설정값 모음.

이전에는 MONITOR_BBOX, 임계값(55점) 등이 여러 파일(main.py, continuous_monitor.py,
visualize_map.py, agent/graph.py)에 각각 흩어져 있었다. 이제 이 파일 하나만 고치면
전체 시스템의 설정이 바뀌도록 통일했다.

.env 자동 로드도 여기서 한 번만 처리한다 - load_dotenv()를 이 파일 맨 위에서
호출하면, 이 config를 import하는 모든 스크립트에서 자동으로 .env 값이 로드된다.
"""
import os
from dotenv import load_dotenv

# .env 파일이 있으면 자동으로 읽어서 환경변수로 등록한다.
# (.env가 없어도 에러 없이 조용히 넘어가므로, .env를 안 만든 사람도 기존처럼 동작한다)
load_dotenv()

# --- 감시 구역 설정 ---
# 2026-08-11 변경: 기존 한반도 근해 전체(48제곱도, 2크레딧/회)에서
# 보호구역(인천/부산·김해) 주변으로 좁힘 (14제곱도, 1크레딧/회).
# 근거: LangGraph_위협융합_에이전트_진행기록.md의 폴링 간격 검증 결과 —
#   "가장자리 스침 + 고속 위협"은 30초 이하 간격이 아니면 원천적으로 놓칠 수 있음.
#   1크레딧/회여야 등록 계정(4000크레딧/일) 기준 21.6초 간격까지 가능해져 30초 목표에 여유가 생김.
MONITOR_BBOX = (34.5, 125.5, 38.0, 129.5)   # (lamin, lomin, lamax, lomax) - 인천·부산/김해 관제권 포함
CENTER_LAT = 36.25
CENTER_LON = 127.5

# --- 위협 스코어링 설정 ---
ANALYST_REVIEW_THRESHOLD = 55.0   # 이 점수 이상이면 분석관 확인(interrupt) 대상

# --- 동적 감시 설정 ---
POLL_INTERVAL_SECONDS = 30   # continuous_monitor.py 폴링 주기 (5분 -> 30초, 2026-08-11 재조정)

# --- 경로 예측 설정 (Phase 4) ---
PREDICTION_MINUTES = 5.0   # 현재 속도/방향을 유지한다고 가정했을 때 몇 분 뒤 위치를 예측할지

# --- 보호구역(geofence) 설정 ---
# 예시로 공개적으로 알려진 대형 공항/발전소 좌표를 사용 (실제 군사기지 좌표 아님).
# name, lat, lon, radius_km: 이 반경 안에 들어오면 위협 스코어링에 가중치를 준다.
PROTECTED_ZONES = [
    {"name": "인천국제공항 관제권", "lat": 37.4602, "lon": 126.4407, "radius_km": 20},
    {"name": "부산/김해 관제권", "lat": 35.1795, "lon": 128.9382, "radius_km": 20},
]

# --- API 키 (.env에서 자동 로드됨) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENSKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID")
OPENSKY_CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET")
