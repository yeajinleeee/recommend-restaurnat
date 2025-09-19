# ---------------------------------
# 1. 라이브러리 임포트
# ---------------------------------
import streamlit as st                 # Streamlit 앱 프레임워크
import pandas as pd                    # 데이터 조작 및 분석을 위한 라이브러리
from streamlit_geolocation import streamlit_geolocation # 브라우저에서 사용자 위치를 가져오는 컴포넌트
from haversine import haversine        # 두 지점 간의 거리를 계산하는 라이브-러리 (현재 코드에서는 직접 사용 안함)
import requests                        # HTTP 요청을 보내기 위한 라이브러리 (API 호출용)
import psycopg2                        # PostgreSQL 데이터베이스 어댑터 (Supabase 연결용)
from supabase import create_client, Client # Supabase Python 클라이언트
import os                              # 운영체제와 상호작용하기 위한 라이브러리 (환경 변수 접근용)
from dotenv import load_dotenv         # .env 파일에서 환경 변수를 로드하기 위한 라이브러리
import re                              # 정규 표현식 라이브러리 (문자열 처리용)
import random                          # 난수 생성을 위한 라이브러리
from typing import Tuple, List         # 타입 힌팅을 위한 라이브러리

# ---------------------------------
# 2. 초기 설정 및 환경 변수 로드
# ---------------------------------
# .env 파일에 저장된 환경 변수를 로드합니다. (API 키 등 민감 정보 관리)
load_dotenv()

# Supabase 접속을 위한 URL과 API 키를 환경 변수에서 가져옵니다.
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
# Supabase 클라이언트를 생성하여 데이터베이스와 통신할 준비를 합니다.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# OpenWeatherMap API 키를 환경 변수에서 가져옵니다.
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

# Streamlit 페이지의 기본 설정을 구성합니다.
st.set_page_config(
    page_title="날씨 + 위치 기반 음식점 추천", # 브라우저 탭에 표시될 제목
    page_icon="🍜",                         # 브라우저 탭에 표시될 아이콘
    layout="wide",                         # 페이지 레이아웃을 넓게 설정
)

# ---------------------------------
# 3. UI 디자인을 위한 CSS 스타일
# ---------------------------------
# Streamlit 앱에 커스텀 CSS를 주입하여 디자인을 개선합니다.
IMPROVED_UI_CSS = """
<style>
    /* 전체 페이지 배경 및 기본 폰트 설정 */
    .stApp { background-color: #f0f2f5; font-family: 'Pretendard', sans-serif; }
    /* 헤더 스타일 */
    .header { background-color: #e3f2fd; padding: 1rem 2rem; border-radius: 10px; margin-bottom: 2rem; display: flex; align-items: center; justify-content: space-between; }
    .header h1 { font-size: 2rem; font-weight: 700; margin: 0; color: #333; }
    .header-icon { font-size: 2.5rem; }
    /* 좌측 정보 패널 컨테이너 */
    .info-container { background-color: white; border-radius: 10px; padding: 25px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05); height: 100%; }
    .info-box { margin-bottom: 25px; }
    .info-box-header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
    .info-box-header h3 { font-size: 1.1rem; font-weight: 600; margin: 0; }
    .info-box-content p { margin: 0; font-size: 0.95rem; line-height: 1.5; color: #555; }
    /* 추천 키워드 스타일 */
    .keyword-box { display: flex; flex-direction: column; gap: 8px; }
    .keyword { font-size: 1rem; font-weight: 600; color: #007bff; }
    /* 우측 메인 콘텐츠 */
    .main-content { padding-left: 20px; }
    .main-content h2 { font-size: 1.2rem; font-weight: 600; margin-top: 0; }
    /* 레스토랑 목록 스크롤 컨테이너 */
    .restaurant-list-container { height: 400px; overflow-y: auto; border: 1px solid #e0e0e0; border-radius: 8px; padding: 10px; background-color: #fff; }
    /* 커스텀 스크롤바 */
    .restaurant-list-container::-webkit-scrollbar { width: 8px; }
    .restaurant-list-container::-webkit-scrollbar-track { background: #f1f1f1; border-radius: 10px; }
    .restaurant-list-container::-webkit-scrollbar-thumb { background: #e57373; border-radius: 10px; }
    .restaurant-list-container::-webkit-scrollbar-thumb:hover { background: #d32f2f; }
    /* 레스토랑 각 항목 스타일 */
    .restaurant-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 15px; border-bottom: 1px solid #eee; }
    .restaurant-item:last-child { border-bottom: none; }
    .restaurant-item a { text-decoration: none; color: #007bff; font-weight: 500; }
    .restaurant-item a:hover { text-decoration: underline; }
    .restaurant-item .distance { color: #666; font-size: 0.9rem; }
    /* "다음" 버튼을 오른쪽으로 정렬 */
    .stButton { display: flex; justify-content: flex-end; margin-top: 20px; }
</style>
"""
st.markdown(IMPROVED_UI_CSS, unsafe_allow_html=True)


# ---------------------------------
# 4. 헬퍼 함수 및 상수 정의
# ---------------------------------

# 카테고리 이름의 미세한 차이를 통일하기 위한 별칭 맵
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식", "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식", "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
}
# OpenWeatherMap의 날씨 ID를 주요 날씨 그룹으로 매핑
WX_GROUPS = {
    "맑음": [800], "구름": [801, 802, 803, 804],
    "비": [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우": [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈": [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "대기": [701, 711, 721, 731, 741, 751, 761, 762],
}
# 날씨 그룹별 추천 분위기와 음식 카테고리를 정의한 맵 (핵심 추천 로직)
WX_RECO = {
    "맑음": {"mood": "야외활동, 기분전환", "cats": ["이국적인 음식", "디저트/카페", "술 한잔 하기 좋은날", "가볍게 간단히", "시원한 음식", "해산물/생선요리"]},
    "구름": {"mood": "실내, 편안함, 든든함", "cats": ["든든한 한끼", "뜨끈한 국물", "디저트/카페", "시원한 음식", "해산물/생선요리"]},
    "비": {"mood": "따뜻하거나 자극적인 음식", "cats": ["뜨끈한 국물", "매콤한 음식", "술 한잔 하기 좋은날", "패스트푸드", "부침개/전"]},
    "이슬비": {"mood": "정적이거나 가벼운 공간", "cats": ["디저트/카페", "가볍게 간단히", "건강/채식", "해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내", "cats": ["육류구이", "든든한 한끼", "패스트푸드"]},
    "눈": {"mood": "감성적, 따뜻함 추구", "cats": ["뜨끈한 국물", "육류구이", "가족/단체 외식", "디저트/카페"]},
    "대기": {"mood": "안개/먼지, 건강 고려", "cats": ["건강/채식", "뜨끈한 국물", "패스트푸드"]},
}
# UI에 표시될 날씨별 추천 키워드 (해시태그)
WEATHER_KEYWORDS = {
    "비": ["#비오는날엔", "#따끈한음식", "#자극적인맛"],
    "맑음": ["#화창한날", "#야외활동후", "#시원한한끼"],
    "구름": ["#흐린날씨", "#감성충만", "#든든한한끼"],
    "눈": ["#눈오는날", "#분위기맛집", "#따뜻하게"],
    "기타": ["#오늘뭐먹지", "#외식플랜", "#맛집탐방"]
}

# 위치 정보를 가져올 수 없을 때 사용할 기본 위치 (서울 시청)
seoul_lat, seoul_lon = 37.5665, 126.9780

def norm_cat(name: str) -> str:
    """카테고리 이름을 CATEGORY_ALIAS를 사용해 표준화합니다."""
    return CATEGORY_ALIAS.get(name, name)

def _normalize_label(s: str) -> str:
    """문자열에서 공백, 특수문자를 제거하고 소문자로 변환하여 비교하기 쉬운 형태로 만듭니다."""
    if s is None: return ""
    return re.sub(r"[\s/_\-()]+", "", str(s).lower())

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    """DataFrame에서 'TRUE', 'FALSE' 같은 문자열 컬럼을 실제 boolean 타입으로 변환합니다."""
    for col in frame.columns:
        if frame[col].dtype != object: continue
        vals = frame[col].astype(str).str.strip().str.upper()
        if vals.isin(["TRUE", "FALSE", "1", "0", "", "NAN"]).mean() > 0.8:
            frame[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
    return frame

def resolve_tf_column(frame: pd.DataFrame, expected_label: str) -> str | None:
    """DataFrame에서 약간 다른 이름의 컬럼(예: '술 한잔 하기 좋은 날' vs '술한잔하기좋은날')을 찾아 반환합니다."""
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
    """OpenWeatherMap의 날씨 ID를 WX_GROUPS를 참조하여 '맑음', '비' 등의 그룹 이름으로 변환합니다."""
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름" # 매칭되는 그룹이 없으면 '구름'으로 처리

def recommended_categories_from_group(group_name: str):
    """날씨 그룹 이름을 받아 WX_RECO를 참조하여 추천 카테고리 리스트와 분위기를 반환합니다."""
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return cats, mood

@st.cache_data(ttl=600) # 10분(600초) 동안 API 호출 결과를 캐싱하여 불필요한 호출 방지
def fetch_weather(lat: float, lon: float) -> dict:
    """주어진 위도/경도로 OpenWeatherMap API를 호출하여 현재 날씨 정보를 가져옵니다."""
    if not OPENWEATHER_API_KEY:
        st.error("OpenWeather API 키가 설정되지 않았습니다.")
        return {}
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status() # 요청 실패 시 예외 발생
        data = res.json()
        # 필요한 정보만 추출하여 딕셔너리 형태로 반환
        return {
            "id": data["weather"][0]["id"], "description": data["weather"][0]["description"],
            "temperature": data["main"]["temp"], "icon": data["weather"][0]["icon"]
        }
    except requests.RequestException as e:
        st.error(f"날씨 정보 로딩 실패: {e}")
        return {}

@st.cache_data(ttl=600) # 10분 동안 DB 쿼리 결과를 캐싱
def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    """주어진 위도/경도를 기준으로 Supabase DB의 'get_restaurant_within_500m' 함수를 호출하여 500m 내 음식점 목록을 가져옵니다."""
    try:
        # Supabase의 RPC(Remote Procedure Call) 기능을 사용해 DB 함수 실행
        response = supabase.rpc("get_restaurant_within_500m", {"user_lat": lat, "user_lng": lon}).execute()
        if not response.data: return pd.DataFrame() # 데이터가 없으면 빈 DataFrame 반환
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    """전체 음식점 DataFrame에서 사용자가 선택한 카테고리(theme)에 해당하는 음식점만 필터링합니다."""
    if frame.empty: return pd.DataFrame()
    frame = coerce_tf_bool(frame.copy())
    col_name = resolve_tf_column(frame, theme)
    if not col_name: return pd.DataFrame()
    out = frame[frame[col_name] == True].copy()
    if "distance_m" in out.columns:
        out["distance_m"] = pd.to_numeric(out["distance_m"], errors="coerce")
        out = out.sort_values("distance_m") # 거리순으로 정렬
    return out

def format_distance(value) -> str:
    """숫자 형태의 거리를 '123m'와 같은 문자열로 포맷팅합니다."""
    if pd.isna(value): return ""
    return f"{int(round(float(value)))}m"


# ---------------------------------
# 5. UI 렌더링 함수
# ---------------------------------
def render_restaurant_list(df: pd.DataFrame):
    """필터링된 음식점 DataFrame을 받아 스크롤 가능한 HTML 리스트로 렌더링합니다."""
    if df is None or df.empty:
        st.info("이 카테고리에는 주변 음식점이 없습니다. 다른 카테고리를 선택해보세요.")
        return

    # 데이터에 따라 유연하게 컬럼 이름 감지
    name_col = next((c for c in ["name", "place_name", "상호명"] if c in df.columns), df.columns[0])
    dist_col = next((c for c in ["distance_m", "distance"] if c in df.columns), None)
    link_col = next((c for c in ["map_link", "place_url"] if c in df.columns), None)

    # 각 음식점 항목을 HTML로 변환
    list_items = []
    for _, row in df.iterrows():
        name = row.get(name_col, "이름 없음")
        link = row.get(link_col, "#")
        distance = format_distance(row.get(dist_col)) if dist_col else ""
        
        list_items.append(f"""
        <div class="restaurant-item">
            <a href="{link}" target="_blank">{name}</a>
            <span class="distance">{distance}</span>
        </div>
        """)

    # 전체 HTML 구조를 만들어 Streamlit에 렌더링
    st.markdown(f"""
    <div class="restaurant-list-container">
        {''.join(list_items)}
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------
# 6. 메인 애플리케이션 로직
# ---------------------------------
def main():
    """Streamlit 앱의 메인 실행 함수"""
    
    # --- 1. 상태 초기화 ---
    # st.session_state: 사용자의 세션 동안 유지되는 변수 저장 공간. 앱이 재실행되어도 값이 유지됨.
    if 'page' not in st.session_state:
        st.session_state.page = 'selection' # 현재 페이지 상태 ('selection' 또는 'details')
    if 'user_lat' not in st.session_state:
        st.session_state.user_lat = seoul_lat # 사용자 위도 (기본값: 서울 시청)
        st.session_state.user_lon = seoul_lon # 사용자 경도
    if 'weather' not in st.session_state:
        st.session_state.weather = None # 날씨 정보
    if 'all_df' not in st.session_state:
        st.session_state.all_df = pd.DataFrame() # 주변 모든 음식점 데이터
    if 'selected_category' not in st.session_state:
        st.session_state.selected_category = None # 사용자가 선택한 카테고리

    # --- 2. 데이터 로드 ---
    # 날씨 정보가 없으면 (앱 최초 실행 또는 위치 변경 시) API와 DB에서 데이터를 가져옴
    if st.session_state.weather is None:
        st.session_state.weather = fetch_weather(st.session_state.user_lat, st.session_state.user_lon)
        st.session_state.all_df = get_restaurant_within_500m_from_supabase(st.session_state.user_lat, st.session_state.user_lon)

    weather_info = st.session_state.weather
    if not weather_info:
        st.error("날씨 정보를 가져올 수 없습니다. 앱을 새로고침 해주세요.")
        st.stop() # 날씨 정보 없으면 앱 실행 중지

    # 날씨 정보 기반으로 추천 카테고리 결정
    weather_group = weather_group_from_id(weather_info['id'])
    categories, mood = recommended_categories_from_group(weather_group)
    
    # 선택된 카테고리가 없으면 추천 카테고리의 첫 번째 항목을 기본값으로 설정
    if st.session_state.selected_category is None and categories:
        st.session_state.selected_category = categories[0]

    # --- 3. 화면 렌더링 ---
    # 헤더 렌더링
    weather_icons = {"비": "🌧️", "맑음": "☀️", "구름": "☁️", "눈": "❄️"}
    header_icon = weather_icons.get(weather_group, "🌤️")
    st.markdown(f"""
    <div class="header">
        <h1>날씨 + 위치 기반 음식점 추천</h1>
        <span class="header-icon">{header_icon}</span>
    </div>
    """, unsafe_allow_html=True)

    # 화면을 2단으로 분할
    left_col, right_col = st.columns([1, 2])

    # --- 좌측 정보 패널 ---
    with left_col:
        st.markdown('<div class="info-container">', unsafe_allow_html=True)

        # 현재 위치 표시 및 재설정 기능
        st.markdown("""
        <div class="info-box">
            <div class="info-box-header">
                <span>📍</span><h3>현재 위치</h3>
            </div>
            <div class="info-box-content">
                <p>위도: {lat:.4f}, 경도: {lon:.4f}</p>
            </div>
        </div>
        """.format(lat=st.session_state.user_lat, lon=st.session_state.user_lon), unsafe_allow_html=True)
        
        # 위치 재설정 버튼 렌더링
        st.write("버튼을 눌러 현재 위치로 재설정하세요.")
        loc = streamlit_geolocation() # 이 컴포넌트가 'Get Location' 버튼을 생성
        
        # 새 위치 정보가 들어왔고, 기존 위치와 다를 경우 상태 업데이트
        if loc and loc.get("latitude") is not None:
            new_lat, new_lon = float(loc["latitude"]), float(loc["longitude"])
            if f"{new_lat:.5f}" != f"{st.session_state.user_lat:.5f}" or f"{new_lon:.5f}" != f"{st.session_state.user_lon:.5f}":
                st.session_state.user_lat = new_lat
                st.session_state.user_lon = new_lon
                # 위치가 변경되었으므로 날씨와 음식점 데이터를 다시 불러오도록 None으로 초기화
                st.session_state.weather = None
                st.session_state.all_df = pd.DataFrame()
                st.session_state.selected_category = None
                st.rerun() # 앱을 새로고침하여 변경된 위치로 데이터 다시 로드

        # 현재 날씨 정보 렌더링
        st.markdown(f"""
        <div class="info-box" style="margin-top: 25px;">
            <div class="info-box-header">
                <span>{header_icon}</span><h3>현재 날씨</h3>
            </div>
            <div class="info-box-content">
                <p><b>{weather_group}</b> ({weather_info['description']})<br>기온: {weather_info['temperature']:.1f}°C</p>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 추천 키워드 렌더링
        keywords = WEATHER_KEYWORDS.get(weather_group, WEATHER_KEYWORDS["기타"])
        st.markdown(f"""
        <div class="info-box">
            <div class="info-box-header">
                <span>#</span><h3>추천 핵심 키워드</h3>
            </div>
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
        st.markdown('<div class="main-content">', unsafe_allow_html=True)
        # 'selection' 페이지일 경우
        if st.session_state.page == 'selection':
            st.markdown("<h2>현재 날씨에 추천 드리는 카테고리입니다. 선택해주세요!</h2>", unsafe_allow_html=True)

            # 카테고리 선택 라디오 버튼
            selected = st.radio(
                "음식 카테고리",
                options=categories,
                index=categories.index(st.session_state.selected_category) if st.session_state.selected_category in categories else 0,
                label_visibility="collapsed" # 라벨 숨김
            )
            # 선택이 바뀌면 session_state 업데이트 후 앱 재실행
            if selected != st.session_state.selected_category:
                st.session_state.selected_category = selected
                st.rerun()

            st.markdown(f"<h2 style='margin-top: 2rem;'>해당 카테고리에 해당되는 반경 500M 내 음식점 입니다.</h2>", unsafe_allow_html=True)

            # 선택된 카테고리로 음식점 필터링
            filtered_df = filter_by_category_tf(st.session_state.all_df, selected)
            
            # 필터링된 목록을 UI에 렌더링
            render_restaurant_list(filtered_df)

            # '다음' 버튼 클릭 시 'details' 페이지로 이동
            if st.button("다음 ▶", key="next_button"):
                st.session_state.page = 'details'
                st.rerun()
        
        # 'details' 페이지일 경우 (현재는 비어있는 페이지)
        elif st.session_state.page == 'details':
            st.subheader(f"'{st.session_state.selected_category}' 카테고리 상세 보기")
            st.write("이 곳에 선택한 카테고리의 식당들을 지도와 함께 보여주는 등 상세 페이지를 구현할 수 있습니다.")
            
            # '이전' 버튼 클릭 시 'selection' 페이지로 이동
            if st.button("◀ 이전"):
                st.session_state.page = 'selection'
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------
# 7. 스크립트 실행
# ---------------------------------
# 이 파일이 직접 실행될 때 main() 함수를 호출합니다.
if __name__ == "__main__":
    main()
