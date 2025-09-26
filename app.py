import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
import requests
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import pydeck as pdk
from typing import List, Tuple
import math
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

        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

# ───────────────────────────────
# 4. UI Helper (디자인 유지)
# ───────────────────────────────
def render_paginated_clickable_name_table(
    frame: pd.DataFrame,
    table_key: str,
    page_size: int = 10,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        st.info("표시할 식당이 없습니다.")
        return pd.DataFrame()

    view_df = frame.copy()

    # 거리 기준 정렬
    if "distance_m" in view_df.columns:
        view_df["distance_m"] = pd.to_numeric(view_df["distance_m"], errors="coerce")
        view_df = view_df.sort_values("distance_m")

    total = len(view_df)
    total_pages = max(1, math.ceil(total / page_size))
    state_key = f"page_{table_key}"
    page = int(st.session_state.get(state_key, 1))

    if page < 1: page = 1
    if page > total_pages: page = total_pages

    col_prev, col_center, col_next = st.columns([0.2, 0.6, 0.2])
    with col_prev:
        prev_click = st.button("◀ 이전", key=f"{table_key}_prev", disabled=(page <= 1))
    label_placeholder = col_center.empty()
    with col_next:
        next_click = st.button("다음 ▶", key=f"{table_key}_next", disabled=(page >= total_pages))

    if prev_click and page > 1:
        page -= 1
    if next_click and page < total_pages:
        page += 1
    st.session_state[state_key] = page

    start = (page - 1) * page_size
    end = start + page_size
    page_df = view_df.iloc[start:end].copy()

    rows_html = []
    for _, row in page_df.iterrows():
        nm = row.get("name_g", "이름 없음")
        dist = row.get("distance_m", "")
        dist_str = f"{int(dist)}m" if pd.notna(dist) else ""
        rows_html.append(f"<tr><td>{nm}</td><td>{dist_str}</td></tr>")

    thead = "<tr><th>이름</th><th>거리</th></tr>"
    table_html = f"""
    <div style="border:1px solid #e6e6e6; border-radius:8px; overflow:hidden">
      <table style="width:100%; border-collapse:collapse; font-size:14px">
        <thead style="position: sticky; top: 0; background: #fafafa; z-index: 1;">{thead}</thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)

    label_placeholder.markdown(
        f"<div style='text-align:center; font-size:13px; padding-top:6px'>"
        f"Page <b>{page}</b> / {total_pages}</div>",
        unsafe_allow_html=True,
    )

    return page_df

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

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # Page 1
    if st.session_state.page == "page1":
        st.header("현재 날씨에 추천 드리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)
        st.markdown("---")
        st.subheader(f"‘{choice}’ 카테고리에 해당되는 음식점")

        filtered_df = all_df[all_df["category"].map(norm_cat) == norm_cat(choice)] if "category" in all_df.columns else all_df
        _ = render_paginated_clickable_name_table(filtered_df, table_key="page1", page_size=10)

        if st.button("➡ 다음 (Page2)"):
            st.session_state.filtered = filtered_df
            st.session_state.page = "page2"
            st.rerun()

    # Page 2
    elif st.session_state.page == "page2":
        st.header("카테고리에 해당하는 식당입니다. 정렬 방식을 선택하세요!")

        filtered = st.session_state.get("filtered", pd.DataFrame())
        if filtered.empty:
            st.warning("이전 단계에서 선택된 식당이 없습니다.")
        else:
            tabs = st.tabs(["거리순", "별점순", "리뷰순"])
            
            with tabs[0]:
                if "distance_m" in filtered.columns:
                    df_sorted = filtered.sort_values("distance_m")
                    render_paginated_clickable_name_table(df_sorted, table_key="dist", page_size=10)
            with tabs[1]:
                if "rating" in filtered.columns:
                    df_sorted = filtered.sort_values("rating", ascending=False)
                    render_paginated_clickable_name_table(df_sorted, table_key="rating", page_size=10)
            with tabs[2]:
                if "review_count" in filtered.columns:
                    df_sorted = filtered.sort_values("review_count", ascending=False)
                    render_paginated_clickable_name_table(df_sorted, table_key="review", page_size=10)

        if st.button("➡ 다음 (Page3)"):
            st.session_state.page = "page3"
            st.rerun()

    # Page 3
    elif st.session_state.page == "page3":
        st.header("최종 선택 단계 🎉")
        st.success("맛집 선택이 완료되었습니다!")

        if st.button("⬅ 처음으로"):
            st.session_state.page = "page1"
            st.rerun()

if __name__ == "__main__":
    main()
