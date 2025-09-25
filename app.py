import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests
from supabase import create_client
import os
from dotenv import load_dotenv
import re
import math
import pydeck as pdk

# 환경변수 로드 및 DB 클라이언트 초기화
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

# --------------------------------
# 기본 변수 및 함수
# --------------------------------
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
    return CATEGORY_ALIAS.get(name, name)

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype is bool: continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE","FALSE","1","0"]).mean() > 0.8:
                frame[col] = vals.map({"TRUE":True,"FALSE":False,"1":True,"0":False}).fillna(False)
    return frame

def resolve_tf_column(frame: pd.DataFrame, expected_label: str):
    expected = norm_cat(expected_label)
    if expected in frame.columns:
        return expected
    want = re.sub(r"[\s/_\-()]+","", expected.lower())
    normalized = {str(c): re.sub(r"[\s/_\-()]+","", str(c).lower()) for c in frame.columns}
    for col, key in normalized.items():
        if key == want:
            return col
    for col, key in normalized.items():
        if want in key:
            return col
    return None

WX_GROUPS = {
    "클리어": [800],
    "구름": [801,802,803,804],
    "비": [500,501,502,503,504,511,520,521,522,531],
    "이슬비": [300,301,302,310,311,312,313,314,321],
    "뇌우": [200,201,202,210,211,212,221,230,231,232],
    "눈": [600,601,602,611,612,613,615,616,620,621,622],
    "분위기": [701,711,721,731,741,751,761,762],
}

WX_RECO = {
    "클리어":{"mood":"야외활동, 기분전환","cats":["이국적인 음식","디저트/카페","술 한잔 하기 좋은 날","가볍게 간단히","시원한 음식","해산물/생선요리"]},
    "구름":{"mood":"실내 중심, 편안함, 든든함 추구","cats":["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 한끼","해산물/생선요리"]},
    "비":{"mood":"외출 불편, 따뜻하거나 자극적인 음식","cats":["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은 날","패스트푸드/배달","시원한 한끼"]},
    "이슬비":{"mood":"정적이거나 가벼운 공간","cats":["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"]},
    "뇌우":{"mood":"외출 최소화, 실내고정","cats":["육류구이/고기파티","든든한 한끼","패스트푸드/배달"]},
    "눈":{"mood":"감성적, 따뜻함 추구","cats":["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"]},
    "분위기":{"mood":"안개/먼지 등, 건강 고려","cats":["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"]},
}

def weather_group_from_id(weather_id:int):
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"

@st.cache_data(ttl=600)
def fetch_weather(lat:float, lon:float):
    if not OPENWEATHER_API_KEY:
        raise RuntimeError("OpenWeather API 키가 설정되지 않았습니다.")
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url)
    res.raise_for_status()
    data = res.json()
    return {
        "id": data["weather"][0]["id"],
        "description": data["weather"][0]["description"],
        "temperature": data["main"]["temp"]
    }

@st.cache_data(ttl=600)
def get_restaurant_within_500m_from_supabase(lat:float, lon:float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat":lat,"user_lng":lon}).execute()
        if not response or not response.data:
            return pd.DataFrame()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다. ({e})")
        return pd.DataFrame()

def filter_by_category_tf(frame:pd.DataFrame, theme:str)->pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col_name = resolve_tf_column(frame, theme)
    if not col_name:
        st.warning(f"'{theme}'에 해당하는 컬럼을 찾지 못했습니다.")
        return pd.DataFrame()
    out = frame[frame[col_name]==True].copy()
    if "distance_m" in out.columns:
        out["distance_m"] = pd.to_numeric(out["distance_m"], errors="coerce")
        out = out.sort_values("distance_m")
    return out

#세션 상태 초기화 (중요)
if "page" not in st.session_state:
    st.session_state.page = "page1"

# 페이지 분기
def main():
    st.title("날씨 + 위치 기반 음식점 추천 🌨️")

    user_lat, user_lon = get_user_location()
    if user_lat is None or user_lon is None:
        st.warning("위치 권한이 필요합니다. 위치 허용을 확인하세요.")
        st.stop()

    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = WX_RECO[group_name]["cats"], WX_RECO[group_name]["mood"]
    except Exception as e:
        st.error(f"날씨 정보를 불러오는 데 실패했습니다: {e}")
        group_name, opts, mood = "구름", ["한식","중식","일식","양식"], "실내 중심, 편안함"

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    if st.session_state.page == "page1":
        st.subheader("지금 날씨에 어울리는 음식 카테고리")
        choice = st.radio("선택해 주세요 👇", options=opts, horizontal=True)
        wx_df = filter_by_category_tf(all_df, choice)

        st.write("해당 카테고리에 해당되는 반경 500M 내 음식점 입니다.")

        if wx_df is not None and not wx_df.empty:
            st.dataframe(wx_df[["name","distance_m"]].rename(columns={"name":"이름","distance_m":"거리"}))
        else:
            st.warning("해당 카테고리 음식점이 없습니다.")

        if st.button("다음으로 ➡️"):
            st.session_state.choice = choice
            st.session_state.page = "page2"
            st.experimental_rerun()

    elif st.session_state.page == "page2":
        st.subheader("업태 선택")
        choice = st.session_state.get("choice", None)
        if choice is None:
            st.warning("Page1에서 먼저 선택해 주세요.")
            if st.button("⬅️ 이전으로"):
                st.session_state.page = "page1"
                st.experimental_rerun()
            st.stop()

        wx_df = filter_by_category_tf(all_df, choice)
        final_filtered_df = wx_df.copy()

        if "category" in final_filtered_df.columns and not final_filtered_df.empty:
            cats_all = sorted(list(final_filtered_df["category"].dropna().unique()))
            selected = st.multiselect("업태 선택", options=cats_all)
            if selected:
                final_filtered_df = final_filtered_df[final_filtered_df["category"].isin(selected)]

            st.caption(f"선택된 업태: {', '.join(selected)} (총 {len(final_filtered_df)}곳)")

        if st.button("다음으로 ➡️"):
            st.session_state.filtered = final_filtered_df
            st.session_state.page = "page3"
            st.experimental_rerun()

        if st.button("⬅️ 이전으로"):
            st.session_state.page = "page1"
            st.experimental_rerun()

    elif st.session_state.page == "page3":
        st.subheader("결과 확인")
        final_filtered = st.session_state.get("filtered", pd.DataFrame())
        if final_filtered.empty:
            st.warning("Page2에서 필터링된 결과가 없습니다.")
        else:
            st.dataframe(final_filtered)

        if st.button("⬅️ 이전으로"):
            st.session_state.page = "page2"
            st.experimental_rerun()

if __name__ == "__main__":
    main()

