import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine

# CSV 파일 불러오기
df = pd.read_csv('/Users/lyeajin/Desktop/sql/프로젝트 데이터/서울시 일반음식점(완)_위경도포함.csv', encoding='utf-8')

st.title("위치 기반 음식점 추천")

location = streamlit_geolocation()
if location:
    user_lat = location["latitude"]
    user_lon = location["longitude"]
    st.success(f"현재 위치: 위도 {user_lat}, 경도 {user_lon}")
else:
    st.warning("위치정보 허용을 눌러주세요.")
    st.stop()  # 위치 없으면 아래 코드 실행 안 되도록 종료

# 안전하게 haversine 계산
def safe_haversine(lat1, lon1, lat2, lon2):
    if None in [lat1, lon1, lat2, lon2]:
        return None
    return haversine((lat1, lon1), (lat2, lon2))

# 거리 계산: 순서 중요! (위도, 경도)
df['거리(km)'] = df.apply(lambda row: safe_haversine(user_lat, user_lon, row['위도'], row['경도']), axis=1)

# 반경 1.5km 이내 음식점 필터링
df_nearby = df[df['거리(km)'] <= 1.5].copy()

st.title("반경 1.5km 이내 음식점 목록")
st.write("다음은 주변 음식점 목록입니다:")
st.dataframe(df_nearby)
