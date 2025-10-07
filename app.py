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
import urllib.parse  # ✅ 추가

# ───────────────────────────────
# 0. 환경 설정
# ───────────────────────────────
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")  # ✅ 추가

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

CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
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

# ───────────────────────────────
# prettify
# ───────────────────────────────
def prettify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
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
        "name": "이름",
        "place_name": "이름",
        "store_name": "이름",
        "상호명": "이름",
        "category": "업태",
        "rating": "별점",
        "review_cnt": "리뷰 수",
        "address": "주소",
        "도로명주소": "주소",
        "지번주소": "주소",
        "map_link": "지도링크"
    }
    df.rename(columns=rename_map, inplace=True)
    return df

# ───────────────────────────────
# 3+. Google Places API (대표 이미지)
# ───────────────────────────────
def get_place_photo_url(place_name: str) -> str | None:
    try:
        query = urllib.parse.quote(place_name)
        search_url = (
            f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
            f"?input={query}&inputtype=textquery&fields=photos,place_id"
            f"&key={GOOGLE_PLACES_API_KEY}"
        )
        res = requests.get(search_url, timeout=10)
        data = res.json()
        if not data.get("candidates"):
            return None
        photos = data["candidates"][0].get("photos")
        if not photos:
            return None
        ref = photos[0]["photo_reference"]
        return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=400&photo_reference={ref}&key={GOOGLE_PLACES_API_KEY}"
    except:
        return None

# ───────────────────────────────
# 3++. 카드형 표시 함수 (+요인)
# ───────────────────────────────
def show_restaurant_card(info: dict):
    if not info:
        st.warning("선택된 음식점이 없습니다.")
        return
    img = get_place_photo_url(info.get("이름", ""))
    link = info.get("지도링크", "#")
    html = f"""
    <div style='width:450px;background:#fff;border:1px solid #ddd;border-radius:12px;padding:10px;
                box-shadow:0 2px 8px rgba(0,0,0,0.1);cursor:pointer;'
         onclick="window.open('{link}','_blank')">
        <img src='{img if img else "https://via.placeholder.com/400x200"}' width='430' height='200' style='border-radius:10px;'/>
        <h3 style='margin:10px 0 5px 0;'>{info.get("이름","")}</h3>
        <p style='color:#555;margin:0;'>📍 {info.get("주소","")}</p>
        <p style='color:#777;margin:0;'>⭐ {info.get("별점","-")} | 💬 {info.get("리뷰 수","0")} 리뷰</p>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

# ───────────────────────────────
# (이하 날씨, API, 필터링, main 등 기존 동일)
# ───────────────────────────────
# ... 기존 WX_GROUPS, WX_RECO, fetch_weather, get_restaurant_within_500m_from_supabase, filter_by_category_tf 등 동일 ...

# ───────────────────────────────
# 5. Main (+요인만 추가)
# ───────────────────────────────
def main():
    if "page" not in st.session_state:
        st.session_state.page = "page1"
    if "selected_restaurant" not in st.session_state:
        st.session_state.selected_restaurant = None

    # 기존 내용 전부 유지 (위치, 날씨, supabase 데이터 로드 포함)
    user_lat, user_lon = get_user_location()
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except:
        w = {"description": "알수없음", "temperature": "?"}
        group_name, opts, mood = "구름", ["가볍게 간단히", "든든한 한끼", "디저트/카페"], "실내 중심"

    with st.sidebar:
        st.markdown(f"<div style='background:#fff;border-radius:10px;padding:15px;margin-bottom:15px;'>"
                    f"<h3>📍 현재 위치</h3><p>위도: {user_lat:.4f}, 경도: {user_lon:.4f}</p></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#fff;border-radius:10px;padding:15px;margin-bottom:15px;'>"
                    f"<h3>🌤️ 현재 날씨</h3><p>{w['description']}, {w['temperature']}°C</p></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#fff;border-radius:10px;padding:15px;'>"
                    f"<h3>💡 추천 키워드</h3><p>{mood}</p></div>", unsafe_allow_html=True)

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # ───────────── Page1 ─────────────
    if st.session_state.page == "page1":
        st.header("현재 날씨에 추천 드리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)
        filtered_df = filter_by_category_tf(all_df, choice)

        st.subheader(f"‘{choice}’ 카테고리에 해당되는 반경 500M 내 음식점 (거리순)")
        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)[["이름","거리","주소","별점","리뷰 수","지도링크"]]
            df = df.reset_index(drop=True)
            df.index = df.index + 1
            st.dataframe(df, use_container_width=True, height=500)
        else:
            st.warning("해당 카테고리 음식점이 없습니다.")

        col1, col2 = st.columns([9,1])
        with col2:
            if st.button("➡ 다음"):
                st.session_state.choice = choice
                st.session_state.page = "page2"
                st.rerun()

    # ───────────── Page2 (+요인) ─────────────
    elif st.session_state.page == "page2":
        choice = st.session_state.get("choice")
        st.header(f"‘{choice}’ 카테고리 결과")
        filtered_df = filter_by_category_tf(all_df, choice)

        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)[["이름","거리","주소","별점","리뷰 수","지도링크"]]
            df = df.reset_index(drop=True)
            df.index = df.index + 1

            # ✅ 이름 클릭 시 page3으로 이동
            for i, row in df.iterrows():
                if st.button(row["이름"], key=f"btn_{i}"):
                    st.session_state.selected_restaurant = row.to_dict()
                    st.session_state.page = "page3"
                    st.rerun()

        col1, col2 = st.columns([9,1])
        with col1:
            if st.button("⬅ 이전"):
                st.session_state.page = "page1"
                st.rerun()

    # ───────────── Page3 (+요인) ─────────────
    elif st.session_state.page == "page3":
        st.header("🍽️ 선택한 가게 정보")
        if st.session_state.selected_restaurant:
            show_restaurant_card(st.session_state.selected_restaurant)
        else:
            st.warning("선택된 가게가 없습니다.")

        if st.button("⬅ 다시 선택"):
            st.session_state.page = "page2"
            st.rerun()

if __name__ == "__main__":
    main()

