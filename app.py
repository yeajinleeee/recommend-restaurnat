import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
import requests
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import pydeck as pdk
from haversine import haversine
from typing import List, Tuple
import re

# ───────────────────────────────
# 0. 환경 설정
# ───────────────────────────────
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

st.set_page_config(page_title="날씨 + 위치 기반 음식점 추천", page_icon="🍜", layout="wide")
st.title("날씨 + 위치 기반 음식점 추천 🌨️")

# ───────────────────────────────
# 1. 유틸
# ───────────────────────────────
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

# 카테고리 이름 표준화
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",  # 오타 보정
}
def norm_cat(name: str) -> str:
    return CATEGORY_ALIAS.get(str(name).strip(), str(name).strip())

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

def prettify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """컬럼명 한글화 및 거리 단위 붙이기"""
    if df is None or df.empty:
        return df
    df = df.copy()
    if "distance_m" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_m"], errors="coerce").apply(
            lambda x: f"{int(x)}m" if pd.notna(x) else ""
        )
    elif "distance_km" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_km"], errors="coerce").apply(
            lambda x: f"{x:.2f}km" if pd.notna(x) else ""
        )
    rename_map = {
        "name_g": "이름",
        "category": "업태",
        "rating": "별점",
        "review_cnt": "리뷰 수",
        "address": "주소", "도로명주소": "주소", "지번주소": "주소",
    }
    df.rename(columns=rename_map, inplace=True)
    df.index = range(1, len(df) + 1)  # 인덱스 1부터 시작
    return df

# ───────────────────────────────
# 2. 날씨 그룹 & 추천 카테고리
# ───────────────────────────────
WX_GROUPS = {
    "클리어": [800], "구름": [801,802,803,804], "비": [500,501,502,503,504,511,520,521,522,531],
    "이슬비": [300,301,302,310,311,312,313,314,321], "뇌우": [200,201,202,210,211,212,221,230,231,232],
    "눈": [600,601,602,611,612,613,615,616,620,621,622], "분위기": [701,711,721,731,741,751,761,762],
}
WX_RECO = {
    "클리어": {"mood": "야외활동, 기분전환", "cats": ["이국적인 음식","디저트/카페","술 한잔 하기 좋은 날","가볍게 간단히","시원한 한끼","해산물/생선요리"]},
    "구름": {"mood": "실내 중심, 편안함", "cats": ["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 한끼","해산물/생선요리"]},
    "비": {"mood": "외출 불편, 자극적 음식", "cats": ["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은 날","패스트푸드/배달","시원한 한끼"]},
    "이슬비": {"mood": "정적이거나 가벼운 공간", "cats": ["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화", "cats": ["육류구이/고기파티","든든한 한끼","패스트푸드/배달"]},
    "눈": {"mood": "실내, 따뜻함", "cats": ["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"]},
    "분위기": {"mood": "건강 고려", "cats": ["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"]},
}
def weather_group_from_id(weather_id: int) -> str:
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes: return group_name
    return "구름"
def recommended_categories_from_group(group_name: str, top_k: int | None = None):
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return (cats, mood) if top_k is None else (cats[:top_k], mood)

# ───────────────────────────────
# 3. API
# ───────────────────────────────
def fetch_weather(lat: float, lon: float) -> dict:
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    data = res.json()
    return {"id": data["weather"][0]["id"], "description": data["weather"][0]["description"], "temperature": data["main"]["temp"]}

def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not response or response.data is None: return pd.DataFrame()
        df = pd.DataFrame(response.data)
        if "latitude" in df.columns and "longitude" in df.columns:
            df["distance_m"] = df.apply(lambda row: haversine((lat, lon), (row["latitude"], row["longitude"])) * 1000, axis=1).round(0).astype(int)
        return df
    except: return pd.DataFrame()

# ───────────────────────────────
# 4. Main
# ───────────────────────────────
def main():
    if "page" not in st.session_state: st.session_state.page = "page1"
    user_lat, user_lon = get_user_location()

    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except:
        w, group_name, opts, mood = {"description":"알수없음","temperature":"?"}, "구름", ["가볍게 간단히","든든한 한끼"], "실내 중심"

    # 사이드바 카드
    with st.sidebar:
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;'>📍 현재 위치<br>위도 {user_lat:.4f}, 경도 {user_lon:.4f}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;'>🌤️ 현재 날씨<br>{w['description']}, {w['temperature']}°C</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px;'>💡 추천 키워드<br>{mood}</div>", unsafe_allow_html=True)

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # CSS (버튼 위치 고정)
    st.markdown(
        """
        <style>
        .button-row {
            display: flex;
            justify-content: space-between;
            margin-top: 10px;
        }
        </style>
        """, unsafe_allow_html=True
    )

    # Page1
    if st.session_state.page == "page1":
        st.header("현재 날씨에 추천 드리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)
        filtered_df = prettify_dataframe(all_df)
        st.subheader("반경 500m 내 전체 음식점")
        st.dataframe(filtered_df[["이름","거리"]], use_container_width=True)

        # 오른쪽 끝 "다음"
        st.markdown('<div class="button-row"><div></div>', unsafe_allow_html=True)
        with st.form(key="next1"):
            if st.form_submit_button("➡ 다음"):
                st.session_state.choice = choice
                st.session_state.page = "page2"
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # Page2
    elif st.session_state.page == "page2":
        choice = st.session_state.choice
        st.header(f"‘{choice}’ 카테고리 결과")
        filtered_df = prettify_dataframe(all_df)

        tabs = st.tabs(["거리순", "별점순", "리뷰순"])
        with tabs[0]:
            st.dataframe(filtered_df.sort_values("거리"))
        with tabs[1]:
            if "별점" in filtered_df.columns:
                st.dataframe(filtered_df.sort_values("별점", ascending=False))
        with tabs[2]:
            if "리뷰 수" in filtered_df.columns:
                st.dataframe(filtered_df.sort_values("리뷰 수", ascending=False))

        # 왼쪽 이전, 오른쪽 다음
        st.markdown('<div class="button-row">', unsafe_allow_html=True)
        with st.form(key="prev2"):
            if st.form_submit_button("⬅ 이전"):
                st.session_state.page = "page1"; st.rerun()
        with st.form(key="next2"):
            if st.form_submit_button("➡ 다음"):
                st.session_state.page = "page3"; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # Page3
    elif st.session_state.page == "page3":
        st.header("최종 선택")
        st.success("맛집 선택 완료! 🎉")

        # 왼쪽 "이전"
        st.markdown('<div class="button-row">', unsafe_allow_html=True)
        with st.form(key="prev3"):
            if st.form_submit_button("⬅ 이전"):
                st.session_state.page = "page2"; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
