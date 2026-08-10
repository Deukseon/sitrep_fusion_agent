# 운영 시나리오 스토리보드 — LangGraph 기반 다중 센서 위협 융합 에이전트

실제 코드 흐름(`continuous_monitor.py` → `agent/graph.py` → `audit_log.py`)을 그대로 따라가는 8단계 시나리오입니다.
포트폴리오 발표 시 "이 시스템이 실제로 어떻게 쓰이는가"를 설명하는 스토리로 활용하시면 됩니다.

---

## 등장인물 & 배경

- **인물**: 방공 관제소 분석관 (콜사인: "레이븐")
- **장소**: 관제소 상황실 콘솔 앞
- **시간대**: 평시 감시 근무 중, 특정 시각에 이상 징후 포착

---

## 1단계 — 평시 감시 (Baseline Monitoring)

**상황**: 분석관이 콘솔 화면에서 `continuous_monitor.py`가 5분 간격으로 자동 폴링하는 로그를 지켜보고 있다. 화면에는 한반도 근해 지도가 떠 있고, 대부분의 항적이 초록(LOW)·노랑(MEDIUM)으로 표시되어 있다 — 정상적인 민항기 트래픽.

**시스템 동작**: `fetch_data` → `fuse_sensors` → `assess_threats` 노드가 자동 반복 실행. 고위협 없음.

**분석관의 상태**: 평온, 반복적인 루틴 모니터링.

---

## 2단계 — 이상 징후 포착 (Anomaly Detected)

**상황**: 새 폴링 사이클에서 인천국제공항 관제권 인근(반경 20km 이내)에 처음 보는 항적이 나타난다. 호출부호 없음(미확인), 고도 800m(저고도), 속도 220m/s(고속), 항공기 진행방향이 공항 쪽을 향함.

**시스템 동작**: `합성 센서` 모듈이 이 트랙에 열 신호(엔진 고출력 추정)와 미상 주파수 대역 SIGINT 신호까지 함께 포착한 것으로 시뮬레이션.

**분석관의 상태**: 화면에서 빨간 경고성 마커를 발견, 자세를 고쳐 앉으며 집중.

---

## 3단계 — 자동 위협 평가 (Automated Threat Assessment)

**상황**: 콘솔 로그에 위협 스코어링 근거가 한 줄씩 출력된다 — "호출부호 미확인(+20)", "저고도 고속 접근 패턴(+25)", "고열원 신호 감지(+15)", "보호구역 반경 내 위치(+15)", "보호구역 방향으로 접근 중(+10)". 합계 85점, CRITICAL 등급.

**시스템 동작**: `threat_scoring.py`가 규칙 기반으로 점수를 산출하고 근거 리스트를 함께 반환.

**분석관의 상태**: 점수와 근거를 빠르게 훑어보며 상황을 파악.

---

## 4단계 — 시스템 정지, 승인 요청 (Human-in-the-Loop Gate)

**상황**: 화면에 "⏸ 분석관 확인 필요" 메시지가 뜨고, 시스템이 자동 진행을 멈춘 채 분석관의 입력을 기다린다. "경보를 발령할까요? (approve/reject)"라는 프롬프트가 커서와 함께 깜빡인다.

**시스템 동작**: `interrupt()` 호출로 LangGraph 파이프라인이 이 지점에서 실행을 중단.

**분석관의 상태**: 화면을 응시하며 판단을 내리는 순간 — 이 시나리오의 클라이맥스.

---

## 5단계 — 분석관의 판단 (Decision Point)

**상황**: 분석관이 지도 위 마커 위치, 보호구역과의 거리(11.3km), 근거 목록을 종합적으로 검토한다. 공항에 근접·접근 중이며 미확인·고속·저고도라는 복합 조건이 충족됨을 확인하고 `approve`를 입력한다.

**분석관의 상태**: 신중하지만 결단력 있게 키보드를 두드리는 순간.

---

## 6단계 — 경보 발령 및 기록 (Alert & Audit Trail)

**상황**: 화면에 "⚠ 경보 발령됨" 메시지가 뜨고, 동시에 백그라운드에서 `audit_log.jsonl`에 이 결정(누가 언제 무엇을 근거로 승인했는지)이 자동으로 기록된다.

**시스템 동작**: `send_alert` 노드 실행, `audit_log.log_decision()` 호출.

**분석관의 상태**: 결정을 내린 직후의 차분함, 다음 절차를 준비하는 태세.

---

## 7단계 — 상황보고서 생성 (SITREP Generation)

**상황**: 화면 하단에 자연어로 정리된 브리핑이 출력된다 — "인천국제공항 관제권 인근에서 미확인 저고도 고속 항적 포착, 보호구역 접근 중으로 판단되어 경보 발령함..." 같은 문장.

**시스템 동작**: `generate_brief` 노드가 Claude API를 호출해 원시 데이터를 사람이 읽기 좋은 SITREP 형태로 변환.

**분석관의 상태**: 완성된 보고서를 검토하며 다음 보고 채널로 전달할 준비.

---

## 8단계 — 결론: 상급 보고 (Escalation)

**상황**: 분석관이 완성된 SITREP을 상급 지휘부에 전파한다(이 지점부터는 현재 시스템 범위 밖 — 실제 조직의 보고 체계로 이어짐). 화면에는 이 사건이 CRITICAL 등급으로 지도와 로그에 영구 기록된 상태로 남아있다.

**결론**: 시스템은 "다중 센서 데이터 → 자동 융합·스코어링 → 사람의 최종 판단 → 기록 가능한 보고서"까지의 파이프라인을 완결했고, 이후의 실제 대응(요격, 추가 정찰 등)은 인간 지휘계통의 몫으로 넘어간다.

---

## 이미지 생성 프롬프트 (영어 — 대부분의 이미지 생성 도구가 영어 프롬프트에서 더 안정적인 결과를 냄)

각 단계별로 독립적으로 쓸 수 있는 프롬프트입니다. 스타일 통일을 원하시면 각 프롬프트 끝에 붙은 스타일 지시어를 동일하게 유지하세요.

**1단계 — 평시 감시**
```
A military air defense control room at night, an analyst calmly monitoring a wide radar map display showing scattered green and yellow aircraft blips over a peninsula coastline, soft blue console lighting, wide shot, realistic military command center illustration, muted color palette
```

**2단계 — 이상 징후 포착**
```
Close-up over the shoulder of a military analyst leaning forward at a radar console, a single red blinking blip has just appeared near an airport zone on the map display, tense atmosphere, blue-lit control room, realistic illustration style
```

**3단계 — 자동 위협 평가**
```
A radar operator's console screen close-up showing a scrolling list of automated threat-scoring log lines with a large red "CRITICAL 85" score readout, small map inset with a highlighted zone, dark UI with amber and red accent text, technical HUD illustration style
```

**4단계 — 승인 요청**
```
A military analyst sitting upright, hands paused over a keyboard, staring at a console screen displaying a flashing "ANALYST CONFIRMATION REQUIRED / approve or reject" prompt, dramatic blue-red console lighting on the analyst's face, cinematic realistic illustration
```

**5단계 — 분석관의 판단**
```
Close-up of a determined military analyst's hands typing on a keyboard, radar map with a red marker and distance readout visible on the monitor behind, focused expression, moody control-room lighting, realistic illustration
```

**6단계 — 경보 발령 및 기록**
```
A control room screen split into two panels: left panel showing a bold "ALERT ISSUED" banner in red, right panel showing a scrolling audit log with timestamps and decision records, analyst visible in soft focus background, technical illustration style
```

**7단계 — 상황보고서 생성**
```
A console screen displaying a neatly formatted situation report (SITREP) text block in a military UI style, an analyst reading it with a hand near the screen, dark blue command-center ambiance, realistic illustration
```

**8단계 — 상급 보고**
```
Wide shot of a military command center, an analyst standing and gesturing toward a large wall display showing a finalized red-marked threat map, other officers in the background paying attention, cinematic wide illustration, dramatic but composed mood
```

**통일 스타일 지시어(선택, 모든 프롬프트 끝에 추가 가능)**:
```
, digital painting, muted military color palette, no text overlays, no readable insignia, generic uniforms, non-photorealistic illustration
```
*(실제 국기/부대마크/특정 인물처럼 보이는 요소를 피하기 위해 "generic", "no readable insignia" 같은 문구를 넣어두었습니다.)*
