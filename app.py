import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
import requests
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import re
import math
import pydeck as pdk

# ───────────────────────────────
# 환경 변수 설정
# ───────────────────────────────
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

st.set_page_config(page_title="날씨 + 위치 기반 음식점 추천", page_icon="🍜", layout="wide")

# ───────────────────────────────
# 위치 관련
# ───────────────────────────────
seoul_lat, seoul_lon = 37.5665, 126.9780
def get_user_location():
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

# ───────────────────────────────
# 카테고리 이름 매핑
# ───────────────────────────────
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
}
def norm_cat(name: str) -> str:
    return CATEGORY_ALIAS.get(name, name)

def _normalize_label(s: str) -> str:
    if s is None: return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\\-()]+", "", s)

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype is bool: continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE","FALSE","1","0","", "NAN"]).mean() > 0.8:
                frame[col] = vals.map({"TRUE": True,"FALSE": False,"1": True,"0": False}).fillna(False)
    return frame

def resolve_tf_column(frame: pd.DataFrame, expected_label: str) -> str | None:
    expected = norm_cat(expected_label)
    if expected in frame.columns: return expected
    want = _normalize_label(expected)
    normalized = {str(c): _normalize_label(str(c)) for c in frame.columns}
    for col, key in normalized.items():
        if key == want: return col
    for col, key in normalized.items():
        if want in key: return col
    return None

# ───────────────────────────────
# 날씨
# ───────────────────────────────
WX_GROUPS = {
    "클리어": [800],
    "구름": [801, 802, 803, 804],
    "비": [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우": [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈": [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "분위기": [701, 711, 721, 731, 741, 751, 761, 762],
}
WX_RECO = {
    "클리어": {"mood":"야외활동, 기분전환, 걷기 좋은 날",
               "cats":["이국적인 음식","디저트/카페","술 한잔 하기 좋은 날","가볍게 간단히","시원한 음식","해산물/생선요리"]},
    "구름": {"mood":"실내 중심, 편안함, 든든함 추구",
             "cats":["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 한끼","해산물/생선요리"]},
    "비": {"mood":"외출 불편, 따뜻하거나 자극적인 음식",
           "cats":["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은 날","패스트푸드/배달","시원한 한끼"]},
    "이슬비": {"mood":"활동 가능하지만 귀찮음",
               "cats":["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"]},
    "뇌우": {"mood":"외출 최소화, 실내 고정",
             "cats":["육류구이/고기파티","든든한 한끼","패스트푸드/배달"]},
    "눈": {"mood":"실내, 감성적, 따뜻함 추구",
           "cats":["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"]},
    "분위기": {"mood":"안개/먼지 등 건강 고려",
              "cats":["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"]},
}
def weather_group_from_id(weather_id: int) -> str:
    for g,codes in WX_GROUPS.items():
        if int(weather_id) in codes: return g
    return "구름"

def recommended_categories_from_group(group_name: str):
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return cats, mood

def fetch_weather(lat: float, lon: float) -> dict:
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    data = res.json()
    return {"id":data["weather"][0]["id"],"description":data["weather"][0]["description"],"temperature":data["main"]["temp"]}

# ───────────────────────────────
# DB
# ───────────────────────────────
def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not response or response.data is None: return pd.DataFrame()
        return pd.DataFrame(response.data)
    except: return pd.DataFrame()

# ───────────────────────────────
# 렌더링
# ───────────────────────────────
def render_scroll_table(frame: pd.DataFrame, height=250):
    if frame.empty:
        st.info("표시할 식당이 없습니다.")
        return
    df = frame.copy()
    df = df.reset_index(drop=True)
    df.index = df.index + 1
    if "distance_m" in df.columns:
        df["distance_m"] = df["distance_m"].round(0).astype(int).astype(str) + "m"
    st.caption(f"총 {len(df)}개")
    st.dataframe(df[["name","distance_m"]], height=height)

def render_map(lat, lon, frame: pd.DataFrame):
    if frame.empty: return
    df = frame.rename(columns={"latitude":"lat","longitude":"lon"})
    me = pd.DataFrame([{"lat":lat,"lon":lon}])
    layers=[
        pdk.Layer("ScatterplotLayer", data=df, get_position="[lon, lat]", get_radius=40, get_fill_color=[255,0,0,160]),
        pdk.Layer("ScatterplotLayer", data=me, get_position="[lon, lat]", get_radius=90, get_fill_color=[0,100,255,220]),
    ]
    st.pydeck_chart(pdk.Deck(
        map_provider="maplibre",
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=15),
        layers=layers
    ))

# ───────────────────────────────
# 메인
# ───────────────────────────────
def main():
    if "page" not in st.session_state: st.session_state.page = 1
    page = st.session_state.page

    # 위치 + 날씨
    lat, lon = get_user_location()
    try:
        w = fetch_weather(lat, lon)
        group = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group)
    except:
        w, group, opts, mood = {"description":"알수없음","temperature":"?"},"구름",["가볍게 간단히"],"실내 중심"

    # 사이드바
    with st.sidebar:
        st.markdown(f"### 📍 현재 위치\n위도: {lat:.4f}, 경도: {lon:.4f}")
        st.markdown(f"### 🌤️ 현재 날씨\n{w['description']}, {w['temperature']}°C")
        st.markdown(f"### 💡 추천 키워드\n{mood}")

    all_df = get_restaurant_within_500m_from_supabase(lat, lon)

    st.title("날씨 + 위치 기반 음식점 추천 🌨️")

    # Page1
    if page==1:
        st.subheader("현재 날씨 기반 추천 카테고리")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts, key="choice")
        st.divider()
        st.subheader("반경 500m 후보 식당")
        render_scroll_table(all_df)
        if st.button("➡ 다음"): st.session_state.page=2

    # Page2
    elif page==2:
        choice = st.session_state.choice
        st.subheader(f"‘{choice}’ 카테고리에 해당하는 식당")
        filtered = all_df[all_df["category"].astype(str).str.contains(choice, na=False)]
        render_scroll_table(filtered)
        st.subheader("지도 보기")
        render_map(lat, lon, filtered)
        col1,col2=st.columns([0.5,0.5])
        if col1.button("⬅ 이전"): st.session_state.page=1
        if col2.button("다음 ➡"): st.session_state.page=3

    # Page3
    elif page==3:
        choice = st.session_state.choice
        st.subheader(f"최종 선택: ‘{choice}’ 카테고리 결과 중 하나")
        filtered = all_df[all_df["category"].astype(str).str.contains(choice, na=False)]
        if not filtered.empty:
            picked = st.selectbox("식당을 고르세요", filtered["name"].tolist())
            if st.button("맛집을 정했어요!"): st.success(f"🎉 선택한 맛집: {picked}")
            if st.button("조금 더 둘러볼래요!"): st.session_state.page=1
        else:
            st.warning("조건에 맞는 식당이 없습니다.")

if __name__=="__main__":
    main()

