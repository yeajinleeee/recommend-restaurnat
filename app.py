import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import re
import pydeck as pdk
from typing import Tuple, List
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
# 1. 기본값 / 유틸
# ───────────────────────────────
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

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
    if s is None: return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\\-()]+", "", s)

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype is bool:
            continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE","FALSE","1","0","","NAN"]).mean() > 0.8:
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
    "이슬비": {"mood": "활동 가능하지만 귀찮음, 정적이거나 가벼운 공간",
               "cats": ["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내 고정",
             "cats": ["육류구이/고기파티","든든한 한끼","패스트푸드/배달"]},
    "눈": {"mood": "실내, 정적인 장소, 감성적, 따뜻함 추구",
           "cats": ["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"]},
    "분위기": {"mood": "안개/먼지 등 건강 고려, 따뜻한 국물, 배달 선호",
              "cats": ["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"]},
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

# ───────────────────────────────
# 3. API
# ───────────────────────────────
def fetch_weather(weather_lat: float, weather_lon: float) -> dict:
    if not OPENWEATHER_API_KEY:
        raise RuntimeError("OpenWeather API 키가 설정되지 않았습니다.")
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
# 4. 필터 함수
# ───────────────────────────────
def filter_by_weather_via_categories(frame: pd.DataFrame, group_name: str, use_all_cats=True, top_k=3) -> pd.DataFrame:
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
    for col in cols:
        mask = mask | (frame[col] == True)
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
                out = out.sort_values(order_col)
                out["distance_m"] = (pd.to_numeric(out[order_col], errors="coerce")*1000).round(0).astype("Int64")
            else:
                out[order_col] = pd.to_numeric(out[order_col], errors="coerce")
                out = out.sort_values(order_col)
            break
    return out

# ───────────────────────────────
# 5. UI Helper (표)
# ───────────────────────────────
def detect_df_col(frame: pd.DataFrame, candidates, fuzzy=()):
    for cand in candidates:
        if cand in frame.columns:
            return cand
    low_cols = {col.lower(): col for col in frame.columns}
    for substr in fuzzy:
        s = str(substr).lower()
        for low_name, original in low_cols.items():
            if s in low_name:
                return original
    return None

def format_distance(value, colname: str | None) -> str:
    if value is None or (hasattr(pd, "isna") and pd.isna(value)):
        return ""
    try:
        d = float(value)
    except Exception:
        return str(value)
    if colname and "km" in str(colname).lower():
        return f"{d:.2f}km"
    return f"{int(round(d))}m"

def render_paginated_clickable_name_table(frame: pd.DataFrame, *, table_key: str, page_size: int = 10) -> pd.DataFrame:
    if frame is None or frame.empty:
        st.info("표시할 식당이 없습니다.")
        return pd.DataFrame()
    view_df = frame.copy()
    for order_col in ("distance_m", "distance", "dist_m", "distance_km"):
        if order_col in view_df.columns:
            view_df[order_col] = pd.to_numeric(view_df[order_col], errors="coerce")
            view_df = view_df.sort_values(order_col)
            break
    total = len(view_df)
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
    page_df = view_df.iloc[start:end].copy()
    name_col = detect_df_col(page_df, ["name","place_name","상호명","store_name"], fuzzy=("name","상호","place"))
    dist_col = detect_df_col(page_df, ["distance_m","distance","dist_m","distance_km"], fuzzy=("dist","거리"))
    show_cols = []
    if name_col: show_cols.append(name_col)
    if dist_col: show_cols.append(dist_col)
    if show_cols:
        st.dataframe(page_df[show_cols])
    else:
        st.dataframe(page_df)
    return page_df

# ───────────────────────────────
# 6. Main
# ───────────────────────────────
def main():
    user_lat, user_lon = get_user_location()
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except Exception as e:
        st.error(f"날씨 정보를 불러오는 데 실패했습니다: {e}")
        w = {"description":"알수없음","temperature":"?"}
        group_name, opts, mood = "구름", ["가볍게 간단히","든든한 한끼","디저트/카페"], "실내 중심"
    
    # ── 사이드바
    with st.sidebar:
        st.markdown("### 📍 현재 위치")
        st.write(f"위도: {user_lat:.4f}, 경도: {user_lon:.4f}")
        st.markdown("### 🌤️ 현재 날씨")
        st.write(f"{w['description']}, {w['temperature']}°C")
        st.markdown("### 💡 추천 키워드")
        for tag in mood.split(","):
            st.write(f"#{tag.strip()}")

    # ── 본문
    st.header("날씨 + 위치 기반 음식점 추천 🌨️")
    choice = st.radio("현재 날씨에 추천 드리는 카테고리입니다. 선택해 주세요!", options=opts)

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)
    st.caption(f"반경 500m 이내 음식점 목록 (총 {len(all_df)}개)")
    render_paginated_clickable_name_table(all_df, table_key="all_df")

    wx_df = filter_by_weather_via_categories(all_df, group_name)
    filtered_df = filter_by_category_tf(wx_df, choice)
    st.subheader(f"‘{group_name}’ 날씨 + ‘{choice}’ 카테고리 결과")
    render_paginated_clickable_name_table(filtered_df, table_key=f"filtered_{group_name}_{choice}")

if __name__ == "__main__":
    main()
