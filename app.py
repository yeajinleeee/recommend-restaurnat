import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests
import concurrent.futures
import psycopg2
from supabase import create_client, Client
import os
from dotenv import load_dotenv


load_dotenv()
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_API_KEY")
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

OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")


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



def get_restaurant_within_500m_from_supabase(lat, lon):
    response = (
        supabase.rpc("get_restaurant_within_500m", {
        "user_lat": lat,
        "user_lng": lon
        }).execute()
        )
    #response에 데이터 들어가있음

    st.title("반경 1km 이내 음식점 목록")
    st.write("다음은 주변 음식점 목록입니다:")
    st.write(f"전체 행 개수: {len(response.data)}")
    # st.write(response.data)

    df = pd.DataFrame(response.data)

    st.dataframe(df)


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
    get_restaurant_within_500m_from_supabase(seoul_lat, seoul_lon)

if __name__ == "__main__":
    main()