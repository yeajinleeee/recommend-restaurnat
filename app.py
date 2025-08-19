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

    
# CSV 파일 불러오기
#df = pd.read_csv('restaurant_cleaned_data.csv', encoding='utf-8')

st.title("날씨 + 위치 기반 음식점 추천🌨️")

# 업종별 카테고리 딕셔너리 정의
category_map = {
    "시원한 음식": ['냉면 전문점', '국수 전문점', '막국수 전문점', '아이스크림 가게', '소바 전문점'],
    "뜨끈한 국물": ['쌀국수 전문식당', '일본라면 전문식당', '감자탕 전문점', '추어탕 전문점', '칼국수집', '부대찌개 전문점', '보신탕집', '아구전문점',  '설렁탕 전문점', '짬뽕 전문점', '돼지국밥 전문점', '우동 전문점', '삼계탕 전문점', '해장국 전문점', '곰탕전문점', '추어탕 전문점', '중국 국수류 전문점', '탕류(보신용)'],
    "매콤한 음식": ['감자탕 전문점', '떡볶이 전문점', '짬뽕 전문점','닭갈비전문점', '부대찌개 전문점'],
    "든든한 한끼": ['한식', '순대 전문점', '한식당', '곱창구이 전문점', '식육(숯불구이)', '불고기 전문점', '통닭(치킨)', 
                 '감자탕 전문점', '닭요리전문점', '추어탕 전문점', '칼국수집', '부대찌개 전문점', '돈까스 전문식당', 
                 '양고기 바베큐 전문점', '찜닭 전문점', '탕류(보신용)', '설렁탕 전문점', '닭갈비전문점', '튀김덮밥 전문점', 
                 '돼지국밥 전문점', '정육식당', '쇠고기덮밥 전문점', '스테이크 전문점', '일본식 카레 전문식당', 
                 '해장국 전문점', '곰탕전문점', '쌀 전문식당', '보쌈 전문점'],
    "육류구이/고기파티": ['한국식 소고기 전문 음식점', '곱창구이 전문점', '한식 고기구이 레스토랑', '삼겹살 전문점', '식육(숯불구이)',
                      '바 & 그릴', '고기 요리 전문점', '불고기 전문점', '통닭(치킨)', '족발가게', '치킨 전문점', 
                      '닭요리전문점', '숯불구이/바베큐전문점', '갈비 전문 음식점', '곱창전문점', '김밥(도시락)', '부대찌개 전문점',
                      '양고기 바베큐 전문점', '야키니쿠 전문식당', '설렁탕 전문점', '닭갈비전문점', '오븐구이치킨집', '정육식당',
                      '스테이크 전문점', '오리구이 전문점', '민물장어전문점', '막창 전문점', '보쌈 전문점'],
    "가볍게 간단히": ['음식점/카페', '제과점', '김밥전문점', '간이음식점', '브런치 식당', '만두 전문점', '분식', '샐러드샵', 
                  '막국수 전문점', '타코 레스토랑', '우동 전문점', '치킨윙 전문식당', '샌드위치 가게', '도시락 전문점', 
                  '소바 전문점', '튀김 전문식당', '꼬치구이 전문식당', '어묵 전문식당', '딤섬 전문 레스토랑'],
    "술 한잔 하기 좋은날": ['술집', '호프/생맥주집', '호프/통닭', '정종/대포집/소주방', '이자카야', '치킨 전문점', '와인 바', 
                      '막걸리 전문점', '와인 전문점', '숯불구이/바베큐전문점', '칵테일바', '빈대떡 전문점', '곱창전문점', 
                      '부대찌개 전문점', '맥주 전문점', '오코노미야끼 전문식당', '포장마차', '꼬치튀김 전문점', '튀김 전문식당', 
                      '꼬치구이 전문식당', '어묵 전문식당', '딤섬 전문 레스토랑', '보쌈 전문점'],
    "디저트/카페": ['커피숍/커피 전문점', '카페', '음식점/카페', '제과점', '카페테리아', '브런치 식당', '디저트 전문 레스토랑', 
                '까페', '디저트 전문점', '에스프레소 바', '아이스크림 가게', '크레프리', '샌드위치 가게', '음료', '식음료', 
                '토스트 레스토랑'],
    "이국적인 음식": ['하와이 레스토랑', '일식당 및 일정식집', '회 전문점', '중국 음식점', '이탈리아 음식점', '아시아 레스토랑',
                 '쌀국수 전문식당', '일식', '태국 음식점', '일본라면 전문식당', '퓨전 음식점', '경양식', 
                 '외국음식전문점(인도,태국등)', '스시/초밥집', '이자카야', '부리토 레스토랑', '서양음식전문점', '인도 레스토랑', 
                 '스파게티 전문점', '베트남 음식점', '중국식', '에티오피아 음식점', '양고기 바베큐 전문점', '멕시코 음식점',
                 '독일 음식점', '야키니쿠 전문식당', '스페인음식점', '타코 레스토랑', '튀김덮밥 전문점', 
                 '일식 꼬치 및 튀김 전문점', '회전초밥집', '대만 레스토랑', '일본식 카레 전문식당', 
                 '일본식 된장 돈까스 전문점', '아메리칸(현대식) 레스토랑', '아메리칸 레스토랑', '일본 스테이크 전문점', 
                 '딤섬 전문 레스토랑', '중국 국수류 전문점'],
    "해산물/생선요리": ['일본 음식점', '일식당 및 일정식집', '회 전문점', '해산물 요리 전문식당', '일식', '횟집', '이자카야', 
                   '스시/초밥집', '민물장어 요리 전문식당', '추어탕 전문점', '참치 전문점', '아구전문점', '회전초밥집', 
                   '생선 레스토랑', '생태/동태요리 전문점', '바다장어 요리 전문식당', '해산물', '민물장어전문점', 
                   '게 요리 전문점'],
}


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
