import streamlit as st
import pandas as pd
from streamlit_geolocation import streamlit_geolocation
from haversine import haversine
import requests
import psycopg2
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import re
import random
from typing import Tuple, List

# ---------------------------------
# 초기 설정 및 환경 변수 로드
# ---------------------------------
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

st.set_page_config(
    page_title="날씨 + 위치 기반 음식점 추천",
    page_icon="🍜",
    layout="wide",
)

# ---------------------------------
# 새로운 CSS 스타일
# ---------------------------------
# 사이드 정보 박스, 스크롤 테이블, 메인 타이틀 등을 위한 CSS
NEW_THEME_CSS = """
<style>
    /* 기본 레이아웃 및 폰트 */
    .main-title {
        font-size: 2.5rem;
        font-weight: 700;
        padding-bottom: 10px;
        border-bottom: 2px solid #eee;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
    }
    .main-title img {
        height: 40px;
        margin-left: 15px;
    }

    /* 좌측 정보 패널 */
    .info-container {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 20px;
        height: 100%;
    }
    .info-box {
        margin-bottom: 25px;
    }
    .info-box h3 {
        font-size: 1.1rem;
        font-weight: 600;
        margin-bottom: 10px;
        padding-bottom: 5px;
        border-bottom: 1px solid #ddd;
    }
    .info-box p {
        margin: 0;
        font-size: 0.95rem;
    }
    .info-box .location-wrapper {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    
    /* 추천 키워드 스타일 */
    .keyword-box {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .keyword {
        font-size: 1rem;
        font-weight: 600;
        color: #007bff;
    }

    /* 스크롤 가능한 테이블 */
    .table-container {
        height: 400px;
        overflow-y: auto;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
    }
    table {
        width: 100%;
        border-collapse: collapse;
    }
    th, td {
        padding: 12px 15px;
        text-align: left;
        border-bottom: 1px solid #e0e0e0;
    }
    th {
        background-color: #f8f9fa;
        position: sticky;
        top: 0;
    }
    tr:last-child td {
        border-bottom: none;
    }
    a {
        text-decoration: none;
        color: #007bff;
        font-weight: 500;
    }
    a:hover {
        text-decoration: underline;
    }

    /* "다음" 버튼을 오른쪽으로 정렬하기 위한 컨테이너 */
    .stButton {
        display: flex;
        justify-content: flex-end;
        margin-top: 20px;
    }
</style>
"""
st.markdown(NEW_THEME_CSS, unsafe_allow_html=True)


# ---------------------------------
# 기존 헬퍼 함수들 (일부 수정 및 유지)
# ---------------------------------
# (이 부분은 기존 코드의 함수들을 대부분 그대로 사용합니다)
# CATEGORY_ALIAS, WX_GROUPS, WX_RECO 등은 변경 없이 사용

CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식", "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식", "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
}

WX_GROUPS = {
    "맑음": [800], "구름": [801, 802, 803, 804],
    "비": [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우": [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈": [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "대기": [701, 711, 721, 731, 741, 751, 761, 762],
}
WX_RECO = {
    "맑음": {"mood": "야외활동, 기분전환", "cats": ["이국적인 음식", "디저트/카페", "술 한잔 하기 좋은날", "가볍게 간단히", "시원한 음식", "해산물/생선요리"]},
    "구름": {"mood": "실내, 편안함, 든든함", "cats": ["든든한 한끼", "뜨끈한 국물", "디저트/카페", "시원한 음식", "해산물/생선요리"]},
    "비": {"mood": "따뜻하거나 자극적인 음식", "cats": ["뜨끈한 국물", "매콤한 음식", "술 한잔 하기 좋은날", "패스트푸드", "부침개/전"]},
    "이슬비": {"mood": "정적이거나 가벼운 공간", "cats": ["디저트/카페", "가볍게 간단히", "건강/채식", "해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내", "cats": ["육류구이", "든든한 한끼", "패스트푸드"]},
    "눈": {"mood": "감성적, 따뜻함 추구", "cats": ["뜨끈한 국물", "육류구이", "가족/단체 외식", "디저트/카페"]},
    "대기": {"mood": "안개/먼지, 건강 고려", "cats": ["건강/채식", "뜨끈한 국물", "패스트푸드"]},
}
# (norm_cat, _normalize_label, coerce_tf_bool, resolve_tf_column 등 기존 함수 유지)
def norm_cat(name: str) -> str:
    return CATEGORY_ALIAS.get(name, name)

def _normalize_label(s: str) -> str:
    if s is None: return ""
    return re.sub(r"[\s/_\-()]+", "", str(s).lower())

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype != object: continue
        vals = frame[col].astype(str).str.strip().str.upper()
        if vals.isin(["TRUE", "FALSE", "1", "0", "", "NAN"]).mean() > 0.8:
            frame[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
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

def weather_group_from_id(weather_id: int) -> str:
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"

def recommended_categories_from_group(group_name: str):
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return cats, mood

@st.cache_data(ttl=600) # 10분 캐시
def fetch_weather(lat: float, lon: float) -> dict:
    if not OPENWEATHER_API_KEY:
        st.error("OpenWeather API 키가 설정되지 않았습니다.")
        return {}
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        return {
            "id": data["weather"][0]["id"],
            "description": data["weather"][0]["description"],
            "temperature": data["main"]["temp"],
            "icon": data["weather"][0]["icon"]
        }
    except requests.RequestException as e:
        st.error(f"날씨 정보 로딩 실패: {e}")
        return {}

@st.cache_data(ttl=600)
def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not response.data:
            return pd.DataFrame()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    if frame.empty: return pd.DataFrame()
    frame = coerce_tf_bool(frame.copy())
    col_name = resolve_tf_column(frame, theme)
    if not col_name: return pd.DataFrame()
    
    out = frame[frame[col_name] == True].copy()
    
    if "distance_m" in out.columns:
        out["distance_m"] = pd.to_numeric(out["distance_m"], errors="coerce")
        out = out.sort_values("distance_m")
    return out

def format_distance(value) -> str:
    if pd.isna(value): return ""
    return f"{int(round(float(value)))}m"


# ---------------------------------
# 새로운 UI 렌더링 함수
# ---------------------------------

def render_scrollable_table(df: pd.DataFrame):
    """디자인에 맞춘 스크롤 가능한 HTML 테이블을 생성합니다."""
    if df is None or df.empty:
        st.info("이 카테고리에는 주변 음식점이 없습니다. 다른 카테고리를 선택해보세요.")
        return

    # 컬럼명 자동 감지
    name_col = next((c for c in ["name", "place_name", "상호명"] if c in df.columns), df.columns[0])
    dist_col = next((c for c in ["distance_m", "distance"] if c in df.columns), None)
    link_col = next((c for c in ["map_link", "place_url"] if c in df.columns), None)

    table_rows = []
    for _, row in df.iterrows():
        name = row.get(name_col, "이름 없음")
        link = row.get(link_col, "#")
        distance = format_distance(row.get(dist_col)) if dist_col else ""
        
        table_rows.append(f"""
        <tr>
            <td><a href="{link}" target="_blank">{name}</a></td>
            <td>{distance}</td>
        </tr>
        """)

    st.markdown(f"""
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>이름</th>
                    <th>거리</th>
                </tr>
            </thead>
            <tbody>
                {''.join(table_rows)}
            </tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------
# 메인 애플리케이션 로직
# ---------------------------------
def main():
    # --- 1. 상태 초기화 (Session State) ---
    # 앱이 재실행되어도 값을 기억하도록 session_state 사용
    if 'page' not in st.session_state:
        st.session_state.page = 'selection'  # 현재 페이지 (selection or details)
    if 'user_lat' not in st.session_state:
        st.session_state.user_lat = 37.5665   # 서울 시청 기본값
        st.session_state.user_lon = 126.9780
    if 'weather' not in st.session_state:
        st.session_state.weather = None
    if 'all_df' not in st.session_state:
        st.session_state.all_df = pd.DataFrame()
    if 'selected_category' not in st.session_state:
        st.session_state.selected_category = None

    # --- 2. 데이터 로드 (최초 1회 또는 위치 변경 시) ---
    # st.session_state을 활용하여 불필요한 API 호출 방지
    if st.session_state.weather is None:
        st.session_state.weather = fetch_weather(st.session_state.user_lat, st.session_state.user_lon)
        st.session_state.all_df = get_restaurant_within_500m_from_supabase(st.session_state.user_lat, st.session_state.user_lon)

    weather_info = st.session_state.weather
    if not weather_info:
        st.error("날씨 정보를 가져올 수 없습니다. 앱을 새로고침 해주세요.")
        st.stop()

    # 날씨 그룹 및 추천 카테고리 결정
    weather_group = weather_group_from_id(weather_info['id'])
    categories, mood = recommended_categories_from_group(weather_group)
    
    # 첫 카테고리를 기본 선택값으로 설정
    if st.session_state.selected_category is None and categories:
        st.session_state.selected_category = categories[0]

    # --- 3. 화면 렌더링 ---
    # 메인 타이틀
    icon_url = f"http://openweathermap.org/img/wn/{weather_info.get('icon', '01d')}@2x.png"
    st.markdown(f'<div class="main-title">날씨 + 위치 기반 음식점 추천 <img src="{icon_url}" alt="weather icon"></div>', unsafe_allow_html=True)

    # 2단 레이아웃 생성
    left_col, right_col = st.columns([1, 2.2])

    # --- 좌측 정보 패널 ---
    with left_col:
        st.markdown('<div class="info-container">', unsafe_allow_html=True)
        
        # 현재 위치 박스
        with st.container():
            st.markdown("""
            <div class="info-box">
                <h3>📍 현재 위치</h3>
                <div class="location-wrapper">
                    <p>위도: {lat:.4f}, 경도: {lon:.4f}</p>
                </div>
            </div>
            """.format(lat=st.session_state.user_lat, lon=st.session_state.user_lon), unsafe_allow_html=True)
            
            # TODO: 실제 주소 변환 기능 추가 시 여기에 로직 삽입
            if st.button("위치 재설정", key="gps_button"):
                # geolocation은 버튼 클릭 시에만 호출되도록 구현 필요
                # loc = streamlit_geolocation() -> 이 부분은 실제 배포 환경에서 테스트 필요
                st.info("위치 재설정 기능은 준비 중입니다.")


        # 현재 날씨 박스
        with st.container():
            st.markdown(f"""
            <div class="info-box">
                <h3>🌤️ 현재 날씨</h3>
                <p><b>{weather_info['description']}</b><br>기온: {weather_info['temperature']:.1f}°C</p>
            </div>
            """, unsafe_allow_html=True)

        # 추천 핵심 키워드 박스
        with st.container():
            st.markdown('<div class="info-box"><h3>💡 추천 핵심 키워드</h3>', unsafe_allow_html=True)
            keywords = [f"#{mood.split(', ')[0]}", f"#{st.session_state.selected_category}", f"#{random.choice(['든든한', '따뜻한', '시원한'])}한끼"]
            st.markdown(f"""
                <div class="keyword-box">
                    <span class="keyword">{keywords[0]}</span>
                    <span class="keyword">{keywords[1]}</span>
                    <span class="keyword">{keywords[2]}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
        
        st.markdown('</div>', unsafe_allow_html=True)


    # --- 우측 메인 콘텐츠 ---
    with right_col:
        if st.session_state.page == 'selection':
            st.subheader("현재 날씨에 추천 드리는 카테고리입니다. 선택해주세요!")

            # 카테고리 선택 라디오 버튼
            selected = st.radio(
                "음식 카테고리",
                options=categories,
                index=categories.index(st.session_state.selected_category) if st.session_state.selected_category in categories else 0,
                label_visibility="collapsed"
            )
            # 사용자가 선택을 바꾸면 session_state 업데이트 후 rerun
            if selected != st.session_state.selected_category:
                st.session_state.selected_category = selected
                st.rerun()

            st.subheader(f"'{selected}' 카테고리에 해당되는 반경 500M 내 음식점 목록입니다.")

            # 선택된 카테고리에 따라 식당 목록 필터링
            filtered_df = filter_by_category_tf(st.session_state.all_df, selected)
            
            # 스크롤 테이블 렌더링
            render_scrollable_table(filtered_df)

            # 다음 버튼
            if st.button("다음 ▶", key="next_button"):
                st.session_state.page = 'details'
                st.rerun() # 페이지를 즉시 변경하기 위해 rerun 호출
        
        elif st.session_state.page == 'details':
            # --- 다음 페이지 로직 ---
            st.subheader(f"'{st.session_state.selected_category}' 카테고리 상세 보기")
            st.write("이 곳에 선택한 카테고리의 식당들을 지도와 함께 보여주는 등 상세 페이지를 구현할 수 있습니다.")
            
            # 이전으로 돌아가기 버튼
            if st.button("◀ 이전"):
                st.session_state.page = 'selection'
                st.rerun()


if __name__ == "__main__":
    main()
