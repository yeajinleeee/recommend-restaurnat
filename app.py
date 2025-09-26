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

# ───────────────────────────────
# 0. 환경 설정
# ───────────────────────────────
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

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

# 카테고리 이름 표준화
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",  # 오타 보정
}
def norm_cat(name: str) -> str:
    return CATEGORY_ALIAS.get(str(name).strip(), str(name).strip())

# ───────────────────────────────
# 2. 날씨 그룹 & 추천 카테고리
# ───────────────────────────────
WX_GROUPS = {
    "클리어": [800],
    "구름":   [801, 802, 803, 804],
    "비":     [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우":   [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈":     [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "분위기": [701, 711, 721, 731, 741, 751, 761, 762],
}
WX_RECO = {
    "클리어": {"mood": "야외활동, 기분전환, 걷기 좋은 날",
               "cats": ["이국적인 음식","디저트/카페","술 한잔 하기 좋은날","가볍게 간단히","시원한 음식","해산물/생선요리"]},
    "구름": {"mood": "실내 중심, 편안함, 든든함 추구",
             "cats": ["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 음식","해산물/생선요리"]},
    "비": {"mood": "외출 불편, 따뜻하거나 자극적인 음식",
           "cats": ["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은날","패스트푸드","시원한 음식"]},
    "이슬비": {"mood": "활동 가능하지만 귀찮음",
               "cats": ["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내 고정",
             "cats": ["육류구이/고기파티","든든한 한끼","패스트푸드"]},
    "눈": {"mood": "실내, 감성적, 따뜻함 추구",
           "cats": ["뜨끈한 국물","육류구이/고기파티","가족/단체 외식","디저트/카페","해산물/생선요리"]},
    "분위기": {"mood": "안개/먼지 등 건강 고려",
              "cats": ["건강/채식/특수식단","뜨끈한 국물","패스트푸드"]},
}

def weather_group_from_id(weather_id: int) -> str:
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"

def recommended_categories_from_group(group_name: str, top_k: int | None = None):
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return (cats, mood) if top_k is None else (cats[:top_k], mood)

# ───────────────────────────────
# 3. API
# ───────────────────────────────
def fetch_weather(weather_lat: float, weather_lon: float) -> dict:
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={weather_lat}&lon={weather_lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {
        "id": data["weather"][0]["id"],
        "description": data["weather"][0]["description"],
        "temperature": data["main"]["temp"]
    }

def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat, "user_lng": lon
        }).execute()
        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(response.data)

        # distance_m 계산
        if "latitude" in df.columns and "longitude" in df.columns:
            df["distance_m"] = df.apply(
                lambda row: haversine(
                    (lat, lon),
                    (row["latitude"], row["longitude"])
                ) * 1000, axis=1
            ).round(0).astype(int)
        return df
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

# ───────────────────────────────
# 4. UI Helper
# ───────────────────────────────
def render_scroll_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        st.info("표시할 식당이 없습니다.")
        return pd.DataFrame()

    df = frame.copy()

    if "distance_m" in df.columns:
        df["거리"] = (
            pd.to_numeric(df["distance_m"], errors="coerce")
            .round(0)
            .astype("Int64")
            .astype(str)
            + "m"
        )

    if "name_g" in df.columns:
        df.rename(columns={"name_g": "이름"}, inplace=True)

    df.reset_index(drop=True, inplace=True)
    df.index = df.index + 1

    st.caption(f"총 {len(df)}개 결과")
    cols_to_show = ["이름", "거리"] if "거리" in df.columns else ["이름"]
    st.dataframe(df[cols_to_show], use_container_width=True, height=500)

    return df

def render_map(user_lat, user_lon, frame: pd.DataFrame):
    if frame.empty:
        st.warning("표시할 식당이 없습니다.")
        return
    if "latitude" not in frame.columns or "longitude" not in frame.columns:
        st.warning("'latitude', 'longitude' 컬럼이 필요합니다.")
        return
    df_map = frame.rename(columns={"latitude":"lat","longitude":"lon"}).copy()
    layers = [
        pdk.Layer("ScatterplotLayer", data=df_map,
                  get_position="[lon, lat]", get_radius=60,
                  get_fill_color=[255,0,0,160], pickable=True),
        pdk.Layer("ScatterplotLayer", data=pd.DataFrame([{"lat":user_lat,"lon":user_lon}]),
                  get_position="[lon, lat]", get_radius=100,
                  get_fill_color=[0,100,255,200])
    ]
    st.pydeck_chart(pdk.Deck(
        map_provider="maplibre",
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        initial_view_state=pdk.ViewState(latitude=user_lat, longitude=user_lon, zoom=15),
        layers=layers
    ))

def select_and_filter_by_business_type(frame: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    if frame.empty or "category" not in frame.columns:
        return frame, []
    cats_all = (
        frame["category"]
        .dropna()
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .map(norm_cat)
        .unique()
        .tolist()
    )
    selected = st.multiselect("업태를 선택하세요", options=cats_all, default=cats_all)
    filtered = frame[frame["category"].map(norm_cat).isin(selected)]
    return filtered, selected

# ───────────────────────────────
# 5. Main
# ───────────────────────────────
def main():
    if "page" not in st.session_state:
        st.session_state.page = "page1"

    user_lat, user_lon = get_user_location()
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except:
        w = {"description":"알수없음","temperature":"?"}
        group_name, opts, mood = "구름", ["가볍게 간단히","든든한 한끼","디저트/카페"], "실내 중심"

    # 사이드바
    with st.sidebar:
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;'>"
                    f"<h3>📍 현재 위치</h3><p>위도: {user_lat:.4f}, 경도: {user_lon:.4f}</p></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;'>"
                    f"<h3>🌤️ 현재 날씨</h3><p>{w['description']}, {w['temperature']}°C</p></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px;'>"
                    f"<h3>💡 추천 키워드</h3><p>{mood}</p></div>", unsafe_allow_html=True)

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # Page 1
    if st.session_state.page == "page1":
        st.header("현재 날씨에 추천 드리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)
        st.markdown("---")
        st.subheader(f"‘{choice}’ 카테고리에 해당되는 반경 500M 내 음식점")

        # ✅ boolean TF 칼럼 우선, 없으면 category 비교
        if norm_cat(choice) in all_df.columns:
            filtered_df = all_df[all_df[norm_cat(choice)] == True]
        else:
            filtered_df = all_df[all_df["category"].map(norm_cat) == norm_cat(choice)] if "category" in all_df.columns else all_df

        page_df = render_scroll_table(filtered_df)

        _, col_btn = st.columns([0.85, 0.15])
        with col_btn:
            if st.button("다음 ➡", use_container_width=True):
                st.session_state.filtered = filtered_df
                st.session_state.page = "page2"
                st.rerun()

    # Page 2
    elif st.session_state.page == "page2":
        st.header("카테고리에 해당하는 식당입니다. 자세히 알아보고 싶은 식당을 골라주세요!")

        filtered = st.session_state.get("filtered", pd.DataFrame())
        if filtered.empty:
            st.warning("이전 단계에서 선택된 식당이 없습니다.")
        else:
            filtered, selected_types = select_and_filter_by_business_type(filtered)

            tabs = st.tabs(["거리순", "별점순", "리뷰순", "지도맵"])
            
            with tabs[0]:
                if "distance_m" in filtered.columns:
                    df_sorted = filtered.sort_values("distance_m", ascending=True).copy()
                    render_scroll_table(df_sorted)
                else:
                    st.warning("거리 정보가 없습니다.")
            
            with tabs[1]:
                if "rating" in filtered.columns:
                    df_sorted = filtered.sort_values("rating", ascending=False).copy()
                    df_sorted.rename(columns={"name_g": "이름"}, inplace=True)
                    df_sorted.reset_index(drop=True, inplace=True)
                    df_sorted.index = df_sorted.index + 1
                    st.caption(f"총 {len(df_sorted)}개 결과")
                    st.dataframe(df_sorted[["이름", "rating"]], use_container_width=True, height=500)
                else:
                    st.warning("별점 정보가 없습니다.")
            
            with tabs[2]:
                if "review_cnt" in filtered.columns:  # ✅ 수정됨
                    df_sorted = filtered.sort_values("review_cnt", ascending=False).copy()
                    df_sorted.rename(columns={"name_g": "이름"}, inplace=True)
                    df_sorted.reset_index(drop=True, inplace=True)
                    df_sorted.index = df_sorted.index + 1
                    st.caption(f"총 {len(df_sorted)}개 결과")
                    st.dataframe(df_sorted[["이름", "review_cnt"]], use_container_width=True, height=500)
                else:
                    st.warning("리뷰 수 정보가 없습니다.")
            
            with tabs[3]:
                render_map(user_lat, user_lon, filtered)

        col_prev, col_next = st.columns([0.5, 0.5])
        with col_prev:
            if st.button("⬅ 이전 (Page1)", use_container_width=True):
                st.session_state.page = "page1"
                st.rerun()
        with col_next:
            if st.button("➡ 다음 (Page3)", use_container_width=True):
                st.session_state.page = "page3"
                st.rerun()

    # Page 3
    elif st.session_state.page == "page3":
        st.header("최종 선택")
        selected = st.session_state.get("selected_store")
        if selected is not None:
            st.image(selected.get("map_link", ""), caption="음식점 링크 이미지", use_column_width=True)
        col1, col2 = st.columns([0.5, 0.5])
        with col1:
            if st.button("⬅ 조금 더 둘러볼래요!"):
                st.session_state.clear()
                st.session_state.page = "page1"
                st.rerun()
        with col2:
            if st.button("맛집을 정했어요!"):
                st.success("맛집 선택이 완료되었습니다! 🎉")

if __name__ == "__main__":
    main()
