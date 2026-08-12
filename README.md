# 다중 센서 위협 융합 및 상황보고 자동화 에이전트

LangGraph 기반 국방 시뮬레이션 프로젝트. 실시간 항공기 위치(OpenSky) + 기상(Open-Meteo)에
합성 레이더·열상·SIGINT 신호를 결합해서 위협을 자동 평가하고, 고위협 트랙은 사람(분석관)의
승인을 받아 경보를 발령하는 human-in-the-loop 파이프라인입니다. 웹 대시보드로 실시간 감시·
승인·경보 이력 확인까지 가능합니다.

[![tests](https://github.com/Deukseon/sitrep_fusion_agent/actions/workflows/tests.yml/badge.svg)](https://github.com/Deukseon/sitrep_fusion_agent/actions/workflows/tests.yml)

## Phase 진행 상황

| Phase | 내용 | 상태 |
|---|---|---|
| 1. 기반 정리 | 환경설정 중앙화(`config.py`), 로깅 체계 정리 | ✅ 완료 |
| 2. 공개/문서화 | GitHub 공개, README, 아키텍처 문서 | ✅ 완료 |
| 3. 상태 지속성 | 트랙 이력 저장 및 실제 이동 벡터 계산(`fusion/track_history.py`) | ✅ 완료 |
| 4. 경로 예측 | 선형 외삽으로 N분 후 위치 예측, 보호구역 진입 예상(`fusion/trajectory_prediction.py`) | ✅ 완료 |
| 5. 실시간 대시보드 | FastAPI 기반 웹 관제 화면, 분석관 승인/거부, 경보 이력(`dashboard_api.py`) | ✅ 완료 |
| 6. 컴퓨터비전 확장 | EO/IR 표적탐지 모듈 (12월까지 목표) | 🔜 예정 |

세부 논의 과정과 설계 결정 이유는 `LangGraph_위협융합_에이전트_진행기록.md`에 세션별로 기록되어 있습니다.

## 기술 아키텍처

![기술 아키텍처](docs/architecture.svg)

> 이 다이어그램은 Phase 1~2 시점 기준입니다. Phase 3~5(이력 저장, 경로 예측, 대시보드)는
> 아직 반영되지 않았습니다 — 최신 파이프라인 구조는 아래 "파이프라인 흐름" 섹션 참고.

## 운영 시나리오

Phase 1~5를 모두 반영한 최신 운영 시나리오 및 협업 검토 문서: `docs/운영시나리오_협업검토문서_Phase1-5.docx`
(개발자·기획자·현업자가 함께 검토할 수 있도록 실제 대시보드 스크린샷과 역할별 체크포인트, 현업자
검증이 필요한 논의 항목까지 포함되어 있습니다.)

Phase 2 시점의 초기 스토리보드는 `docs/scenario_storyboard.png` 참고 (터미널 기반 승인 방식 —
현재는 웹 대시보드로 대체됨).

## 구조

```
sitrep_fusion_agent/
├── data_sources/
│   ├── flight_tracker.py         # 실제 API: OpenSky Network (실시간 항공기 위치)
│   ├── weather_api.py            # 실제 API: Open-Meteo (실시간 기상)
│   └── synthetic_sensors.py      # 합성: 레이더/열상/SIGINT (실제 트랙에 상관시켜 생성)
├── fusion/
│   ├── threat_scoring.py         # 규칙 기반 다중 센서 융합 위협 스코어링 + CRITICAL 오버라이드
│   ├── geofence.py                # 보호구역 근접/접근 판정
│   ├── track_history.py           # Phase 3: 트랙 이력 저장 및 실제 이동 벡터 계산
│   └── trajectory_prediction.py   # Phase 4: 선형 외삽 기반 N분 후 위치 예측
├── agent/
│   └── graph.py                   # LangGraph 파이프라인 (수집→융합→예측→평가→[분석관 확인]→브리핑)
├── static/
│   └── dashboard.html             # Phase 5: 실시간 웹 대시보드 (지도+패널+요약+경보이력)
├── docs/                          # 아키텍처 다이어그램, 운영 시나리오, 문헌 조사 노트
├── config.py                       # 환경설정 중앙화 (.env 자동 로드)
├── audit_log.py                    # 감사 로그 (분석관 결정 이력, pending→approve/reject)
├── main.py                         # 실행 진입점 (1회성, 대화형 승인)
├── continuous_monitor.py           # CLI 지속 감시 (30초 간격 폴링, 이력 누적)
├── dashboard_api.py                 # Phase 5: FastAPI 웹 서버 (폴링 루프 내장 + REST API)
├── visualize_map.py                 # 1회 스냅샷을 지도 HTML로 저장
└── test_*.py (7개, pytest)          # 전체 테스트 스위트
```

## 파이프라인 흐름

한 사이클(기본 30초 간격)마다 아래 순서로 실행됩니다.

```
fetch_data → fuse_sensors → predict_trajectory → assess_threats
                                                        │
                                        ┌───(HIGH 이상 없음)─→ generate_brief
                                        └───(HIGH 이상 있음)─→ log_pending_review → analyst_review ⏸
                                                                                          │
                                                                          승인(approve) │ 거부(reject)
                                                                                          ▼         ▼
                                                                                    send_alert  generate_brief
                                                                                          │
                                                                                          ▼
                                                                                    generate_brief → END
```

- `predict_trajectory`: 트랙 이력이 있으면 실제 이동 벡터로, 없으면 API 순간값으로 N분 후 위치를 예측하고 보호구역 진입 여부를 판단합니다.
- `log_pending_review`: 분석관 확인 대기가 시작되는 시점을 먼저 감사 로그에 남깁니다 — `analyst_review`는 `interrupt()` 재개 시 노드 전체가 재실행되므로, 이 로그를 별도 노드로 분리해서 중복 기록을 방지했습니다.
- `analyst_review`: `interrupt()` + `MemorySaver`(checkpointer) 조합으로 구현. `thread_id`로 세션을 구분하므로 여러 감시 구역/사용자를 동시에 운용 가능합니다.

## 위협 판정 로직

기본은 항목별 점수 누적(호출부호 미확인 +20, 저고도 고속 +25, 보호구역 반경 내 +15 등, 자세한 항목은
`fusion/threat_scoring.py` 주석 참고)이지만, 두 가지 규칙 기반 오버라이드가 점수 계산과 별개로 작동합니다.

1. **구역 내부 위치 오버라이드**: 미확인 물체가 보호구역 내부에 실제로 위치하면 점수와 무관하게 CRITICAL로 강제. 단, 수직속도가 뚜렷하면(이착륙 패턴 추정) 이 오버라이드에서 제외됩니다 — 실제 국제공항 좌표를 보호구역으로 쓰다 보니 정상 이착륙 여객기가 오탐되는 문제를 수직속도로 완화했습니다.
2. **지속성 게이트**: 구역과 무관한 순수 센서 신호 조합만으로 CRITICAL이 나오려면, 단일 관측만으로는 부족하고 이력(2회 이상 실관측)이 뒷받침되어야 합니다. 레이더 공학의 CFAR(오경보율을 일정하게 유지하기 위한 적응형 문턱값) 개념과 발상이 유사합니다 (`docs/레이더_기초_개념_정리.md` 참고).

## 실행 방법

```bash
pip install -r requirements.txt
cp .env.example .env   # 실제 키 값을 채워넣기 (없어도 대부분 동작)
```

**방법 1 — 1회 실행 (대화형)**
```bash
python main.py
```

**방법 2 — CLI 지속 감시 (30초 간격)**
```bash
python continuous_monitor.py
```

**방법 3 — 웹 대시보드 (추천)**
```bash
uvicorn dashboard_api:app --reload --port 8000
```
브라우저에서 `http://127.0.0.1:8000/` 접속. 지도+상세패널+요약카드+경보이력이 자동 갱신(5초)되며,
고위협 트랙 발견 시 화면에서 바로 승인/거부할 수 있습니다.

## 테스트

```bash
pytest -v
```

7개 파일, 23개 테스트로 TrackHistory 누적, 경로 예측, CRITICAL 오버라이드, 지속성 게이트,
FastAPI 백엔드, 폴링 간격 회귀(Phase 3에서 30초로 정한 근거를 고정 assert로 재현), 무작위
시나리오 파이프라인 정합성까지 검증합니다. 전부 목데이터/목서버 기반이라 실제 API 키 없이도
동작하고, GitHub Actions로 push마다 자동 실행됩니다.

## 설계 포인트 (포트폴리오 설명용)

1. **실제 데이터 + 합성 데이터 하이브리드**: 실제 군사 센서 데이터는 공개되지 않으므로,
   공개 ADS-B 항적을 '실제 물체'로 삼고 레이더/열상/SIGINT는 그 물체의 물리량(고도·속도)에
   상관된 합성 신호로 생성합니다. RCS 값 범위 등은 공개 문헌(MIT Lincoln Lab, 한국군사과학
   기술학회지)을 근거로 삼았습니다 — `논문_리뷰_노트.md` 참고.

2. **장애 허용 설계(Fail-safe)**: 각 API 호출 모듈은 실패 시 예외를 던지지 않고
   빈 값/기본값으로 폴백합니다.

3. **설명 가능한 스코어링**: 위협 점수는 블랙박스가 아니라 `reasons` 리스트로
   근거를 남겨서, LLM 브리핑 생성과 사람의 최종 판단 모두에 활용됩니다.

4. **상태 지속성과 지속성 게이트**: 단일 관측만으로 최고 등급을 확정하지 않고, 이력이
   쌓일수록 판정이 격상되는 구조 — 레이더 공학의 CFAR와 유사한 발상을 문헌 조사 없이
   먼저 설계했고, 나중에 관련 이론과 맞닿아 있다는 걸 확인했습니다.

## 알려진 한계

- 레이더/열상/SIGINT는 실제 센서가 아니라 시뮬레이션이며, 위치 정보도 민간 ADS-B(OpenSky)라 군사 레이더 데이터가 아닙니다.
- 위협 점수 임계값(80점, 55점 등)은 실측 데이터 기반이 아니라 설계 논의를 통해 정한 값입니다.
- RCS는 방향각과 무관하게 무작위 생성됩니다 — 실제로는 관측각에 따라 RCS가 크게 달라진다는 게 알려진 단순화입니다.
- 보호구역으로 실제 국제공항 좌표를 사용해서, 정상 여객기 트래픽이 구조적으로 많이 발생합니다(수직속도로 일부 완화).

이런 한계와 설계 결정 배경은 `방산_프로젝트_성장방향_노트.md`, `LangGraph_위협융합_에이전트_진행기록.md`에 상세히 기록되어 있습니다.

## 다음 단계

Phase 6(컴퓨터비전 확장 — EO/IR 표적탐지)이 다음 예정 단계입니다. 자세한 계획은
`프로젝트_진행_스케줄.md` 참고.
