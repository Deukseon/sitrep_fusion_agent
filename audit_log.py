"""
의사결정 감사 로그 (Audit Log)

실제 지휘통제 시스템은 '누가, 언제, 왜 그렇게 판단했는지'를 전부 기록해서
사후 검토와 책임소재 확인에 쓴다. 이 모듈은 분석관의 승인/거부 결정을
JSON Lines 형식(한 줄에 레코드 하나)으로 파일에 남긴다.

JSON Lines를 쓰는 이유: 로그가 계속 쌓여도 한 줄씩 추가만 하면 되고,
나중에 pandas.read_json(lines=True) 등으로 바로 분석 가능하다.
"""
import json
import time
from pathlib import Path
from typing import Optional

AUDIT_LOG_PATH = Path(__file__).parent / "audit_log.jsonl"


def log_decision(
    track_id: str,
    label: str,
    score: float,
    level: str,
    identity: str,
    decision: str,
    alert_sent: bool,
    zone_name: Optional[str] = None,
) -> None:
    """분석관의 승인/거부 결정 한 건을 감사 로그에 추가한다."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "track_id": track_id,
        "label": label,
        "score": score,
        "level": level,
        "identity": identity,
        "zone_name": zone_name,
        "decision": decision,       # "approve" / "reject"
        "alert_sent": alert_sent,
    }
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_audit_log() -> list[dict]:
    """감사 로그 전체를 읽어서 리스트로 반환 (분석/검토용)"""
    if not AUDIT_LOG_PATH.exists():
        return []
    with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_recent_alerts(limit: int = 10) -> list[dict]:
    """
    실제로 발령된(alert_sent=True) 경보만, 최신순으로 최대 limit건 반환.
    "pending"(대기 시작 기록)이나 "reject"(보류)는 제외 - 대시보드 경보 이력
    패널용. 보류 이력까지 보고 싶으면 read_audit_log()로 전체 로그를 직접 확인.
    """
    all_entries = read_audit_log()
    alerts = [e for e in all_entries if e.get("alert_sent") is True]
    alerts.sort(key=lambda e: e["timestamp"], reverse=True)
    return alerts[:limit]


if __name__ == "__main__":
    log_decision(
        track_id="test001", label="UNKNOWN", score=65.0, level="HIGH",
        identity="UNKNOWN", decision="approve", alert_sent=True,
        zone_name="인천국제공항 관제권",
    )
    print(f"감사 로그 기록 위치: {AUDIT_LOG_PATH}")
    for entry in read_audit_log():
        print(entry)
