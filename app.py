import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests
import concurrent.futures
import psycopg2
from supabase import create_client, Client
import os

url: str = "https://yamiretxhfjduvaktqhx.supabase.co"
key: str = "sb_publishable_J4ipWe8Qp6JpxFHBtBO3PA_P4-Mcg5Q"
supabase: Client = create_client(url, key)

st.title("날씨 + 위치 기반 음식점 추천🌨️")

#임의 위치 설정
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()  # 버튼/권한 요청
    if not loc or loc.get("latitude") is None:
        return seoul_lat, seoul_lon, "fallback"
    return float(loc["latitude"]), float(loc["longitude"]), "browser"

#날씨

OPENWEATHER_API_KEY = "56dfd0f8d8a24c9b492d704b63ddb493"

def fetch_weather(lat: float, lon: float):
    if not OPENWEATHER_API_KEY:
        raise RuntimeError("OpenWeather API 키가 설정되지 않았습니다. st.secrets 또는 ENV에 넣어주세요.")
    weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={user_lat}&lon={user_lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {
        "main": data["weather"][0]["main"],
        "description": data["weather"][0]["description"],
        "temperature": data["main"]["temp"]
    }


def display_weather(lat: float, lon: float):
    try:
        w = fetch_weather(lat, lon)
        st.markdown(
            f"""
            ### 현재 날씨
            - 상태: **{w['description']}**
            - 기온: **{w['temperature']}°C**
            """
        )
    except Exception:
        st.error("날씨 정보를 불러오는 데 실패했습니다.")



def display_restaurants(lat: float, lon: float):
    st.subheader("반경 500m 이내 음식점 목록")
    data = get_restaurants_nearby(lat, lon)
    st.write(f"전체 행 개수: {len(data)}")
    if data:
        df = pd.DataFrame(data)
        # distance 컬럼이 m 단위로 있으면 정렬/표시 개선
        sort_key = "distance" if "distance" in df.columns else ("distance_m" if "distance_m" in df.columns else None)
        if sort_key:
            df = df.sort_values(sort_key, ascending=True)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("주변에 음식점이 없습니다.")


# (선택) 지도 표시
def display_map(lat: float, lon: float):
    try:
        st.map(pd.DataFrame([{"lat": lat, "lon": lon}]), latitude="lat", longitude="lon", zoom=16)
    except Exception:
        pass


def main():
    # 위치 가져오기
    user_lat, user_lon, source = get_user_location()
    if source == "browser":
        st.success(f"현재 위치(브라우저): 위도 {user_lat:.5f}, 경도 {user_lon:.5f}")
    else:
        st.info(f"위치 권한이 없어 기본 위치(서울 시청)를 사용합니다. 위도 {user_lat:.5f}, 경도 {user_lon:.5f}") #예시

    # 지도 (선택)
    display_map(user_lat, user_lon)

    # 날씨
    display_weather(user_lat, user_lon)
    st.divider()

    # 음식점
    display_restaurants(user_lat, user_lon)

if __name__ == "__main__":
    main()