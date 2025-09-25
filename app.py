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
import pydeck as pdk
from typing import Tuple, List
import math

# -----------------------------
# 0. 기본 설정
# -----------------------------
st.set_page_config(page_title="날씨+위치 기반 음식점 추천", page_icon="🍜", layout="wide")

load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

# -----------------------------
# 1. 위치 가져오기
# -----------------------------
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

# -----------------------------
# 2. 전처리 함수
# -----------------------------
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
}

def norm_cat(name: str) -> str:
    return CATEGORY_ALIAS.get(name, name)

def _normalize_label(s: str) -> str:
    if s is None:
        return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\-()]+", "", s)

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype is bool:
            continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE", "FALSE", "1", "0", "", "NAN"]).mean() > 0.8:
                frame[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
    return frame

def resolve_tf_column(frame: pd.DataFrame, expected_label: str) -> str | None:
    expected = norm_cat(expected_label)
    if expected in frame.columns:
        return expected
    want = _normalize_label(expected)
    normalized = {str(c): _normalize_label(str(c)) for c in frame.columns}
    for col, key in normalized.items():
        if key == want:
            return col
    for col, key in normalized.items():
        if want in key:
            return col
    return None

# -----------------------------
# 3. 날씨 그룹 정의
# -----------------------------
WX_GROUPS = {
    "클리어": [800],
    "구름": [801, 802, 803, 804],
    "비": [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우": [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈": [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "분위기": [701, 711, 721, 731, 741, 751, 761, 762],
}

WX_RECO = {
    "클리어": {"mood": "야외활동, 기분전환, 걷기 좋은 날",
               "cats": ["이국적인 음식", "디저트/카페", "술 한잔 하기 좋은 날", "가볍게 간단히", "시원한 음식", "해산물/생선요리"]},
    "구름": {"mood": "실내 중심, 무거운 분위기로 인한 편안함, 든든함 추구",
             "cats": ["든든한 한끼", "뜨끈한 국물", "디저트/카페", "시원한 한끼", "해산물/생선요리"]},
    "비": {"mood": "외출 불편, 따뜻하거나 자극적인 음식",
           "cats": ["뜨끈한 국물", "매콤한 음식", "술 한잔 하기 좋은 날", "패스트푸드/배달", "시원한 한끼"]},
    "이슬비": {"mood": "활동 가능하지만 귀찮음, 정적이거나 가벼운 공간",
              "cats": ["디저트/카페", "가볍게 간단히", "건강/채식/특수식단", "해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내고정",
            "cats": ["육류구이/고기파티", "든든한 한끼", "패스트푸드/배달"]},
    "눈": {"mood": "실내, 정적인 장소, 감성적, 따뜻함 추구",
           "cats": ["뜨끈한 국물", "육류구이/고기파티", "가족/단체회식", "디저트/카페", "해산물/생선요리"]},
    "분위기": {"mood": "안개/먼지 등: 건강 고려, 따뜻한 국물, 배달 선호",
              "cats": ["건강/채식/특수식단", "뜨끈한 국물", "패스트푸드/배달"]},
}

def weather_group_from_id(weather_id: int) -> str:
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"

def recommended_categories_from_group(group_name: str, top_k: int | None = None):
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return (cats, mood) if top_k is None else (cats[:top_k], mood)

# -----------------------------
# 4. 외부 API
# -----------------------------
def fetch_weather(weather_lat: float, weather_lon: float) -> dict:
    if not OPENWEATHER_API_KEY:
        raise RuntimeError("OpenWeather API 키가 설정되지 않았습니다.")
    weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={weather_lat}&lon={weather_lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(weather_url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {"id": data["weather"][0]["id"],
            "description": data["weather"][0]["description"],
            "temperature": data["main"]["temp"]}

def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m",
                                {"user_lat": lat, "user_lng": lon}).execute()
        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다. ({e})")
        return pd.DataFrame()

# -----------------------------
# 5. 필터링 함수
# -----------------------------
def filter_by_weather_via_categories(frame: pd.DataFrame, group_name: str,
                                     use_all_cats: bool = True, top_k: int = 3) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    cats_all = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    cats = cats_all if use_all_cats else cats_all[:top_k]
    cols = [resolve_tf_column(frame, c) for c in cats]
    cols = [c for c in cols if c]
    if not cols:
        return pd.DataFrame()
    mask = False
    for col_name in cols:
        mask = mask | (frame[col_name] == True)
    return frame[mask].copy()

def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col_name = resolve_tf_column(frame, theme)
    if not col_name:
        return pd.DataFrame()
    out = frame[frame[col_name] == True].copy()
    for order_col in ["distance_m", "distance", "dist_m", "distance_km"]:
        if order_col in out.columns:
            if order_col == "distance_km":
                out["distance_m"] = (pd.to_numeric(out[order_col], errors="coerce") * 1000).round(0).astype("Int64")
                out = out.sort_values("distance_m")
            else:
                out[order_col] = pd.to_numeric(out[order_col], errors="coerce")
                out = out.sort_values(order_col)
            break
    return out

def select_and_filter_by_business_type(frame: pd.DataFrame, group_name: str, choice: str,
                                       multiselect_label: str = "업태를 선택하세요 (복수 선택 가능)") -> Tuple[pd.DataFrame, List[str]]:
    if frame is None or frame.empty:
        return pd.DataFrame(), []
    selected_categories: List[str] = []
    if "category" not in frame.columns:
        return frame, selected_categories
    cats_all = frame["category"].dropna().astype(str).unique().tolist()
    selected_categories = st.multiselect(multiselect_label, options=sorted(cats_all), default=[])
    filtered = frame[frame["category"].isin(selected_categories)].copy() if selected_categories else frame
    return filtered, selected_categories

# -----------------------------
# 6. UI 관련 함수
# -----------------------------
def render_paginated_clickable_name_table(frame: pd.DataFrame, *, table_key: str, page_size: int = 10) -> pd.DataFrame:
    if frame is None or frame.empty:
        st.info("표시할 식당이 없습니다.")
        return pd.DataFrame()
    view_df = frame.copy()
    if "distance_m" in view_df.columns:
        view_df["distance_m"] = pd.to_numeric(view_df["distance_m"], errors="coerce")
        view_df = view_df.sort_values("distance_m")

    total = len(view_df)
    total_pages = max(1, math.ceil(total / page_size))
    page = int(st.session_state.get(table_key, 1))
    if st.button("◀ 이전", disabled=(page <= 1), key=f"{table_key}_prev"):
        page -= 1
    if st.button("다음 ▶", disabled=(page >= total_pages), key=f"{table_key}_next"):
        page += 1
    st.session_state[table_key] = page
    start, end = (page - 1) * page_size, (page - 1) * page_size + page_size
    page_df = view_df.iloc[start:end]
    st.dataframe(page_df[["name", "distance_m"]] if "distance_m" in page_df.columns else page_df)
    return page_df

def pick_one_restaurant(frame: pd.DataFrame) -> Tuple[pd.DataFrame | None, str]:
    if frame is None or frame.empty:
        return None, ""
    options = list(frame.index)
    picked_idx = st.selectbox("아래에서 식당을 선택하세요", options=options, format_func=lambda i: frame.loc[i, "name"])
    picked_row = frame.loc[[picked_idx]].copy()
    return picked_row, frame.loc[picked_idx, "name"]

# -----------------------------
# 7. 메인 실행부 (3단계 페이지)
# -----------------------------
if "page" not in st.session_state:
    st.session_state.page = "page1"

def main():
    user_lat, user_lon = get_user_location()
    w, group_name, opts, mood = {}, "구름", ["한식"], "편안함"
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except Exception as e:
        st.error(f"날씨 정보를 불러오는 데 실패했습니다: {e}")

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # --- Page 1 ---
    if st.session_state.page == "page1":
        st.header("날씨 + 위치 기반 음식점 추천 🌨️")
        choice = st.radio("현재 날씨에 추천 드리는 카테고리 입니다. 선택해 주세요!", options=opts, horizontal=False, key="choice")
        wx_df = filter_by_category_tf(all_df, choice)
        st.write("해당 카테고리에 해당되는 반경 500M 내 음식점 입니다.")
        st.dataframe(wx_df[["name", "distance_m"]]) if not wx_df.empty else st.warning("없음")
        if st.button("다음 ➡"):
            st.session_state.choice = choice
            st.session_state.page = "page2"
            st.rerun()

    # --- Page 2 ---
    elif st.session_state.page == "page2":
        choice = st.session_state.get("choice")
        wx_df = filter_by_category_tf(all_df, choice)
        st.header("카테고리에 해당하는 식당입니다.")
        filtered_df, _ = select_and_filter_by_business_type(wx_df, group_name, choice)
        tab1, tab2 = st.tabs(["거리순", "지도뷰"])
        with tab1:
            _ = render_paginated_clickable_name_table(filtered_df, table_key="dist", page_size=10)
        with tab2:
            st.map(filtered_df.rename(columns({"latitude": "lat", "longitude": "lon"})))
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⬅ 이전"):
                st.session_state.page = "page1"
                st.rerun()
        with col2:
            if st.button("다음 ➡"):
                st.session_state.filtered = filtered_df
                st.session_state.page = "page3"
                st.rerun()

    # --- Page 3 ---
    elif st.session_state.page == "page3":
        st.header("결과 확인")
        final_df = st.session_state.get("filtered", pd.DataFrame())
        picked_df, picked_label = pick_one_restaurant(final_df)
        st.image("https://via.placeholder.com/600x300.png?text=음식점+링크+이미지")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("조금 더 둘러볼래요!"):
                st.session_state.page = "page2"
                st.rerun()
        with col2:
            if st.button("맛집을 정했어요!"):
                st.success(f"🎉 선택한 곳: {picked_label}")

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()

