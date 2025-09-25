import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import re
import math
import pydeck as pdk

# ---------------------------------
# 1. 세션 상태 페이지 초기화 (필수!)
# ---------------------------------
if "page" not in st.session_state:
    st.session_state.page = "page1"

# ---------------------------------
# 2. 환경변수 로드 및 Supabase 클라이언트 생성
# ---------------------------------
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

# ---------------------------------
# 3. UI CSS 스타일 정의(간략)
# ---------------------------------
IMPROVED_UI_CSS = """
<style>
.stApp { background: #FFF; font-family: 'Pretendard', sans-serif; }
.info-box { background: #fff; border-radius: 10px; padding: 15px; margin-bottom: 15px;}
.info-box h3 { font-size: 1.1rem; font-weight: 600; margin-bottom: 10px; }
.info-box .keyword { color: #007bff; font-weight: 600;}
.stButton>button { width: 100%; }
.icon-button-container { display: flex; justify-content: flex-end;}
</style>
"""
st.markdown(IMPROVED_UI_CSS, unsafe_allow_html=True)

# ---------------------------------
# 4. 헬퍼 함수 및 상수 정의
# ---------------------------------
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식", "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식", "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
}
def norm_cat(name):
    return CATEGORY_ALIAS.get(name, name)

def _normalize_label(s):
    if s is None: return ""
    return re.sub(r"[\s/_\-()]+", "", str(s).lower())

def coerce_tf_bool(frame):
    for col in frame.columns:
        if frame[col].dtype is bool: continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE", "FALSE", "1", "0"]).mean() > 0.8:
                frame[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
    return frame

def resolve_tf_column(frame, expected_label):
    expected = norm_cat(expected_label)
    if expected in frame.columns: return expected
    want = _normalize_label(expected)
    normalized = {str(c): _normalize_label(str(c)) for c in frame.columns}
    for col, key in normalized.items():
        if key == want: return col
    for col, key in normalized.items():
        if want in key: return col
    return None

WX_GROUPS = {
    "클리어": [800], "구름": [801,802,803,804], "비": [500,501,502,503,504,511,520,521,522,531],
    "이슬비": [300,301,302,310,311,312,313,314,321],
    "뇌우": [200,201,202,210,211,212,221,230,231,232],
    "눈": [600,601,602,611,612,613,615,616,620,621,622],
    "분위기": [701,711,721,731,741,751,761,762],
}
WX_RECO = {
    "클리어": {"mood": "야외활동, 기분전환", "cats": ["이국적인 음식","디저트/카페","술 한잔 하기 좋은 날","가볍게 간단히","시원한 음식","해산물/생선요리"]},
    "구름": {"mood": "실내 중심, 편안함, 든든함 추구", "cats": ["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 한끼","해산물/생선요리"]},
    "비": {"mood": "외출 불편, 따뜻하거나 자극적인 음식", "cats": ["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은 날","패스트푸드/배달","시원한 한끼"]},
    "이슬비": {"mood": "정적이거나 가벼운 공간", "cats": ["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내고정", "cats": ["육류구이/고기파티","든든한 한끼","패스트푸드/배달"]},
    "눈": {"mood": "감성적, 따뜻함 추구", "cats": ["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"]},
    "분위기": {"mood": "안개/먼지 등, 건강 고려", "cats": ["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"]},
}
def weather_group_from_id(weather_id):
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes: return group_name
    return "구름"

def recommended_categories_from_group(group_name):
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return cats, mood

@st.cache_data(ttl=600)
def fetch_weather(lat, lon):
    if not OPENWEATHER_API_KEY: raise RuntimeError("OpenWeather API 키가 설정되지 않았습니다.")
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {"id": data["weather"][0]["id"], "description": data["weather"][0]["description"], "temperature": data["main"]["temp"]}

@st.cache_data(ttl=600)
def get_restaurant_within_500m_from_supabase(lat, lon):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not response or not response.data: return pd.DataFrame()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다. ({e})")
        return pd.DataFrame()

def filter_by_category_tf(frame, theme):
    if frame is None or frame.empty: return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col_name = resolve_tf_column(frame, theme)
    if not col_name: return pd.DataFrame()
    out = frame[frame[col_name] == True].copy()
    if "distance_m" in out.columns:
        out["distance_m"] = pd.to_numeric(out["distance_m"], errors="coerce")
        out = out.sort_values("distance_m")
    return out

def select_and_filter_by_business_type(frame):
    if frame is None or frame.empty:
        st.info("조건에 맞는 주변 업소가 없습니다.")
        return pd.DataFrame(), []
    if "category" not in frame.columns: return frame, []
    cats_all = sorted([cat for cat in frame["category"].dropna().unique() if cat])
    if not cats_all: return frame, []
    selected_categories = st.multiselect("업태를 선택해 더 자세히 필터링하세요", options=cats_all)
    filtered = frame[frame["category"].isin(selected_categories)].copy() if selected_categories else frame
    if selected_categories:
        st.caption(f"선택된 업태: {', '.join(selected_categories)} (총 {len(filtered)}곳)")
    return filtered, selected_categories

def format_distance(val, unit="m"):
    try:
        if pd.isna(val): return ""
        dist = float(val)
        if unit == "km": return f"{dist/1000:.2f}km"
        return f"{int(dist)}m"
    except Exception: return ""

def render_paginated_clickable_name_table(frame, *, table_key, page_size=10):
    if frame is None or frame.empty:
        st.info("표시할 식당이 없습니다.")
        return pd.DataFrame()
    view_df = frame.sort_values(by="distance_m").copy()
    total_pages = max(1, math.ceil(len(view_df) / page_size))
    page = int(st.session_state.get(f"page_{table_key}", 1))
    page = max(1, min(page, total_pages))
    col1, col2, col3 = st.columns([1, 2, 1])
    if col1.button("◀ 이전", key=f"{table_key}_prev", disabled=(page <= 1)): page -= 1
    if col3.button("다음 ▶", key=f"{table_key}_next", disabled=(page >= total_pages)): page += 1
    st.session_state[f"page_{table_key}"] = page
    col2.markdown(f"<div style='text-align:center; padding-top: 8px;'>Page <b>{page}</b> / {total_pages}</div>", unsafe_allow_html=True)
    start_idx = (page - 1) * page_size
    page_df = view_df.iloc[start_idx : start_idx + page_size]
    rows_html = []
    for _, row in page_df.iterrows():
        name = row.get("name", "이름 없음")
        link = row.get("map_link")
        dist = format_distance(row.get("distance_m"), "m")
        name_html = f'<a href="{link}" target="_blank">{name}</a>' if pd.notna(link) else name
        rows_html.append(f"<tr><td>{name_html}</td><td>{dist}</td></tr>")
    table_html = f"""<div style="border:1px solid #e6e6e6; border-radius:8px; overflow:hidden; margin-top: 10px;">
    <table style="width:100%; border-collapse:collapse; font-size:14px">
    <thead style="background: #fafafa;"><tr><th>이름</th><th>거리</th></tr></thead>
    <tbody>{''.join(rows_html)}</tbody></table></div>"""
    st.markdown(table_html, unsafe_allow_html=True)
    return page_df

def render_map_with_markers(user_lat, user_lon, frame):
    if frame is None or frame.empty or "latitude" not in frame.columns or "longitude" not in frame.columns:
        return
    df_map = frame.dropna(subset=["latitude", "longitude"]).copy()
    df_map["latitude"] = pd.to_numeric(df_map["latitude"], errors="coerce")
    df_map["longitude"] = pd.to_numeric(df_map["longitude"], errors="coerce")
    df_map.dropna(subset=["latitude", "longitude"], inplace=True)
    if df_map.empty: return
    center_lat, center_lon = df_map["latitude"].mean(), df_map["longitude"].mean()
    lat_span = df_map["latitude"].max() - df_map["latitude"].min()
    lon_span = df_map["longitude"].max() - df_map["longitude"].min()
    zoom = 14 - math.log(max(lat_span, lon_span, 0.005) * 100)
    st.pydeck_chart(pdk.Deck(
        map_style="mapbox://styles/mapbox/light-v10",
        initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=zoom, pitch=50),
        layers=[
            pdk.Layer("ScatterplotLayer", data=df_map, get_position='[longitude, latitude]', get_color='[200, 30, 0, 160]', get_radius=50),
            pdk.Layer("ScatterplotLayer", data=pd.DataFrame([{"latitude": user_lat, "longitude": user_lon}]), get_position='[longitude, latitude]', get_color='[0, 100, 255, 220]', get_radius=80),
        ],
        tooltip={"text": "{name}\n{address}"}
    ))

# ---------------------------------
# 5. 메인 실행 함수
# ---------------------------------
def main():
    st.set_page_config(page_title="날씨 + 위치 기반 음식점 추천", page_icon="🍜", layout="wide")
    st.title("날씨 + 위치 기반 음식점 추천 🌨️")
    user_lat, user_lon = get_user_location()
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except Exception as e:
        st.error(f"날씨 정보를 불러오는 데 실패했습니다: {e}")
        w = {"description": "정보 없음", "temperature": "N/A"}
        group_name, opts, mood = "구름", ["가볍게 간단히", "든든한 한끼"], "실내 중심, 편안함"
    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    with st.sidebar:
        st.markdown(f"""
        <div class="info-box">
            <h3>📍 현재 위치</h3>
            <p>위도: {user_lat:.4f}, 경도: {user_lon:.4f}</p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"""
        <div class="info-box">
            <h3>🌤️ 현재 날씨</h3>
            <p><b>{w.get('description', 'N/A')}</b></p>
            <p>기온: {w.get('temperature', 'N/A')}°C</p>
        </div>
        """, unsafe_allow_html=True)
        mood_tags = [tag.strip() for tag in mood.split(',')]
        keywords_html = "".join([f'<p class="keyword"># {tag}</p>' for tag in mood_tags])
        st.markdown(f"""
        <div class="info-box">
            <h3>💡 추천 키워드</h3>
            {keywords_html}
        </div>
        """, unsafe_allow_html=True)

    # ------------------- 페이지 컨트롤 ---------------------
if st.session_state.page == "page1":
    st.subheader("지금 날씨에 어울리는 음식 카테고리")
    choice = st.radio("선택해 주세요 👇", options=opts, horizontal=True)
    wx_df = filter_by_category_tf(all_df, choice)

    st.write("해당 카테고리에 해당되는 반경 500M 내 음식점 입니다.")
    if wx_df is not None and not wx_df.empty:
        st.dataframe(wx_df[["name", "distance_m"]].rename(columns={"name":"이름", "distance_m":"거리"}))
    else:
        st.warning("해당 카테고리 음식점이 없습니다.")

    # 항상 '다음' 버튼이 보여야 함
    if st.button("다음"):
        st.session_state.choice = choice
        st.session_state.page = "page2"
        st.rerun()

elif st.session_state.page == "page2":
    st.subheader("업태 선택")
    choice = st.session_state.get("choice", None)
    if choice is None:
        st.warning("Page1에서 먼저 선택해 주세요.")
        if st.button("이전"):
            st.session_state.page = "page1"
            st.rerun()
        st.stop()
    # 이어서 업태 선택 등 추가
            
        wx_df = filter_by_category_tf(all_df, choice)

        final_filtered_df, _ = select_and_filter_by_business_type(wx_df) if not wx_df.empty else (pd.DataFrame(), [])
        if st.button("다음"):
            st.session_state.filtered = final_filtered_df
            st.session_state.page = "page3"
            st.rerun()
        if st.button("이전"):
            st.session_state.page = "page1"
            st.rerun()
    elif st.session_state.page == "page3":
        st.subheader("결과 확인")
        final_filtered_df = st.session_state.get("filtered", pd.DataFrame())
        if final_filtered_df.empty:
            st.warning("Page2에서 먼저 필터링해 주세요.")
        else:
            st.dataframe(final_filtered_df)
        if st.button("이전"):
            st.session_state.page = "page2"
            st.rerun()

# 6. 실행
main()
