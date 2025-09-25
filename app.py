import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
import requests
from supabase import create_client
import os
from dotenv import load_dotenv
import re
import math
import pydeck as pdk

# -- 세션 상태 초기화 (반드시 필요)
if "page" not in st.session_state:
    st.session_state.page = "page1"

# -- 환경변수 로드 및 Supabase 클라이언트 생성
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

# -- 상수 및 헬퍼 함수 정의
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

def norm_cat(name):
    CATEGORY_ALIAS = {
        "시원한 한끼": "시원한 음식",
        "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
        "가족/단체회식": "가족/단체 외식",
        "패스트푸드/배달": "패스트푸드",
        "헤산물/생선요리": "해산물/생선요리",
    }
    return CATEGORY_ALIAS.get(name, name)

def _normalize_label(s):
    if s is None:
        return ""
    return re.sub(r"[\s/_\-()]+", "", str(s).lower())

def coerce_tf_bool(frame):
    for col in frame.columns:
        if frame[col].dtype is bool:
            continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE", "FALSE", "1", "0"]).mean() > 0.8:
                frame[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
    return frame

def resolve_tf_column(frame, expected_label):
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
    "클리어": {
        "mood": "야외활동, 기분전환",
        "cats": [
            "이국적인 음식",
            "디저트/카페",
            "술 한잔 하기 좋은 날",
            "가볍게 간단히",
            "시원한 음식",
            "해산물/생선요리",
        ],
    },
    "구름": {
        "mood": "실내 중심, 편안함, 든든함 추구",
        "cats": [
            "든든한 한끼",
            "뜨끈한 국물",
            "디저트/카페",
            "시원한 한끼",
            "해산물/생선요리",
        ],
    },
    "비": {
        "mood": "외출 불편, 따뜻하거나 자극적인 음식",
        "cats": [
            "뜨끈한 국물",
            "매콤한 음식",
            "술 한잔 하기 좋은 날",
            "패스트푸드/배달",
            "시원한 한끼",
        ],
    },
    "이슬비": {
        "mood": "정적이거나 가벼운 공간",
        "cats": [
            "디저트/카페",
            "가볍게 간단히",
            "건강/채식/특수식단",
            "해산물/생선요리",
        ],
    },
    "뇌우": {
        "mood": "외출 최소화, 실내고정",
        "cats": [
            "육류구이/고기파티",
            "든든한 한끼",
            "패스트푸드/배달",
        ],
    },
    "눈": {
        "mood": "감성적, 따뜻함 추구",
        "cats": [
            "뜨끈한 국물",
            "육류구이/고기파티",
            "가족/단체회식",
            "디저트/카페",
            "해산물/생선요리",
        ],
    },
    "분위기": {
        "mood": "안개/먼지 등, 건강 고려",
        "cats": [
            "건강/채식/특수식단",
            "뜨끈한 국물",
            "패스트푸드/배달",
        ],
    },
}

def weather_group_from_id(weather_id):
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"

def recommended_categories_from_group(group_name):
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return cats, mood

@st.cache_data(ttl=600)
def fetch_weather(lat, lon):
    if not OPENWEATHER_API_KEY:
        raise RuntimeError("OpenWeather API 키가 설정되지 않았습니다.")
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {
        "id": data["weather"][0]["id"],
        "description": data["weather"][0]["description"],
        "temperature": data["main"]["temp"],
    }

@st.cache_data(ttl=600)
def get_restaurant_within_500m_from_supabase(lat, lon):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not response or not response.data:
            return pd.DataFrame()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다. ({e})")
        return pd.DataFrame()

def filter_by_category_tf(frame, theme):
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col_name = resolve_tf_column(frame, theme)
    if not col_name:
        return pd.DataFrame()
    out = frame[frame[col_name] == True].copy()
    if "distance_m" in out.columns:
        out["distance_m"] = pd.to_numeric(out["distance_m"], errors="coerce")
        out = out.sort_values("distance_m")
    return out

def format_distance(val, unit="m"):
    try:
        if pd.isna(val):
            return ""
        dist = float(val)
        if unit == "km":
            return f"{dist/1000:.2f}km"
        return f"{int(dist)}m"
    except Exception:
        return ""

# -- Streamlit 페이지 기본 설정
st.set_page_config(page_title="날씨+위치 기반 음식점 추천", page_icon="🍜", layout="wide")

# -- 메인 실행 부분
def main():
    st.title("날씨 + 위치 기반 음식점 추천 🌨️")

    user_lat, user_lon = get_user_location()

    # 날씨, 추천 카테고리 가져오기
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except Exception as e:
        st.error(f"날씨 정보를 불러오는 데 실패했습니다: {e}")
        opts = ["한식", "중식", "일식", "양식"]  # 기본값
        mood = "실내 중심, 편안함"

    # 음식점 데이터 가져오기
    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # 사이드바
    with st.sidebar:
        st.markdown(f"""
        <div style="background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;">
            <h3>📍 현재 위치</h3>
            <p>위도: {user_lat:.4f}, 경도: {user_lon:.4f}</p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"""
        <div style="background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;">
            <h3>🌤️ 현재 날씨</h3>
            <p><b>{w.get('description', 'N/A')}</b></p>
            <p>기온: {w.get('temperature', 'N/A')}°C</p>
        </div>
        """, unsafe_allow_html=True)
        mood_tags = [tag.strip() for tag in mood.split(',')]
        keywords_html = "".join([f'<p style="color:#007bff; font-weight:600;"># {tag}</p>' for tag in mood_tags])
        st.markdown(f"""
        <div style="background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;">
            <h3>💡 추천 키워드</h3>
            {keywords_html}
        </div>
        """, unsafe_allow_html=True)

    # 페이지 분기
    if "page" not in st.session_state:
        st.session_state.page = "page1"

    if st.session_state.page == "page1":
        st.subheader("지금 날씨에 어울리는 음식 카테고리")
        choice = st.radio("선택해 주세요 👇", options=opts, horizontal=True)
        wx_df = filter_by_category_tf(all_df, choice)

        st.write("해당 카테고리에 해당되는 반경 500M 내 음식점 입니다.")
        if wx_df is not None and not wx_df.empty:
            st.dataframe(wx_df[["name", "distance_m"]].rename(columns={"name": "이름", "distance_m": "거리"}))
        else:
            st.warning("해당 카테고리 음식점이 없습니다.")

        # 항상 '다음' 버튼이 보여야 함
        if st.button("다음"):
            st.session_state.choice = choice
            st.session_state.page = "page2"
            st.rerun()

    elif st.session_state.page == "page2":
        st.subheader("업태 선택")
        choice = st.session_state.get("choice", None)
        if choice is None:
            st.warning("Page1에서 먼저 선택해 주세요.")
            if st.button("이전"):
                st.session_state.page = "page1"
                st.rerun()
            st.stop()

        wx_df = filter_by_category_tf(all_df, choice)

        final_filtered_df, _ = pd.DataFrame(), []
        if not wx_df.empty:
            # 업태 다중 선택 필터
            cats_all = sorted([cat for cat in wx_df["category"].dropna().unique() if cat])
            selected_categories = st.multiselect("업태를 선택해 더 자세히 필터링하세요", options=cats_all)
            final_filtered_df = wx_df
            if selected_categories:
                final_filtered_df = wx_df[wx_df["category"].isin(selected_categories)].copy()
            st.caption(f"선택된 업태: {', '.join(selected_categories)} (총 {len(final_filtered_df)}곳)")

        if st.button("다음"):
            st.session_state.filtered = final_filtered_df
            st.session_state.page = "page3"
            st.rerun()
        if st.button("이전"):
            st.session_state.page = "page1"
            st.rerun()

    elif st.session_state.page == "page3":
        st.subheader("결과 확인")
        final_filtered_df = st.session_state.get("filtered", pd.DataFrame())
        if final_filtered_df.empty:
            st.warning("Page2에서 먼저 필터링해 주세요.")
        else:
            st.dataframe(final_filtered_df)

        if st.button("이전"):
            st.session_state.page = "page2"
            st.rerun()

if __name__ == "__main__":
    main()

