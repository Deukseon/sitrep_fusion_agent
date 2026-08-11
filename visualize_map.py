"""
위협 평가 결과를 인터랙티브 지도(HTML)로 시각화

사용법:
  python visualize_map.py

main.py의 파이프라인을 그대로 돌린 뒤, 결과를 지도 위에 마커로 찍어서
threat_map.html로 저장합니다. 저장된 파일을 더블클릭하면 브라우저에서 열립니다.

색상 규칙:
  CRITICAL = 빨강 / HIGH = 주황 / MEDIUM = 노랑 / LOW = 초록
"""
import logging
import folium
from agent.graph import build_graph, PipelineState, Command
from config import MONITOR_BBOX, CENTER_LAT, CENTER_LON, PROTECTED_ZONES
from fusion.track_history import load_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

LEVEL_COLOR = {
    "CRITICAL": "red",
    "HIGH": "orange",
    "MEDIUM": "beige",   # folium은 'yellow'를 직접 지원하지 않아 beige로 대체
    "LOW": "green",
}


def run_pipeline() -> dict:
    """main.py와 동일하게 파이프라인을 실행하되, 지도용으로 raw_tracks까지 함께 반환"""
    app = build_graph()
    graph_config = {"configurable": {"thread_id": "map-session"}}

    # main.py와 마찬가지로 이력은 읽기 전용으로만 사용 (쓰기는 continuous_monitor.py 담당)
    history = load_history()

    initial_state: PipelineState = {
        "bbox": MONITOR_BBOX, "center_lat": CENTER_LAT, "center_lon": CENTER_LON,
        "raw_tracks": [], "enriched_tracks": [], "visibility_ok": True,
        "assessments": [], "briefing_text": None,
        "analyst_decision": None, "alert_sent": False,
        "track_history": history,
    }
    result = app.invoke(initial_state, config=graph_config)

    if "__interrupt__" in result:
        # 지도 확인용 스크립트에서는 자동으로 보류(reject) 처리 -- 승인은 main.py에서
        result = app.invoke(Command(resume="reject"), config=graph_config)

    return result


def _fmt(value, unit: str) -> str:
    """
    None이면 'N/A', 숫자면 반올림해서 단위와 함께 표시.
    실제 항공기 데이터는 지상에 있거나 신호 누락 시 값이 None으로 오는 경우가
    흔해서, 포맷팅 전에 항상 이 함수를 거치도록 한다.
    """
    if value is None:
        return "N/A"
    return f"{value:.0f}{unit}"


def _label(location: list, text: str, color: str = "#333", bold: bool = False,
           offset_y: int = -18) -> folium.Marker:
    """
    클릭/팝업 없이도 지도에서 바로 읽히는 상시 텍스트 라벨.
    흰색 배경 박스 대신, 글자 둘레에 흰색 테두리(halo)만 넣는 방식(text-shadow)을 썼다 -
    지도 배경이 어떤 색이든 글자는 읽히면서도, 박스가 겹겹이 쌓여 지저분해 보이는 걸 피할 수 있다.

    offset_y 부호 규칙(Leaflet iconAnchor 기준): 양수 = 마커 위쪽에 표시, 음수 = 마커 아래쪽에 표시.
    같은 트랙이라도 "현재 위치 라벨"과 "예측 위치 라벨"이 서로 반대 방향에 뜨도록 호출부에서 부호를
    다르게 줘서, 두 라벨이 겹치지 않게 한다.
    """
    weight = "bold" if bold else "normal"
    halo = "-1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff, 1px 1px 0 #fff, 0 0 3px #fff"
    html = (
        f'<div style="font-size:11px; font-weight:{weight}; color:{color}; '
        f'white-space:nowrap; text-shadow:{halo};">{text}</div>'
    )
    return folium.Marker(
        location=location,
        icon=folium.DivIcon(html=html, icon_anchor=(-8, offset_y)),
    )


def _add_legend(m: folium.Map) -> None:
    """지도 우하단에 고정 범례 - 색상/기호 뜻을 별도 설명 없이도 알 수 있게."""
    legend_html = """
    <div style="position: fixed; bottom: 20px; right: 20px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 6px;
                border: 1px solid #999; box-shadow: 0 1px 4px rgba(0,0,0,0.3);
                font-size: 12px; line-height: 1.6; color: #222;">
      <b>위협 등급</b><br>
      <span style="color:red;">●</span> CRITICAL (80점 이상)&nbsp;
      <span style="color:orange;">●</span> HIGH (55점 이상)<br>
      <span style="color:#c9a227;">●</span> MEDIUM (30점 이상)&nbsp;
      <span style="color:green;">●</span> LOW (30점 미만)<br>
      <hr style="margin:4px 0;">
      <b>기호 안내</b><br>
      ─ ─ ─ &nbsp;점선 = 예측 이동 경로<br>
      ▲ &nbsp;삼각형 = N분 후 예상 위치<br>
      <span style="color:blue;">○</span> &nbsp;파란 원 = 보호구역 경계
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))


def build_map(result: dict) -> folium.Map:
    # raw_tracks(위경도)와 assessments(위협 등급)를 track_id 기준으로 합친다
    assessment_by_id = {a["track_id"]: a for a in result["assessments"]}

    m = folium.Map(location=[CENTER_LAT, CENTER_LON], zoom_start=6, tiles="cartodbpositron")

    # Phase 4: 보호구역 경계를 먼저 그려서, 예측 경로가 왜 위협으로 잡히는지 시각적 맥락을 준다
    for zone in PROTECTED_ZONES:
        folium.Circle(
            location=[zone["lat"], zone["lon"]],
            radius=zone["radius_km"] * 1000,   # folium Circle은 미터 단위
            color="blue",
            weight=1.5,
            fill=True,
            fill_opacity=0.05,
            popup=folium.Popup(f"<b>보호구역: {zone['name']}</b><br>반경 {zone['radius_km']}km", max_width=200),
        ).add_to(m)
        _label([zone["lat"], zone["lon"]], f"🛡 {zone['name']}", color="blue", bold=True, offset_y=18).add_to(m)

    plotted = 0
    for t in result["raw_tracks"]:
        if t["lat"] is None or t["lon"] is None:
            continue  # 위경도 결측치는 지도에 못 찍으므로 건너뜀
        a = assessment_by_id.get(t["track_id"])
        level = a["level"] if a else "LOW"
        score = a["score"] if a else "?"
        color = LEVEL_COLOR.get(level, "gray")

        zone_info = f"보호구역: {a['zone_name']}({a['zone_distance_km']}km)<br>" if (a and a.get("zone_name") and a.get("zone_distance_km", 999) <= 50) else ""
        popup_html = (
            f"<b>{t['label']}</b><br>"
            f"고도: {_fmt(t['altitude_m'], 'm')}<br>"
            f"속도: {_fmt(t['speed_ms'], 'm/s')}<br>"
            f"등급: {level} ({score}점)<br>"
            f"식별: {a.get('identity', 'UNKNOWN') if a else 'UNKNOWN'}<br>"
            f"{zone_info}"
        )
        folium.CircleMarker(
            location=[t["lat"], t["lon"]],
            radius=6,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            popup=folium.Popup(popup_html, max_width=250),
        ).add_to(m)
        # 상시 라벨: "{호출부호/식별} · {등급} {점수}점" - 클릭 안 해도 바로 보임
        # 마커 "아래쪽"에 배치 (예측 위치 라벨은 반대로 "위쪽"에 둬서 서로 겹치지 않게 분리)
        label_text = f"{t['label']} · {level} {score}점"
        _label([t["lat"], t["lon"]], label_text, color=color, bold=(level in ("HIGH", "CRITICAL")), offset_y=-18).add_to(m)
        plotted += 1

        # Phase 4: 예측 경로(점선) + 예상 위치 마커
        pred_lat = a.get("predicted_lat") if a else None
        pred_lon = a.get("predicted_lon") if a else None
        if pred_lat is not None and pred_lon is not None:
            minutes = a.get("prediction_minutes")
            folium.PolyLine(
                locations=[[t["lat"], t["lon"]], [pred_lat, pred_lon]],
                color=color,
                weight=2,
                opacity=0.7,
                dash_array="6,8",   # 점선 - 현재 위치와 구분되는 "예측"임을 시각적으로 표현
            ).add_to(m)

            pred_popup = f"<b>{t['label']} - {minutes:.0f}분 후 예상 위치</b>"
            predicted_zone = a.get("predicted_zone_name")
            if predicted_zone:
                pred_popup += f"<br>⚠ 보호구역 '{predicted_zone}' 진입 예상"
            folium.RegularPolygonMarker(
                location=[pred_lat, pred_lon],
                number_of_sides=3,   # 삼각형 = 화살표 느낌으로 "이동 방향 끝점"을 표시
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.5,
                popup=folium.Popup(pred_popup, max_width=250),
            ).add_to(m)

            # 상시 라벨: 구역 진입이 예상되면 빨간 경고문, 아니면 그냥 "N분 후"만 표시
            # 마커 "위쪽"에 배치 (현재 위치 라벨은 반대로 "아래쪽"에 있어서 서로 안 겹침)
            if predicted_zone:
                pred_label = f"⚠ {minutes:.0f}분 후 '{predicted_zone}' 진입 예상"
                _label([pred_lat, pred_lon], pred_label, color="red", bold=True, offset_y=18).add_to(m)
            else:
                _label([pred_lat, pred_lon], f"{minutes:.0f}분 후", color="#666", offset_y=18).add_to(m)

    _add_legend(m)
    logger.info("지도에 %d건 표시", plotted)
    return m


if __name__ == "__main__":
    result = run_pipeline()
    m = build_map(result)
    output_path = "threat_map.html"
    m.save(output_path)
    print(f"\n지도 저장 완료: {output_path}")
    print("파일을 더블클릭하면 브라우저에서 열립니다.")
