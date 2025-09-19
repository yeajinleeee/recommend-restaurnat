# ---------------------------------
# 1. 라이브러리 임포트
# ---------------------------------
import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests
import psycopg2
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import re
import random
import pydeck as pdk
from typing import Tuple, List
import math

# ---------------------------------
# 2. 초기 설정 및 환경 변수 로드
# ---------------------------------
# .env 파일에서 환경 변수를 로드합니다.
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

# Streamlit 페이지의 기본 설정을 구성합니다.
st.set_page_config(
    page_title="날씨 + 위치 기반 음식점 추천",
    page_icon="🍜",
    layout="wide",
)

# ---------------------------------
# 3. UI 디자인을 위한 CSS 스타일
# ---------------------------------
IMPROVED_UI_CSS = """
<style>
    /* 전체 페이지 배경 및 기본 폰트 설정 */
    .stApp { background-color: #FFFFFF; font-family: 'Pretendard', sans-serif; }

    /* 제목 전체를 감싸는 컨테이너 스타일 */
    .title-container {
        background-color: #f0f2f5;
        padding: 1rem 1.5rem;
        border-bottom: 3px solid #e57373;
        margin-bottom: 2rem;
        display: flex;
        align-items: center;
        gap: 15px;
    }
    .title-container h1 {
        font-size: 1.8rem;
        font-weight: 700;
        color: #333;
        margin: 0;
        padding: 0;
    }
    .title-container .icon {
        font-size: 1.8rem;
    }

    /* 좌측 정보 패널의 각 정보 박스 스타일 */
    .info-box {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        border-radius: 10px;
        padding: 15px;
        margin-bottom: 15px;
    }
    .info-box h3 {
        font-size: 1.1rem;
        font-weight: 600;
        margin-bottom: 10px;
        padding-bottom: 5px;
        border-bottom: 1px solid #ddd;
    }
    .info-box p, .info-box .keyword { margin: 0; font-size: 0.95rem; }
    .info-box .keyword { color: #007bff; font-weight: 600; padding-top: 5px; }

    /* 아이콘 버튼 스타일 */
    .stButton>button {
        width: 100%;
    }
    .icon-button-container {
        display: flex;
        justify-content: flex-end;
        align-items: center;
    }
    .icon-button-container button {
        width: 38px !important;
        height: 38px !important;
        padding: 0 !important;
        border: 1px solid #DCDCDC !important;
        border-radius: 5px !important;
        background-color: #FAFAFA !important;
        font-size: 20px;
        line-height: 1;
    }
    .icon-button-container button:hover {
        border-color: #A0A0A0 !important;
        background-color: #F0F0F0 !important;
    }
</style>
"""
st.markdown(IMPROVED_UI_CSS, unsafe_allow_html=True)

# ---------------------------------
# 4. 헬퍼 함수 및 상수 정의
# ---------------------------------

# 위치 정보를 가져올 수 없을 때 사용할 기본 위치 (서울 시청)
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    """브라우저의 Geolocation API를 사용해 사용자 위치를 요청하고, 실패 시 기본 위치를 반환합니다."""
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

# 카테고리 이름의 미세한 차이를 통일하기 위한 별칭 맵
CATEGORY_ALIAS = { "시원한 한끼": "시원한 음식", "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날", "가족/단체회식": "가족/단체 외식", "패스트푸드/배달": "패스트푸드", "헤산물/생선요리": "해산물/생선요리",}

def norm_cat(name: str) -> str:
    """카테고리 이름을 CATEGORY_ALIAS를 사용해 표준화합니다."""
    return CATEGORY_ALIAS.get(name, name)

def _normalize_label(s: str) -> str:
    """문자열에서 공백, 특수문자를 제거하고 소문자로 변환하여 비교하기 쉬운 형태로 만듭니다."""
    if s is None: return ""
    return re.sub(r"[\s/_\-()]+", "", str(s).lower())

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    """DataFrame에서 'TRUE', 'FALSE' 같은 문자열 컬럼을 실제 boolean 타입으로 변환합니다."""
    for col in frame.columns:
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE","FALSE","1","0","", "NAN"]).mean() > 0.8:
                frame[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
    return frame

def resolve_tf_column(frame: pd.DataFrame, expected_label: str) -> str | None:
    """DataFrame에서 약간 다른 이름의 컬럼을 찾아 반환합니다."""
    expected = norm_cat(expected_label)
    if expected in frame.columns: return expected
    want = _normalize_label(expected)
    normalized = {str(c): _normalize_label(str(c)) for c in frame.columns}
    for col, key in normalized.items():
        if key == want: return col
    for col, key in normalized.items():
        if want in key: return col
    return None

# 날씨 ID를 그룹으로 매핑
WX_GROUPS = { "클리어": [800], "구름": [801, 802, 803, 804], "비": [500, 501, 502, 503, 504, 511, 520, 521, 522, 531], "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321], "뇌우": [200, 201, 202, 210, 211, 212, 221, 230, 231, 232], "눈": [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622], "분위기": [701, 711, 721, 731, 741, 751, 761, 762],}

# 날씨 그룹별 추천 정보
WX_RECO = { "클리어": {"mood": "야외활동, 기분전환", "cats": ["이국적인 음식","디저트/카페","술 한잔 하기 좋은 날","가볍게 간단히","시원한 음식","해산물/생선요리"]}, "구름": {"mood": "실내 중심, 편안함, 든든함 추구", "cats": ["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 한끼","해산물/생선요리"]}, "비": {"mood": "외출 불편, 따뜻하거나 자극적인 음식", "cats": ["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은 날","패스트푸드/배달","시원한 한끼"]}, "이슬비": {"mood": "정적이거나 가벼운 공간", "cats": ["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"]}, "뇌우": {"mood": "외출 최소화, 실내고정", "cats": ["육류구이/고기파티","든든한 한끼","패스트푸드/배달"]}, "눈": {"mood": "감성적, 따뜻함 추구", "cats": ["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"]}, "분위기": {"mood": "안개/먼지 등, 건강 고려", "cats": ["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"]},}

def weather_group_from_id(weather_id: int) -> str:
    """날씨 ID를 내부 그룹명으로 변환합니다."""
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes: return group_name
    return "구름"

def recommended_categories_from_group(group_name: str):
    """날씨 그룹명으로 추천 카테고리와 분위기를 반환합니다."""
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return cats, mood

@st.cache_data(ttl=600)
def fetch_weather(lat: float, lon: float) -> dict:
    """OpenWeatherMap API로 날씨 정보를 가져옵니다."""
    if not OPENWEATHER_API_KEY: raise RuntimeError("OpenWeather API 키가 설정되지 않았습니다.")
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return { "id": data["weather"][0]["id"], "description": data["weather"][0]["description"], "temperature": data["main"]["temp"]}

@st.cache_data(ttl=600)
def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    """Supabase DB에서 반경 500m 내 식당 목록을 가져옵니다."""
    try:
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not response or not response.data: return pd.DataFrame()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다. ({e})")
        return pd.DataFrame()

def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    """선택한 카테고리로 식당을 필터링하고 거리순으로 정렬합니다."""
    if frame is None or frame.empty: return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col_name = resolve_tf_column(frame, theme)
    if not col_name: return pd.DataFrame()
    out = frame[frame[col_name] == True].copy()
    if "distance_m" in out.columns:
        out["distance_m"] = pd.to_numeric(out["distance_m"], errors="coerce")
        out = out.sort_values("distance_m")
    return out

def select_and_filter_by_business_type(frame: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """multiselect UI로 사용자가 선택한 업태로 추가 필터링합니다."""
    if frame is None or frame.empty: return pd.DataFrame(), []
    if "category" not in frame.columns: return frame, []
    
    cats_all = sorted([cat for cat in frame["category"].dropna().unique() if cat])
    if not cats_all: return frame, []
    
    selected_categories = st.multiselect("업태를 선택해 더 자세히 필터링하세요", options=cats_all)
    
    if selected_categories:
        filtered = frame[frame["category"].isin(selected_categories)].copy()
        st.caption(f"선택된 업태: {', '.join(selected_categories)} (총 {len(filtered)}곳)")
        return filtered, selected_categories
    return frame, []

def format_distance(value, colname: str | None) -> str:
    """거리 값을 '123m' 형식의 문자열로 변환합니다."""
    if pd.isna(value): return ""
    try: d = float(value)
    except: return str(value)
    if colname and "km" in str(colname).lower(): return f"{d:.2f}km"
    return f"{int(round(d))}m"

def render_paginated_clickable_name_table(frame: pd.DataFrame, *, table_key: str, page_size: int = 10) -> pd.DataFrame:
    """페이지네이션 기능이 있는 클릭 가능한 HTML 테이블을 렌더링합니다."""
    if frame is None or frame.empty:
        st.info("표시할 식당이 없습니다.")
        return pd.DataFrame()
    view_df = frame.sort_values(by="distance_m").copy()
    total_pages = max(1, math.ceil(len(view_df) / page_size))
    page = int(st.session_state.get(f"page_{table_key}", 1))
    page = max(1, min(page, total_pages))
    
    col1, col2, col3 = st.columns([1, 2, 1])
    if col1.button("◀ 이전", key=f"{table_key}_prev", disabled=(page <= 1)): page -= 1
    if col3.button("다음 ▶", key=f"{table_key}_next", disabled=(page >= total_pages), use_container_width=True): page += 1
    st.session_state[f"page_{table_key}"] = page
    col2.markdown(f"<div style='text-align:center; padding-top: 8px;'>Page <b>{page}</b> / {total_pages}</div>", unsafe_allow_html=True)

    start_idx = (page - 1) * page_size
    page_df = view_df.iloc[start_idx : start_idx + page_size]
    
    rows_html = []
    for _, row in page_df.iterrows():
        name, link, dist = row.get("name", ""), row.get("map_link"), format_distance(row.get("distance_m"), "m")
        name_html = f'<a href="{link}" target="_blank">{name}</a>' if pd.notna(link) else name
        rows_html.append(f"<tr><td>{name_html}</td><td>{dist}</td></tr>")
    
    table_html = f"""<div style="border:1px solid #e6e6e6; border-radius:8px; overflow:hidden; margin-top: 10px;">
    <table style="width:100%; border-collapse:collapse; font-size:14px">
    <thead style="background: #fafafa;"><tr><th>이름</th><th>거리</th></tr></thead>
    <tbody>{''.join(rows_html)}</tbody></table></div>"""
    st.markdown(table_html, unsafe_allow_html=True)
    return page_df

def render_map_with_markers(user_lat: float, user_lon: float, frame: pd.DataFrame):
    """pydeck을 사용하여 지도에 현재 위치와 식당 위치를 마커로 표시합니다."""
    if frame is None or frame.empty or "latitude" not in frame.columns: return
    df_map = frame.dropna(subset=["latitude", "longitude"]).copy()
    if df_map.empty: return

    center_lat, center_lon = df_map["latitude"].mean(), df_map["longitude"].mean()
    lat_span = df_map["latitude"].max() - df_map["latitude"].min()
    zoom = 14 - math.log(max(lat_span, 0.005) * 100)

    st.pydeck_chart(pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=zoom, pitch=50),
        layers=[
            pdk.Layer("ScatterplotLayer", data=df_map, get_position='[longitude, latitude]', get_color='[200, 30, 0, 160]', get_radius=50, pickable=True),
            pdk.Layer("ScatterplotLayer", data=pd.DataFrame([{"latitude": user_lat, "longitude": user_lon}]), get_position='[longitude, latitude]', get_color='[0, 100, 255, 220]', get_radius=80),
        ],
        tooltip={"html": "<b>{name}</b><br/>{address}<br/>거리: {distance_m}m", "style": {"color": "white"}}
    ))

# ---------------------------------
# 5. 메인 애플리케이션 로직
# ---------------------------------
def main():
    """Streamlit 앱의 메인 실행 함수"""

    # --- 데이터 로드 (초기에 한 번만 실행) ---
    if 'user_lat' not in st.session_state or 'user_lon' not in st.session_state:
        st.session_state.user_lat, st.session_state.user_lon = get_user_location()
    user_lat, user_lon = st.session_state.user_lat, st.session_state.user_lon

    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except Exception as e:
        st.error(f"날씨 정보를 불러오는 데 실패했습니다: {e}")
        w, group_name, opts, mood = {"description": "N/A", "temperature": "N/A"}, "구름", [], "정보 없음"

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # --- UI 렌더링 ---
    
    # 1. 커스텀 헤더 출력
    st.markdown(f"""
    <div class="title-container">
        <h1>날씨 + 위치 기반 음식점 추천</h1>
        <span class="icon">{"🌨️" if "비" in group_name or "눈" in group_name else "☀️"}</span>
    </div>
    """, unsafe_allow_html=True)

    # 2. st.columns를 사용해 페이지를 좌우로 분할
    left_col, right_col = st.columns([1, 2.2])

    # --- 왼쪽 컬럼 (정보 패널) ---
    with left_col:
        # Box 1: 현재 위치
        st.markdown('<div class="info-box">', unsafe_allow_html=True)
        col1, col2 = st.columns([0.7, 0.3])
        with col1:
            st.markdown("<h3>📍 현재 위치</h3>", unsafe_allow_html=True)
            st.write(f"위도: {user_lat:.4f}, 경도: {user_lon:.4f}")
        with col2:
            st.markdown('<div class="icon-button-container">', unsafe_allow_html=True)
            if st.button("🎯", key="refresh_location"):
                del st.session_state.user_lat
                del st.session_state.user_lon
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # Box 2: 현재 날씨
        st.markdown(f"""
        <div class="info-box">
            <h3>🌤️ 현재 날씨</h3>
            <p><b>{w.get('description', 'N/A')}</b> | 기온: {w.get('temperature', 'N/A')}°C</p>
        </div>
        """, unsafe_allow_html=True)

        # Box 3: 추천 키워드
        mood_tags = [tag.strip() for tag in mood.split(',')]
        keywords_html = "".join([f'<p class="keyword"># {tag}</p>' for tag in mood_tags])
        st.markdown(f"""
        <div class="info-box">
            <h3>💡 추천 키워드</h3>
            {keywords_html}
        </div>
        """, unsafe_allow_html=True)

    # --- 오른쪽 컬럼 (메인 콘텐츠) ---
    with right_col:
        st.subheader("현재 날씨에 추천 드리는 카테고리입니다. 선택해주세요!")
        
        choice = st.radio("카테고리 선택", options=opts, horizontal=True, label_visibility="collapsed")
        
        filtered_df = filter_by_category_tf(all_df, choice)
        
        st.subheader(f"'{choice}' 카테고리에 해당되는 반경 500M 내 음식점 입니다.")
        
        final_df, _ = select_and_filter_by_business_type(filtered_df)
        page_df = render_paginated_clickable_name_table(final_df, table_key=f"filtered_{choice}")
        
        st.subheader("지도에서 결과 보기")
        render_map_with_markers(user_lat, user_lon, page_df)

if __name__ == "__main__":
    main()
