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
# 1. 호환용 쿼리 파라미터 함수 (Streamlit 1.38+ 대응)
# ───────────────────────────────
def get_query_params():
    try:
        return st.query_params
    except AttributeError:
        return st.experimental_get_query_params()

def set_query_params(**kwargs):
    try:
        st.query_params.clear()
        st.query_params.update(kwargs)
    except AttributeError:
        st.experimental_set_query_params(**kwargs)

# ───────────────────────────────
# 2. 유틸
# ───────────────────────────────
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
    return CATEGORY_ALIAS.get(str(name).strip(), str(name).strip())

def _normalize_label(s: str) -> str:
    if s is None: return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\-()]+", "", s)

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype is bool: continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE", "FALSE", "1", "0", "", "NAN"]).mean() > 0.8:
                frame[col] = vals.map({
                    "TRUE": True, "FALSE": False, "1": True, "0": False
                }).fillna(False)
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

def prettify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    df = df.copy()
    if "distance_m" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_m"], errors="coerce").apply(
            lambda x: f"{int(x)}m" if pd.notna(x) else ""
        )
    rename_map = {
        "name_g": "이름", "name": "이름", "place_name": "이름",
        "store_name": "이름", "상호명": "이름", "category": "업태",
        "rating": "별점", "review_cnt": "리뷰 수", "address": "주소",
        "도로명주소": "주소", "지번주소": "주소"
    }
    df.rename(columns=rename_map, inplace=True)
    return df

# ───────────────────────────────
# 3. API
# ───────────────────────────────
def fetch_weather(lat: float, lon: float) -> dict:
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    data = res.json()
    return {"id": data["weather"][0]["id"], "description": data["weather"][0]["description"], "temperature": data["main"]["temp"]}

def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not response or not response.data: return pd.DataFrame()
        df = pd.DataFrame(response.data)
        if "latitude" in df.columns and "longitude" in df.columns:
            df["distance_m"] = df.apply(lambda r: haversine((lat, lon), (r["latitude"], r["longitude"])) * 1000, axis=1).round(0).astype(int)
        return df
    except Exception as e:
        st.error(f"데이터 불러오기 실패: {e}")
        return pd.DataFrame()

# ───────────────────────────────
# 4. 필터링
# ───────────────────────────────
def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    if frame.empty: return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col = resolve_tf_column(frame, theme)
    if not col: return pd.DataFrame()
    out = frame[frame[col] == True].copy()
    if "distance_m" in out.columns: out = out.sort_values("distance_m")
    return out

# ───────────────────────────────
# 5. Main
# ───────────────────────────────
def main():
    if "page" not in st.session_state: st.session_state.page = "page1"

    user_lat, user_lon = get_user_location()

    try:
        w = fetch_weather(user_lat, user_lon)
        desc, temp = w["description"], w["temperature"]
    except:
        desc, temp = "알수없음", "?"

    # ─── 사이드바 복원 (3개 카드 유지)
    with st.sidebar:
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:10px;'>"
                    f"<h3>📍 현재 위치</h3><p>{user_lat:.4f}, {user_lon:.4f}</p></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:10px;'>"
                    f"<h3>🌤️ 현재 날씨</h3><p>{desc}, {temp}°C</p></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='background:#fff; border-radius:10px; padding:15px;'>"
                    f"<h3>💡 추천 키워드</h3><p>실내 중심, 든든한 한끼</p></div>", unsafe_allow_html=True)

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # ─── Page 1
    if st.session_state.page == "page1":
        st.header("현재 날씨에 추천 드리는 카테고리입니다.")
        opts = ["든든한 한끼", "디저트/카페", "가볍게 간단히"]
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)
        filtered_df = filter_by_category_tf(all_df, choice)
        st.subheader(f"‘{choice}’ 카테고리 음식점 (반경 500M)")

        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)
            df = df.reset_index(drop=True)
            df.index = df.index + 1

            # 💄 CSS 기반 표 디자인
            html_rows = ""
            for i, row in df.iterrows():
                name, dist, link = row.get("이름", ""), row.get("거리", ""), row.get("map_link", "")
                html_rows += f"""
                <tr>
                    <td style='text-align:center'>{i}</td>
                    <td style='padding:8px'>{name}</td>
                    <td style='text-align:center'>{dist}</td>
                    <td style='text-align:center'>
                        <a href='{link}' target='_blank'
                        style='color:black; border:1px solid black; padding:3px 8px;
                        border-radius:6px; text-decoration:none;'>열기 🔗</a>
                    </td>
                </tr>
                """
            st.markdown(f"""
            <div style='max-height:500px; overflow-y:auto'>
                <table style='width:100%; border-collapse:collapse;'>
                    <thead><tr><th>번호</th><th>이름</th><th>거리</th><th>링크</th></tr></thead>
                    <tbody>{html_rows}</tbody>
                </table>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.warning("해당 카테고리 음식점이 없습니다.")

        if st.button("➡ 다음"):
            st.session_state.choice = choice
            st.session_state.page = "page2"
            st.rerun()

    # ─── Page 2
    elif st.session_state.page == "page2":
        choice = st.session_state.get("choice")
        st.header(f"‘{choice}’ 카테고리 결과")

        filtered_df = filter_by_category_tf(all_df, choice)
        tabs = st.tabs(["거리순", "별점순", "리뷰순", "지도"])

        with tabs[0]:
            df = prettify_dataframe(filtered_df)
            df = df.sort_values("distance_m").reset_index(drop=True)
            df.index += 1

            html_rows = ""
            for i, row in df.iterrows():
                name = row.get("이름", "")
                dist = row.get("거리", "")
                link = row.get("map_link", "")
                html_rows += f"""
                    <tr>
                        <td style='text-align:center'>{i}</td>
                        <td style='padding:8px'>
                            <a href='#' onclick="window.parent.postMessage('{name}','*');"
                               style='color:#0056b3; font-weight:400; text-decoration:underline;'>{name}</a>
                        </td>
                        <td style='text-align:center'>{dist}</td>
                        <td style='text-align:center'>
                            <a href='{link}' target='_blank'
                            style='color:black; border:1px solid black; padding:3px 8px; border-radius:6px; text-decoration:none;'>열기 🔗</a>
                        </td>
                    </tr>
                """
            html_table = f"<table style='width:100%; border-collapse:collapse;'><thead><tr><th>번호</th><th>이름</th><th>거리</th><th>링크</th></tr></thead><tbody>{html_rows}</tbody></table>"
            st.markdown(f"<div style='max-height:500px; overflow-y:auto'>{html_table}</div>", unsafe_allow_html=True)

            # JS 메시지 수신 → 3페이지 전환
            st.markdown("""
            <script>
            window.addEventListener("message", (event) => {
                if (event.data){
                    const store = event.data;
                    window.parent.streamlitAPI.setComponentValue(store);
                }
            });
            </script>
            """, unsafe_allow_html=True)

            store = st.experimental_get_query_params().get("store", [None])[0]
            if store:
                st.session_state.selected_store = store
                st.session_state.page = "page3"
                set_query_params()
                st.rerun()

        if st.button("⬅ 이전"):
            st.session_state.page = "page1"
            st.rerun()

    # ─── Page 3
    elif st.session_state.page == "page3":
        st.header("🍽️ 선택한 음식점 정보")

        store_name = st.session_state.get("selected_store")
        if not store_name:
            st.warning("선택된 음식점이 없습니다.")
        else:
            row = all_df[all_df["name"] == store_name].iloc[0]
            name = row.get("name", "")
            addr = row.get("address", "")
            rating = row.get("rating", "정보 없음")
            review = row.get("review_cnt", "정보 없음")
            dist = row.get("distance_m", "")
            link = row.get("map_link", "")

            st.markdown(f"""
            <div style='background:#fff; padding:20px; border:1px solid #ddd; border-radius:15px;
                        box-shadow:0 4px 10px rgba(0,0,0,0.1); max-width:700px;'>
                <h2 style='margin-bottom:10px;'>{name}</h2>
                <p><b>📍 주소:</b> {addr}</p>
                <p><b>📏 거리:</b> {dist}m</p>
                <p><b>⭐ 별점:</b> {rating}</p>
                <p><b>💬 리뷰 수:</b> {review}</p>
                <p><a href='{link}' target='_blank'
                      style='color:black; border:1px solid black; padding:5px 10px; border-radius:6px; text-decoration:none;'>지도에서 보기 🔗</a></p>
            </div>
            """, unsafe_allow_html=True)

            st.success("맛집 선택이 완료되었습니다! 🎉")

        if st.button("⬅ 다시 선택"):
            st.session_state.page = "page2"
            st.rerun()


if __name__ == "__main__":
    main()
