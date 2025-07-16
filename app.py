import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests


# CSV 파일 불러오기
df = pd.read_csv('restaurant_cleaned_data.csv', encoding='utf-8')

st.title("날씨 + 위치 기반 음식점 추천🌨️")

#위치 가져오기 
location = streamlit_geolocation()

if location:
    user_lat = 126.9732829
    user_lon = 37.56332531
    st.success(f"현재 위치: 위도 {user_lat}, 경도 {user_lon}")
else:
    st.warning("위치정보 허용을 눌러주세요.")
    st.stop()  # 위치 없으면 아래 코드 실행 안 되도록 종료

API_KEY = "56dfd0f8d8a24c9b492d704b63ddb493"
weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={user_lat}&lon={user_lon}&appid={API_KEY}&units=metric&lang=kr"

try:
    res = requests.get(weather_url)
    weather_data = res.json()
    weather_main = weather_data['weather'][0]['main']  # 예: Clear, Rain, Snow
    weather_desc = weather_data['weather'][0]['description']
    temp = weather_data['main']['temp']

    st.markdown(f"""
    ### 현재 날씨 정보
    - 상태: **{weather_desc}**
    - 기온: **{temp}°C**
    """)
except Exception as e:
    st.error("날씨 정보를 불러오는 데 실패했습니다.")
    weather_main = None


# 안전하게 haversine 계산
def safe_haversine(lat1, lon1, lat2, lon2):
    if None in [lat1, lon1, lat2, lon2]:
        return None
    return haversine((lat1, lon1), (lat2, lon2))

# 거리 계산: 순서 중요! (위도, 경도)
df['거리(km)'] = df.apply(lambda row: safe_haversine(user_lat, user_lon, row['위도'], row['경도']), axis=1)

# 반경 1.0km 이내 음식점 필터링
df_nearby = df[df['거리(km)'] <= 1.0].copy()
row_count = df_nearby.shape[0]

st.title("반경 1km 이내 음식점 목록")
st.write("다음은 주변 음식점 목록입니다:")
st.dataframe(df_nearby)
st.write(f"전체 행 개수: {row_count}")
