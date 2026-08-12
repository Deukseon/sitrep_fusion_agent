"""
Phase 5 FastAPI 백엔드 테스트 (목데이터, 실제 서버 기동 없이 로직 직접 검증)

실행: pytest test_dashboard_api.py -v

검증 항목:
  1. 사이클 실행 후 고위협 트랙이 있으면 awaiting_review=True로 바뀌는가
  2. /approve(approve) 호출 시 alert_sent=True가 되고 awaiting_review가 풀리는가
  3. 승인 이력이 audit_log.jsonl에 남는가 (pending -> approve)
  4. /status, /tracks 엔드포인트가 실제 HTTP 요청에도 정상 응답하는가 (TestClient)
"""
import os
import sys
from unittest.mock import patch
import tempfile

import pytest

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

AUDIT_LOG_TEST_PATH = os.path.join(tempfile.gettempdir(), "test_dashboard_audit.jsonl")


def _mock_high_threat_tracks():
    from data_sources.flight_tracker import AirTrack
    # 부산/김해 관제권 중심에 위치한 미확인 저고도 고속 트랙 - CRITICAL 확정 (구역 내부 오버라이드)
    return [
        AirTrack(icao24="DASH01", callsign=None, longitude=128.9382, latitude=35.1795,
                  altitude_m=900, velocity_ms=220.0, heading_deg=90.0, vertical_rate_ms=0),
    ]


@pytest.mark.asyncio
async def test_cycle_and_approve():
    import audit_log
    audit_log.AUDIT_LOG_PATH = __import__("pathlib").Path(AUDIT_LOG_TEST_PATH)
    if os.path.exists(AUDIT_LOG_TEST_PATH):
        os.remove(AUDIT_LOG_TEST_PATH)

    with patch("agent.graph.fetch_tracks", return_value=_mock_high_threat_tracks()), \
         patch("agent.graph.fetch_weather", return_value=None):

        import dashboard_api as dapi

        print("=== 테스트 1: 사이클 실행 -> 고위협 트랙 발견 시 대기 상태 전환 ===")
        await dapi.run_cycle()
        assert dapi.dash.awaiting_review is True, "고위협 트랙이 있는데 대기 상태로 안 바뀌었습니다"
        assert len(dapi.dash.pending_tracks) == 1
        assert dapi.dash.pending_tracks[0]["track_id"] == "DASH01"
        print(f"✅ awaiting_review=True, 대기 트랙: {dapi.dash.pending_tracks[0]['track_id']} "
              f"({dapi.dash.pending_tracks[0]['level']}, {dapi.dash.pending_tracks[0]['score']}점)")

        print("\n=== 테스트 2: 대기 중에는 새 사이클을 건너뛰는가 ===")
        cycle_count_before = dapi.dash.cycle_count
        await dapi.run_cycle()   # awaiting_review=True인 상태에서 또 호출
        assert dapi.dash.cycle_count == cycle_count_before, "대기 중인데 새 사이클이 실행됐습니다!"
        print("✅ 대기 중에는 새 사이클을 건너뜀 확인")

        print("\n=== 테스트 3: 승인(approve) 처리 ===")
        result = await dapi.approve_decision(dapi.ApproveRequest(decision="approve"))
        assert dapi.dash.awaiting_review is False, "승인 후에도 대기 상태가 안 풀렸습니다"
        assert result["alert_sent"] is True, "승인했는데 alert_sent가 False입니다"
        print(f"✅ 승인 처리 완료: alert_sent={result['alert_sent']}, awaiting_review={dapi.dash.awaiting_review}")

        print("\n=== 테스트 4: 감사 로그에 pending -> approve 순서로 기록됐는가 ===")
        entries = audit_log.read_audit_log()
        decisions = [e["decision"] for e in entries if e["track_id"] == "DASH01"]
        assert decisions == ["pending", "approve"], f"로그 순서가 예상과 다릅니다: {decisions}"
        print(f"✅ 감사 로그 순서 확인: {decisions}")

        print("\n=== 테스트 5: /alerts/recent가 승인된 경보만 반환하는가 ===")
        recent = await dapi.get_recent_alerts(limit=10)
        assert len(recent["alerts"]) == 1
        assert recent["alerts"][0]["decision"] == "approve"
        print(f"✅ 최근 경보 이력: {len(recent['alerts'])}건, decision={recent['alerts'][0]['decision']}")


def test_http_endpoints_smoke():
    print("\n=== 테스트 6: 실제 HTTP 요청으로 /status, /tracks 응답 확인 (TestClient) ===")
    with patch("agent.graph.fetch_tracks", return_value=[]), \
         patch("agent.graph.fetch_weather", return_value=None):
        from fastapi.testclient import TestClient
        import dashboard_api as dapi

        with TestClient(dapi.app) as client:
            r1 = client.get("/status")
            assert r1.status_code == 200, f"/status 응답 실패: {r1.status_code}"
            print(f"✅ GET /status -> {r1.status_code}, body={r1.json()}")

            r2 = client.get("/tracks")
            assert r2.status_code == 200
            print(f"✅ GET /tracks -> {r2.status_code}, track_count={len(r2.json()['raw_tracks'])}")

            r3 = client.get("/alerts/recent")
            assert r3.status_code == 200
            print(f"✅ GET /alerts/recent -> {r3.status_code}")

            # 대기 중이 아닐 때 /approve 호출하면 409여야 함
            r4 = client.post("/approve", json={"decision": "approve"})
            assert r4.status_code == 409, f"대기 중이 아닌데 /approve가 409를 안 줍니다: {r4.status_code}"
            print(f"✅ POST /approve (대기 없음) -> {r4.status_code} (의도된 실패)")
