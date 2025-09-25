import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
import requests
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import re
import math

# ───────────────────────────────
# 0. 환경 설정
# ───────────────────────────────
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

st.set_page_config(page_title="날씨 + 위치 기반 음식점 추천", page_icon="🍜", layout="wide")

# ───────────────────────────────
# 1. 유틸
# ───────────────────────────────
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

def _normalize_label(s: str) -> str:
    if s is None: return ""
    s = str(s).lower()
    # ✅ 정규식 수정 (Python 3.13 호환)
    return re.sub(r"[\s/_\-\(\)]+", "", s)

# ───────────────────────────────
# 2. 날씨 그룹 & 추천 카테고리
# ───────────────────────────────
WX_GROUPS = {
    "클리어": [800],
    "구름":   [801, 802, 803, 804],
    "비":     [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우":   [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈":     [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "분위기": [701, 711, 721, 731, 741, 751, 761, 762],
}
WX_RECO = {
    "클리어": {"mood": "야외활동, 기분전환, 걷기 좋은 날",
               "cats": ["이국적인 음식","디저트/카페","술 한잔 하기 좋은 날","가볍게 간단히","시원한 음식","해산물/생선요리"]},
    "구름": {"mood": "실내 중심, 편안함, 든든함 추구",
             "cats": ["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 한끼","해산물/생선요리"]},
    "비": {"mood": "외출 불편, 따뜻하거나 자극적인 음식",
           "cats": ["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은 날","패스트푸드/배달","시원한 한끼"]},
    "이슬비": {"mood": "활동 가능하지만 귀찮음",
               "cats": ["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내 고정",
             "cats": ["육류구이/고기파티","든든한 한끼","패스트푸드/배달"]},
    "눈": {"mood": "실내, 감성적, 따뜻함 추구",
           "cats": ["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"]},
    "분위기": {"mood": "안개/먼지 등 건강 고려",
              "cats": ["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"]},
}

def weather_group_from_id(weather_id: int) -> str:
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"

def recommended_categories_from_group(group_name: str, top_k: int | None = None):
    cats = WX_RECO[group_name]["cats"]
    mood = WX_RECO[group_name]["mood"]
    return (cats, mood) if top_k is None else (cats[:top_k], mood)

# ───────────────────────────────
# 3. API
# ───────────────────────────────
def fetch_weather(weather_lat: float, weather_lon: float) -> dict:
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={weather_lat}&lon={weather_lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {
        "id": data["weather"][0]["id"],
        "description": data["weather"][0]["description"],
        "temperature": data["main"]["temp"]
    }

def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat, "user_lng": lon
        }).execute()
        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

# ───────────────────────────────
# 4. UI Helper (표)
# ───────────────────────────────
def render_paginated_table(frame: pd.DataFrame, *, table_key: str, page_size: int = 10) -> pd.DataFrame:
    if frame is None or frame.empty:
        st.info("표시할 식당이 없습니다.")
        return pd.DataFrame()
    total = len(frame)
    total_pages = max(1, math.ceil(total / page_size))
    page = int(st.session_state.get(table_key, 1))
    col1, col2 = st.columns([0.5,0.5])
    with col1:
        if st.button("◀ 이전", disabled=(page<=1), key=f"{table_key}_prev"):
            page -= 1
    with col2:
        if st.button("다음 ▶", disabled=(page>=total_pages), key=f"{table_key}_next"):
            page += 1
    st.session_state[table_key] = page
    start, end = (page-1)*page_size, (page-1)*page_size+page_size
    page_df = frame.iloc[start:end].copy()
    st.dataframe(page_df)  # ✅ 스크롤 형태
    return page_df

# ───────────────────────────────
# 5. Main (Page1 → Page2 → Page3)
# ───────────────────────────────
def main():
    if "page" not in st.session_state:
        st.session_state.page = "page1"

    user_lat, user_lon = get_user_location()
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except:
        w = {"description":"알수없음","temperature":"?"}
        group_name, opts, mood = "구름", ["가볍게 간단히","든든한 한끼","디저트/카페"], "실내 중심"

    # 사이드바 (박스 스타일)
    with st.sidebar:
        st.markdown(f"""
        <div style="background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;">
            <h3>📍 현재 위치</h3>
            <p>위도: {user_lat:.4f}, 경도: {user_lon:.4f}</p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"""
        <div style="background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;">
            <h3>🌤️ 현재 날씨</h3>
            <p>{w['description']}, {w['temperature']}°C</p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"""
        <div style="background:#fff; border-radius:10px; padding:15px;">
            <h3>💡 추천 키워드</h3>
            <p>{mood}</p>
        </div>
        """, unsafe_allow_html=True)

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # Page 분기
    if st.session_state.page == "page1":
        st.header("Step 1️⃣ 카테고리 선택")
        choice = st.radio("현재 날씨에 추천 드리는 카테고리입니다", options=opts)
        if st.button("다음 ➡"):
            st.session_state.choice = choice
            st.session_state.page = "page2"
            st.rerun()

    elif st.session_state.page == "page2":
        st.header("Step 2️⃣ 후보 식당 확인")
        wx_df = all_df  # 여기서는 단순히 전체에서 보여줌
        filtered_df = wx_df  # 추후 filter_by_category_tf 적용 가능
        st.write(f"총 {len(filtered_df)}곳")
        page_df = render_paginated_table(filtered_df, table_key="page2_table")
        if st.button("⬅ 이전"):
            st.session_state.page = "page1"
            st.rerun()
        if st.button("다음 ➡"):
            st.session_state.page = "page3"
            st.rerun()

    elif st.session_state.page == "page3":
        st.header("Step 3️⃣ 최종 선택")
        st.write("여기서 하나를 최종적으로 고르는 로직 추가")
        if st.button("⬅ 이전"):
            st.session_state.page = "page2"
            st.rerun()

if __name__ == "__main__":
    main()
