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

st.set_page_config(
    page_title="날씨 + 위치 기반 음식점 추천",
    page_icon="🍜",
    layout="wide"
)
st.title("날씨 + 위치 기반 음식점 추천 🌨️")

# ───────────────────────────────
# 1. 유틸
# ───────────────────────────────
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    try:
        loc = streamlit_geolocation()
        if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
            return seoul_lat, seoul_lon
        return float(loc["latitude"]), float(loc["longitude"])
    except Exception:
        return seoul_lat, seoul_lon


CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
}

def norm_cat(name: str) -> str:
    return CATEGORY_ALIAS.get(str(name).strip(), str(name).strip())

def _normalize_label(s: str) -> str:
    if s is None:
        return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\-()]+", "", s)

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype is bool:
            continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE", "FALSE", "1", "0", "", "NAN"]).mean() > 0.8:
                frame[col] = vals.map({
                    "TRUE": True, "FALSE": False, "1": True, "0": False
                }).fillna(False)
    return frame

def resolve_tf_column(frame: pd.DataFrame, expected_label: str) -> str | None:
    expected = norm_cat(expected_label)
    if expected in frame.columns:
        return expected
    want = _normalize_label(expected)
    normalized = {str(c): _normalize_label(str(c)) for c in frame.columns}
    for col, key in normalized.items():
        if key == want:
            return col
    for col, key in normalized.items():
        if want in key:
            return col
    return None

def prettify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    if "distance_m" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_m"], errors="coerce").apply(
            lambda x: f"{int(x)}m" if pd.notna(x) else ""
        )
    rename_map = {
        "name_g": "이름", "name": "이름", "place_name": "이름",
        "store_name": "이름", "상호명": "이름",
        "category": "업태", "rating": "별점", "review_cnt": "리뷰 수",
        "address": "주소", "도로명주소": "주소", "지번주소": "주소",
    }
    df.rename(columns=rename_map, inplace=True)
    return df

# ───────────────────────────────
# 2. 날씨 그룹 & 추천 카테고리
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
    "클리어": {"mood": "야외활동, 기분전환, 걷기 좋은 날",
               "cats": ["이국적인 음식", "디저트/카페", "술 한잔 하기 좋은 날",
                        "가볍게 간단히", "시원한 한끼", "해산물/생선요리"]},
    "구름": {"mood": "실내 중심, 편안함, 든든함 추구",
             "cats": ["든든한 한끼", "뜨끈한 국물", "디저트/카페",
                      "시원한 한끼", "해산물/생선요리"]},
    "비": {"mood": "외출 불편, 따뜻하거나 자극적인 음식",
           "cats": ["뜨끈한 국물", "매콤한 음식", "술 한잔 하기 좋은 날",
                    "패스트푸드/배달", "시원한 한끼"]},
    "이슬비": {"mood": "활동 가능하지만 귀찮음",
               "cats": ["디저트/카페", "가볍게 간단히",
                        "건강/채식/특수식단", "해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내 고정",
             "cats": ["육류구이/고기파티", "든든한 한끼", "패스트푸드/배달"]},
    "눈": {"mood": "실내, 감성적, 따뜻함 추구",
           "cats": ["뜨끈한 국물", "육류구이/고기파티",
                    "가족/단체회식", "디저트/카페", "해산물/생선요리"]},
    "분위기": {"mood": "안개/먼지 등 건강 고려",
               "cats": ["건강/채식/특수식단", "뜨끈한 국물", "패스트푸드/배달"]},
}
def weather_group_from_id(weather_id: int) -> str:
    for g, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return g
    return "구름"
def recommended_categories_from_group(g: str, top_k: int | None = None):
    cats = [norm_cat(c) for c in WX_RECO[g]["cats"]]
    mood = WX_RECO[g]["mood"]
    return (cats, mood) if top_k is None else (cats[:top_k], mood)

# ───────────────────────────────
# 3. API
# ───────────────────────────────
def fetch_weather(lat: float, lon: float) -> dict:
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    d = res.json()
    return {"id": d["weather"][0]["id"], "description": d["weather"][0]["description"], "temperature": d["main"]["temp"]}

def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        res = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not res or res.data is None or len(res.data) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        if "latitude" in df.columns and "longitude" in df.columns:
            df["distance_m"] = df.apply(lambda r: haversine((lat, lon), (r["latitude"], r["longitude"])) * 1000, axis=1).round(0).astype(int)
        return df
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

# ───────────────────────────────
# 4. 필터링
# ───────────────────────────────
def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col_name = resolve_tf_column(frame, theme)
    if not col_name:
        return pd.DataFrame()
    out = frame[frame[col_name] == True].copy()
    if "distance_m" in out.columns:
        out = out.sort_values("distance_m")
    return out

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
        w = {"description": "알수없음", "temperature": "?"}
        group_name, opts, mood = "구름", ["가볍게 간단히", "든든한 한끼", "디저트/카페"], "실내 중심"

    with st.sidebar:
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px;'>"
                    f"<h3>📍 현재 위치</h3><p>{user_lat:.4f}, {user_lon:.4f}</p>"
                    f"<h3>🌤️ 날씨</h3><p>{w['description']}, {w['temperature']}°C</p>"
                    f"<h3>💡 키워드</h3><p>{mood}</p></div>", unsafe_allow_html=True)

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # PAGE 1
    if st.session_state.page == "page1":
        st.header("현재 날씨에 추천 드리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)
        filtered_df = filter_by_category_tf(all_df, choice)

        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)
            if "map_link" not in df.columns:
                df["map_link"] = ""
            df = df.reset_index(drop=True)
            df.index = df.index + 1

            rows = ""
            for i, row in df.iterrows():
                name, dist = row["이름"], row.get("거리", "")
                link = row.get("map_link", "")
                rows += f"<tr><td style='text-align:center'>{i}</td><td>{name}</td><td style='text-align:center'>{dist}</td><td style='text-align:center'><a href='{link}' target='_blank' style='color:black; border:1px solid black; padding:5px 10px; border-radius:6px; text-decoration:none;'>열기 🔗</a></td></tr>"
            table_html = f"<table style='width:100%; border-collapse:collapse;'><thead><tr><th>번호</th><th>이름</th><th>거리</th><th>링크</th></tr></thead><tbody>{rows}</tbody></table>"
            st.markdown(f"<div style='max-height:500px; overflow-y:auto'>{table_html}</div>", unsafe_allow_html=True)
        else:
            st.warning("해당 카테고리 음식점이 없습니다.")

        if st.button("➡ 다음"):
            st.session_state.choice = choice
            st.session_state.page = "page2"
            st.rerun()

    # PAGE 2
    elif st.session_state.page == "page2":
        choice = st.session_state.get("choice")
        st.header(f"‘{choice}’ 카테고리 결과")
        filtered_df = filter_by_category_tf(all_df, choice)

        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)
            df = df.reset_index(drop=True)
            df.index = df.index + 1

            rows = ""
            for i, row in df.iterrows():
                name, dist = row["이름"], row.get("거리", "")
                link = row.get("map_link", "")
                rows += f"""
                <tr>
                    <td style='text-align:center'>{i}</td>
                    <td style='padding:8px;'>
                        <a href='?store={name}' style='color:blue; text-decoration:none; font-weight:bold;'>{name}</a>
                    </td>
                    <td style='text-align:center'>{dist}</td>
                    <td style='text-align:center'>
                        <a href='{link}' target='_blank' style='color:black; border:1px solid black; padding:4px 8px; border-radius:6px; text-decoration:none;'>링크 🔗</a>
                    </td>
                </tr>
                """
            table_html = f"<table style='width:100%; border-collapse:collapse;'><thead><tr><th>번호</th><th>이름</th><th>거리</th><th>링크</th></tr></thead><tbody>{rows}</tbody></table>"
            st.markdown(f"<div style='max-height:500px; overflow-y:auto'>{table_html}</div>", unsafe_allow_html=True)

            params = st.experimental_get_query_params()
            store = params.get("store", [None])[0]
            if store:
                st.session_state.selected_store = store
                st.session_state.page = "page3"
                st.experimental_set_query_params()  # URL 파라미터 초기화
                st.rerun()

        if st.button("⬅ 이전"):
            st.session_state.page = "page1"
            st.rerun()

    # PAGE 3
    elif st.session_state.page == "page3":
        st.header("🍽️ 선택한 음식점 정보")

        store_name = st.session_state.get("selected_store")
        if not store_name:
            st.warning("선택된 음식점이 없습니다.")
        else:
            row = all_df[all_df["name"] == store_name].iloc[0]
            name = row.get("name", "")
            address = row.get("address", "")
            rating = row.get("rating", "정보 없음")
            review_cnt = row.get("review_cnt", "정보 없음")
            dist = row.get("distance_m", "")
            link = row.get("map_link", "")

            st.markdown(f"""
            <div style='border:1px solid #ddd; border-radius:15px; padding:20px; background-color:#fff;
                        box-shadow:0 4px 10px rgba(0,0,0,0.1); max-width:700px;'>
                <h2 style='margin-bottom:10px; color:#222;'>{name}</h2>
                <p><b>📍 주소:</b> {address}</p>
                <p><b>📏 거리:</b> {dist}m</p>
                <p><b>⭐ 별점:</b> {rating}</p>
                <p><b>🗨️ 리뷰 수:</b> {review_cnt}</p>
                <p><a href='{link}' target='_blank' 
                      style='color:black; border:1px solid black; padding:5px 10px; border-radius:6px; text-decoration:none;'>지도에서 보기 🔗</a></p>
            </div>
            """, unsafe_allow_html=True)

        if st.button("⬅ 다시 선택"):
            st.session_state.page = "page2"
            st.rerun()

if __name__ == "__main__":
    main()
