import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests
import concurrent.futures
import psycopg2
from supabase import create_client, Client
import os

# url: str = "https://yamiretxhfjduvaktqhx.supabase.co"
# key: str = "sb_publishable_J4ipWe8Qp6JpxFHBtBO3PA_P4-Mcg5Q"
# supabase: Client = create_client(url, key)    
# # CSV 파일 불러오기
# #df = pd.read_csv('restaurant_cleaned_data.csv', encoding='utf-8')

# CSV 파일 불러오기
df1 = pd.read_csv('df_clean1.csv', encoding='utf-8')
df2 = pd.read_csv('df_clean2.csv', encoding='utf-8')

df = pd.concat([df1, df2], axis=0)

st.title("날씨 + 위치 기반 음식점 추천🌨️")

#위치 가져오기 
location = streamlit_geolocation()
#st.caption(f"raw location: {location}")  # 디버깅용

if not location or location.get("latitude") is None:
    st.warning("위치정보 버튼을 눌러 허용해주세요. (브라우저에서 위치 권한 허용 필수)")
    st.stop()

user_lat = 37.5665  #location["latitude"]
user_lon = 126.9780  #location["longitude"]
st.success(f"현재 위치: 위도 {user_lat}, 경도 {user_lon}")



#날씨 api 
API_KEY = "56dfd0f8d8a24c9b492d704b63ddb493"

# 날씨 코드에 따른 카테고리 매핑
weather_categories = {
    "Clear": ["이국적인 음식", "디저트/카페", "술 한잔 하기 좋은 날", "가볍게 간단히", "시원한 음식", "해산물/생선요리"],
    "Clouds": ["든든한 한끼", "뜨끈한 국물", "디저트/카페", "시원한 한끼", "해산물/생선요리"],
    "Rain": ["뜨끈한 국물", "매콤한 음식", "술 한잔 하기 좋은 날", "패스트푸드/배달", "시원한 한끼"],
    "Drizzle": ["디저트/카페", "가볍게 간단히", "건강/채식/특수식당", "해산물/생선요리"],
    "Thunderstorm": ["육류구이/고기파티", "든든한 한끼", "패스트푸드/배달"],
    "Snow": ["뜨끈한 국물", "육류구이/고기파티", "가족/단체회식", "디저트/카페", "해산물/생선요리"],
    "Mist": ["건강/채식/특수식단", "뜨끈한 국물", "배달"],
}

def get_weather(lat, lon):
    weather_url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?lat={user_lat}&lon={user_lon}&appid={API_KEY}&units=metric&lang=kr"
    )
    try:
        response = requests.get(weather_url)
        weather_data = response.json()

        # 날씨 상태 코드와 설명 추출
        weather_main = weather_data['weather'][0]['main']  # 예: Clear, Rain, Snow
        weather_desc = weather_data['weather'][0]['description']
        temp = weather_data['main']['temp']

        return weather_main, weather_desc, temp
    except Exception as e:
        return None, None, None

#날씨 정보 가져오기 
weather_main, weather_desc, temp = get_weather(user_lat, user_lon)

if weather_main:
    st.markdown(f"현재 날씨: **{weather_desc}**")
    st.markdown(f"기온: **{temp}°C**")

    # 날씨 코드에 해당하는 카테고리 리스트 가져오기
    if weather_main in weather_categories:
        st.markdown(f"### 오늘 추천 드리는 카테고리:")
        
        selected_category = None
        # 각 카테고리를 블럭 형태로 버튼으로 만들기
        for category in weather_categories[weather_main]:
            if st.button(category):
                selected_category = category

        # 선택된 카테고리 출력
        if selected_category:
            st.markdown(f"**선택한 카테고리:** {selected_category}")

            # 선택한 카테고리에 맞는 업태 구분명 출력
            st.markdown(f"**{selected_category}**에 해당하는 업태 구분명:")
            st.write(category_map[selected_category])  # 선택된 카테고리에 해당하는 업태 구분명 출력

            #5개씩 나누어 보여주기
            filtered_df = df[df['카테고리'] == selected_category]
            first_5_items = filtered_df.head(5)

            for _, row in first_5_item.iterrows():
                st.write(f"사업장명: {row['사업장명']}, 업태구분명: {row['업태구분명']}, 주소: {row['도로명주소']}, 리뷰수: {row['리뷰수']}")

            
            # 나머지 항목은 "더보기" 버튼 클릭 시 표시
            if len(filtered_df) > 5:
                if st.button("더보기"):
                    for _, row in filtered_df.iloc[5:].iterrows():
                        st.write(f"사업장명: {row['사업장명']}, 업태구분명: {row['업태구분명']}, 주소: {row['도로명주소']}, 리뷰수: {row['리뷰수']}")
            
        else:
            st.warning("카테고리를 선택해주세요.")   
    else:
        st.warning("추천할 카테고리가 없습니다.")
else:
    st.error("날씨 정보를 불러오는 데 실패했습니다.")



# # 반경 1km(또는 500m) 내 음식점: Supabase RPC
# try:
#     resp = supabase.rpc(
#         "get_restaurants_within_500m",
#         {"user_lat": user_lat, "user_lng": user_lon}
#     ).execute()

#     df = pd.DataFrame(resp.data or [])
#     st.subheader("반경 1km 이내 음식점 목록")
#     st.write(f"전체 행 개수: {len(df)}")
#     st.dataframe(df)
# except Exception as e:
#     st.error(f"음식점 데이터 조회 실패: {e}")
