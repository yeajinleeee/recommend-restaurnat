import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
import requests
from supabase import create_client, Client
import os
from dotenv import load_dotenv
from haversine import haversine
from typing import List, Tuple
import re

# ───────────────────────────────
# 0. 환경 설정
# ───────────────────────────────
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

st.set_page_config(page_title="날씨 + 위치 기반 음식점 추천", page_icon="🍜", layout="wide")
st.title("날씨 + 위치 기반 음식점 추천 🌨️")

# ───────────────────────────────
# 1. 유틸 함수
# ───────────────────────────────
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    try:
        loc = streamlit_geolocation()
        if not loc or loc.get("latitude") is None:
            return seoul_lat, seoul_lon
        return float(loc["latitude"]), float(loc["longitude"])
    except:
        return seoul_lat, seoul_lon

def prettify_dataframe(df):
    df = df.copy()
    if "distance_m" in df.columns:
        df["거리"] = df["distance_m"].astype(int).astype(str) + "m"
    rename_map = {
        "name": "이름",
        "category": "업태",
        "address": "주소",
        "rating": "별점",
        "review_cnt": "리뷰 수",
    }
    df.rename(columns=rename_map, inplace=True)
    return df

# ───────────────────────────────
# 2. DB/API 함수
# ───────────────────────────────
def fetch_weather(lat, lon):
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    try:
        res = requests.get(url, timeout=8)
        d = res.json()
        return d["weather"][0]["description"], d["main"]["temp"]
    except:
        return "알 수 없음", "?"

def get_restaurant_within_500m(lat, lon):
    try:
        res = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        df = pd.DataFrame(res.data)
        if not df.empty:
            df["distance_m"] = df.apply(lambda r: haversine((lat, lon), (r["latitude"], r["longitude"])) * 1000, axis=1).round()
        return df
    except Exception as e:
        st.error(f"데이터 불러오기 오류: {e}")
        return pd.DataFrame()

# ───────────────────────────────
# 3. 메인 로직
# ───────────────────────────────
def main():
    if "page" not in st.session_state:
        st.session_state.page = "page1"

    user_lat, user_lon = get_user_location()
    weather, temp = fetch_weather(user_lat, user_lon)

    with st.sidebar:
        st.markdown(
            f"""
            <div style="background:white; padding:15px; border-radius:12px;">
                <h4>📍 현재 위치</h4><p>{user_lat:.4f}, {user_lon:.4f}</p>
                <h4>🌦️ 현재 날씨</h4><p>{weather}, {temp}°C</p>
            </div>
            """,
            unsafe_allow_html=True
        )

    all_df = get_restaurant_within_500m(user_lat, user_lon)

    # PAGE 1
    if st.session_state.page == "page1":
        st.header("현재 날씨에 맞는 카테고리를 선택하세요 🌤️")
        opts = ["든든한 한끼", "가볍게 간단히", "디저트/카페"]
        choice = st.radio("추천 카테고리 중 선택 👇", opts)

        filtered = all_df.copy()
        if not filtered.empty:
            df = prettify_dataframe(filtered)
            df["map_link"] = df.get("map_link", "")

            rows = ""
            for i, row in df.iterrows():
                rows += f"""
                <tr>
                    <td style='text-align:center'>{i+1}</td>
                    <td>{row['이름']}</td>
                    <td style='text-align:center'>{row['거리']}</td>
                    <td style='text-align:center'>
                        <a href='{row['map_link']}' target='_blank'
                           style='color:black; border:1px solid black; border-radius:6px; padding:4px 8px; text-decoration:none;'>열기 🔗</a>
                    </td>
                </tr>
                """
            html = f"""
            <table style='width:100%; border-collapse:collapse;'>
                <thead><tr><th>번호</th><th>이름</th><th>거리</th><th>링크</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
            """
            st.markdown(f"<div style='max-height:500px; overflow-y:auto'>{html}</div>", unsafe_allow_html=True)
        if st.button("➡ 다음"):
            st.session_state.choice = choice
            st.session_state.page = "page2"
            st.rerun()

    # PAGE 2
    elif st.session_state.page == "page2":
        choice = st.session_state.get("choice", "카테고리 미선택")
        st.header(f"‘{choice}’ 카테고리 결과")

        if all_df.empty:
            st.warning("결과가 없습니다.")
        else:
            df = prettify_dataframe(all_df)
            rows = ""
            for i, row in df.iterrows():
                name = row["이름"]
                link = row.get("map_link", "")
                rows += f"""
                <tr>
                    <td style='text-align:center'>{i+1}</td>
                    <td><a href='?store={name}' style='color:blue; text-decoration:none; font-weight:bold;'>{name}</a></td>
                    <td style='text-align:center'>{row['거리']}</td>
                    <td style='text-align:center'>
                        <a href='{link}' target='_blank' style='color:black; border:1px solid black; padding:4px 8px; border-radius:6px; text-decoration:none;'>링크 🔗</a>
                    </td>
                </tr>
                """
            html = f"<table style='width:100%; border-collapse:collapse;'><thead><tr><th>번호</th><th>이름</th><th>거리</th><th>링크</th></tr></thead><tbody>{rows}</tbody></table>"
            st.markdown(f"<div style='max-height:500px; overflow-y:auto'>{html}</div>", unsafe_allow_html=True)

            # ✅ Streamlit 최신 API 반영
            params = st.query_params
            store = params.get("store", [None])[0] if isinstance(params.get("store"), list) else params.get("store")
            if store:
                st.session_state.selected_store = store
                st.session_state.page = "page3"
                st.query_params.clear()
                st.rerun()

        if st.button("⬅ 이전"):
            st.session_state.page = "page1"
            st.rerun()

    # PAGE 3
    elif st.session_state.page == "page3":
        st.header("🍽️ 선택한 음식점 정보")

        store = st.session_state.get("selected_store")
        if not store:
            st.warning("선택된 음식점이 없습니다.")
        else:
            row = all_df[all_df["name"] == store].iloc[0]
            name = row.get("name", "")
            address = row.get("address", "")
            rating = row.get("rating", "정보 없음")
            review = row.get("review_cnt", "정보 없음")
            dist = row.get("distance_m", "")
            link = row.get("map_link", "")

            st.markdown(f"""
            <div style='background:white; padding:20px; border-radius:15px; border:1px solid #ddd;
                        box-shadow:0 4px 10px rgba(0,0,0,0.1); max-width:700px;'>
                <h2>{name}</h2>
                <p><b>📍 주소:</b> {address}</p>
                <p><b>📏 거리:</b> {dist}m</p>
                <p><b>⭐ 별점:</b> {rating}</p>
                <p><b>💬 리뷰 수:</b> {review}</p>
                <p><a href='{link}' target='_blank' style='border:1px solid black; color:black; border-radius:6px; padding:5px 10px; text-decoration:none;'>지도에서 보기 🔗</a></p>
            </div>
            """, unsafe_allow_html=True)

        if st.button("⬅ 다시 선택"):
            st.session_state.page = "page2"
            st.rerun()


if __name__ == "__main__":
    main()
