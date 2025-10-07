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
# Google Places API (대표 사진)
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
        res.raise_for_status()
        data = res.json()
        if not data.get("candidates"): return None
        photos = data["candidates"][0].get("photos")
        if not photos: return None
        ref = photos[0]["photo_reference"]
        return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=400&photo_reference={ref}&key={GOOGLE_PLACES_API_KEY}"
    except: return None

# ───────────────────────────────
# hover 미리보기
# ───────────────────────────────
def add_hover_place_photo(df: pd.DataFrame) -> pd.DataFrame:
    if "이름" not in df.columns:
        return df
    df = df.copy()
    def make_hover_html(name):
        img_url = get_place_photo_url(name)
        if not img_url: return name
        return f"""
        <div style='position:relative; display:inline-block;'>
            <span style='cursor:pointer; color:#0073e6; text-decoration:underline;'>{name}</span>
            <div style='visibility:hidden; width:400px; background:white; border:1px solid #ccc; border-radius:8px;
                        position:absolute; z-index:10; top:25px; left:0;'>
                <img src='{img_url}' width='400' height='200' style='border-radius:8px;'/>
            </div>
        </div>
        <script>
        const p=document.currentScript.previousElementSibling;
        p.onmouseover=()=>p.querySelector('div').style.visibility='visible';
        p.onmouseout =()=>p.querySelector('div').style.visibility='hidden';
        </script>
        """
    df["이름"] = df["이름"].apply(make_hover_html)
    return df

# ───────────────────────────────
# Page3 카드 표시 함수
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
# 2페이지 선택 → 3페이지 연결 함수
# ───────────────────────────────
def clickable_table(df):
    html = "<table style='width:100%;border-collapse:collapse;'>"
    html += "<tr><th>이름</th><th>거리</th></tr>"
    for _, row in df.iterrows():
        onclick = f"window.parent.postMessage('{row.to_json()}', '*')"
        html += f"<tr onclick=\"{onclick}\" style='cursor:pointer;'><td>{row['이름']}</td><td>{row['거리']}</td></tr>"
    html += "</table>"
    st.components.v1.html(html, height=500, scrolling=True)

# ───────────────────────────────
# 이하 기존 main 구조 (요약)
# ───────────────────────────────
def main():
    if "page" not in st.session_state: st.session_state.page = "page1"
    if "selected_restaurant" not in st.session_state: st.session_state.selected_restaurant = None

    # (위치, 날씨, 데이터 로드 등 기존 동일)
    user_lat, user_lon = 37.5665,126.9780
    all_df = pd.DataFrame()  # supabase 연동 생략 (기존 동일)

    # PAGE1 ~ PAGE3 구조
    if st.session_state.page == "page2":
        st.header("가게 선택 (클릭하면 이동)")
        df = pd.DataFrame([{"이름":"백반집","거리":"200m","주소":"서울시 마포구","지도링크":"https://maps.google.com","별점":4.5,"리뷰 수":12}])
        clickable_table(df)
        # message 이벤트 리스너 연결
        st.session_state.selected_restaurant = df.iloc[0].to_dict()
        if st.button("➡ 선택 완료"): 
            st.session_state.page="page3"; st.rerun()

    elif st.session_state.page=="page3":
        st.header("🍽️ 선택된 가게 정보")
        show_restaurant_card(st.session_state.selected_restaurant)
        if st.button("⬅ 다시 선택"):
            st.session_state.page="page2"; st.rerun()
    else:
        st.header("1페이지: 카테고리 선택 (예시)")
        if st.button("➡ 다음"):
            st.session_state.page="page2"; st.rerun()

if __name__=="__main__":
    main()
