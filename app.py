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

# ───────────────────────────────
# 0. 환경 변수 & supabase
# ───────────────────────────────
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.title("날씨 + 위치 기반 음식점 추천🌨️")

# ───────────────────────────────
# 1. 위치
# ───────────────────────────────
seoul_lat, seoul_lon = 37.5665, 126.9780
def get_user_location():
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

# ───────────────────────────────
# 2. 카테고리 매핑 & 정규화
# ───────────────────────────────
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
    return re.sub(r"[\s/_\-()]+", "", s)

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype is bool:
            continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE","FALSE","1","0","", "NAN"]).mean() > 0.8:
                frame[col] = vals.map({
                    "TRUE": True, "FALSE": False, "1": True, "0": False
                }).fillna(False)
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
# 3. 날씨 그룹
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
    "클리어": {
        "mood": "야외활동, 기분전환, 걷기 좋은 날",
        "cats": ["이국적인 음식","디저트/카페","술 한잔 하기 좋은 날","가볍게 간단히","시원한 음식","해산물/생선요리"],
    },
    "구름": {
        "mood": "실내 중심, 편안함, 든든함 추구",
        "cats": ["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 한끼","해산물/생선요리"],
    },
    "비": {
        "mood": "외출 불편, 따뜻하거나 자극적인 음식",
        "cats": ["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은 날","패스트푸드/배달","시원한 한끼"],
    },
    "이슬비": {
        "mood": "활동 가능하지만 귀찮음",
        "cats": ["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"],
    },
    "뇌우": {
        "mood": "외출 최소화, 실내 고정",
        "cats": ["육류구이/고기파티","든든한 한끼","패스트푸드/배달"],
    },
    "눈": {
        "mood": "실내, 감성적, 따뜻함 추구",
        "cats": ["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"],
    },
    "분위기": {
        "mood": "안개/먼지 등 건강 고려",
        "cats": ["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"],
    },
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
# 4. 날씨 API
# ───────────────────────────────
def fetch_weather(weather_lat: float, weather_lon: float) -> dict:
    if not OPENWEATHER_API_KEY:
        raise RuntimeError("OpenWeather API 키가 없습니다.")
    weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={weather_lat}&lon={weather_lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(weather_url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {
        "id": data["weather"][0]["id"],
        "description": data["weather"][0]["description"],
        "temperature": data["main"]["temp"]
    }

# ───────────────────────────────
# 5. Supabase RPC
# ───────────────────────────────
def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat, "user_lng": lon
        }).execute()
        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다. ({e})")
        return pd.DataFrame()

# ───────────────────────────────
# 6. Main
# ───────────────────────────────
def main():
    # 위치
    user_lat, user_lon = get_user_location()
    st.caption(f"현재 위치: {user_lat:.4f}, {user_lon:.4f}")

    # 날씨
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
        st.markdown(f"### 오늘 날씨: {w['description']}, {w['temperature']}°C")
        st.caption(f"({group_name} / {mood})")
    except Exception as e:
        st.error(f"날씨 불러오기 실패: {e}")
        group_name, opts = "구름", ["가볍게 간단히","든든한 한끼","디저트/카페"]

    # Supabase 데이터
    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)
    st.subheader("반경 500m 음식점 목록")
    st.write(all_df.head(10))  # 확인용

    # 추천 카테고리 선택
    choice = st.radio("추천 카테고리 선택", options=opts)

    # 필터링 로직 (데이터프레임 처리 함수 그대로 활용)
    wx_df = coerce_tf_bool(all_df)   # 불리언 변환
    wx_df["category_norm"] = wx_df["category"].map(norm_cat)  # 카테고리 정규화
    filtered_df = wx_df[wx_df["category_norm"] == norm_cat(choice)]

    st.subheader(f"‘{choice}’ 카테고리 결과")
    st.write(filtered_df[["name_g","category","distance_m"]].head(10))

if __name__ == "__main__":
    main()
