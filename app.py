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
import urllib.parse

# ───────────────────────────────
# 0. 환경 설정
# ───────────────────────────────
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

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
# 3+. Google Places API (이미지용)
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
# 카드형 표시 함수 (+요인)
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
# Supabase 함수
# ───────────────────────────────
def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat, "user_lng": lon
        }).execute()

        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(response.data)
        if "latitude" in df.columns and "longitude" in df.columns:
            df["distance_m"] = df.apply(
                lambda row: haversine((lat, lon), (row["latitude"], row["longitude"])) * 1000,
                axis=1
            ).round(0).astype(int)
        return df
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

# ───────────────────────────────
# Main
# ───────────────────────────────
def main():
    if "page" not in st.session_state:
        st.session_state.page = "page1"
    if "selected_restaurant" not in st.session_state:
        st.session_state.selected_restaurant = None

    user_lat, user_lon = get_user_location()
    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # ───────────── Page1 ─────────────
    if st.session_state.page == "page1":
        st.header("현재 날씨에 추천 드리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", ["시원한 음식", "든든한 한끼", "디저트/카페"])
        filtered_df = all_df  # 간단히 예시

        st.subheader("근처 음식점 (링크 클릭 시 지도 열기)")
        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)[["이름","거리","지도링크"]]
            for _, row in df.iterrows():
                st.markdown(f"[{row['이름']}]({row['지도링크']}) — {row['거리']}", unsafe_allow_html=True)
        else:
            st.warning("해당 카테고리 음식점이 없습니다.")

        if st.button("➡ 다음"):
            st.session_state.page = "page2"
            st.rerun()

    # ───────────── Page2 ─────────────
    elif st.session_state.page == "page2":
        st.header("세부 추천 리스트")
        filtered_df = prettify_dataframe(all_df)[["이름","거리","주소","별점","리뷰 수","지도링크"]]
        for i, row in filtered_df.iterrows():
            col1, col2 = st.columns([8,2])
            with col1:
                st.markdown(f"**{row['이름']}** — {row['거리']} | ⭐ {row['별점']} | 💬 {row['리뷰 수']}")
            with col2:
                if st.button("선택하기", key=f"sel_{i}"):
                    st.session_state.selected_restaurant = row.to_dict()
                    st.session_state.page = "page3"
                    st.rerun()

        if st.button("⬅ 이전"):
            st.session_state.page = "page1"
            st.rerun()

    # ───────────── Page3 ─────────────
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


