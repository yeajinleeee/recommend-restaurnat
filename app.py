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

# 카테고리 이름 표준화 맵을 한 번만 정의
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",  # 오타 보정
}

#위에 이름 바꾼걸 토대로 카테고리 명 통일
def norm_cat(name: str) -> str:
    return CATEGORY_ALIAS.get(name, name)

#비교용으로 문자열을 소문자 + 특수문자 제거 -> 정규화
#칼럼명 대조 시 오탈자/공백/하이픈 차이 없앰
def _normalize_label(s: str) -> str:
    if s is None: return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\-()]+", "", s)

#"TRUE","FALSE","1","0","", "NAN" 같은 값들을 진짜 bool로 변환
def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    for col in frame.columns:
        if frame[col].dtype is bool:
            continue
        # 값이 True/False 표처럼 보이는 경우만 변환 시도
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE","FALSE","1","0","", "NAN"]).mean() > 0.8:
                frame[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
    return frame

#원하는 라벨을 실제 칼럼명으로 매핑 완전일치→정규화일치→부분일치
def resolve_tf_column(frame: pd.DataFrame, expected_label: str) -> str | None:
    expected = norm_cat(expected_label)
    if expected in frame.columns:
        return expected
    want = _normalize_label(expected)
    #문자열로 강제
    normalized = {str(c): _normalize_label(str(c)) for c in frame.columns}
    # 정규화 일치
    for col, key in normalized.items():
        if key == want:
            return col
    # 부분 포함
    for col, key in normalized.items():
        if want in key:
            return col
    return None
    
# ── 날씨 코드 그룹 정의 (OpenWeather weather.id)
WX_GROUPS = {
    "클리어": [800],
    "구름": [801, 802, 803, 804],
    "비": [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우": [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈": [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "분위기": [701, 711, 721, 731, 741, 751, 761, 762],
}

# ── 그룹별 추천 카테고리 & 분위기 설명
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

#openweather의 숫자id를 우리 내부 그룹명으로 변환
def weather_group_from_id(weather_id):
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름" #못 찾으면 기본값

#날씨 그룹에 맞는 추천 카테고리들과 분위기 설명을 반환(top_k 주면 일부만, 없으면 전체)
def recommended_categories_from_group(group_name: str, top_k: int | None = None):
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return (cats, mood) if top_k is None else (cats[:top_k], mood)


#OpenWeather API 호출
#현재 날씨 ID/설명/기온을 가져와 추천 로직에 사용
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
def fetch_weather(weather_lat: float, weather_lon: float) -> dict:
    if not OPENWEATHER_API_KEY:
        raise RuntimeError("OpenWeather API 키가 설정되지 않았습니다. st.secrets 또는 ENV에 넣어주세요.")
    weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={weather_lat}&lon={weather_lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    res = requests.get(weather_url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {
        "id": data["weather"][0]["id"],
        "main": data["weather"][0]["main"],
        "description": data["weather"][0]["description"],
        "temperature": data["main"]["temp"]
    }


#Supabase의 RPC(get_restaurant_within_500m)를 호출해 반경 500m 내 식당들을 가져와 DF로 반환
def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = (
            supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat,
            "user_lng": lon
            }).execute()
            )
        #response에 데이터 들어가있음

        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()
        return pd.DataFrame(response.data)

    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다. ({e})")
        return pd.DataFrame()  #실패해도 빈 DF 반환

#날씨 그룹에 대응하는 여러 카테고리의 TF 컬럼들을 찾아 OR 조건으로 1차 필터.
#coerce_tf_bool + resolve_tf_column 사용 -> 칼럼명 불일치/문자형 bool도 안전처리
def filter_by_weather_via_categories(frame: pd.DataFrame, group_name: str, use_all_cats: bool = True, top_k: int = 3) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()

    # 그룹에 대응하는 카테고리들
    frame = coerce_tf_bool(frame) #bool 강제
    cats_all = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    cats = cats_all if use_all_cats else cats_all[:top_k]

    # 실제 존재하는 TF 컬럼만 사용
    cols = [resolve_tf_column(frame, c) for c in cats]
    cols = [c for c in cols if c]
    if not cols:
        st.warning(f"해당 날씨 그룹('{group_name}')에 매칭되는 TF 컬럼을 찾지 못했습니다.")
        return pd.DataFrame()
    mask = False
    for col_name in cols:
        mask = mask | (frame[col_name] == True)
    return frame[mask].copy()


#사용자가 고른 단일 카테고리의 t만 남기는 2차 필터
#정렬 distance_m/distance/dist_m/distance_km 중 존재하는 컬럼 기준으로 가까운 순
#distance_km만 있을 땐 distance_m를 새로 계산해 붙임
def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col_name = resolve_tf_column(frame, theme)
    if not col_name:
        st.warning(f"DataFrame에 '{theme}' 컬럼을 찾지 못했습니다.")
        return pd.DataFrame()

    out = frame[frame[col_name] == True].copy()

    # Supabase가 거리까지 계산해줬다면 그 컬럼으로 정렬
    for order_col in ["distance_m", "distance", "dist_m", "distance_km"]:
        if order_col in out.columns:
            if order_col == "distance_km":
                out = out.sort_values(order_col)
                out["distance_m"] = (pd.to_numeric(out[order_col], errors="coerce") * 1000).round(0).astype("Int64")
            else:
                out[order_col] = pd.to_numeric(out[order_col], errors="coerce")
                out = out.sort_values(order_col)
            break
    return out

#2차 필터 결과에서 업태(category) 멀티셀렉트를 렌더링하고, 선택된 업태만 남기는 2.5차 필터
def select_and_filter_by_business_type(
    frame: pd.DataFrame,
    group_name: str,
    choice: str,
    multiselect_label: str = "업태를 선택하세요 (복수 선택 가능)",
) -> Tuple[pd.DataFrame, List[str]]:

    # 빈 DF 처리
    if frame is None or frame.empty:
        st.info("조건에 맞는 주변 업소가  없습니다. 다른 카테고리를 선택해 보세요.")
        return pd.DataFrame(), []

    # 기본 선택 리스트 (모든 분기에서 변수가 존재하도록)
    selected_categories: List[str] = []

    if "category" not in frame.columns:
        st.warning("데이터에 'category' 컬럼이 없어 업태별 선택을 제공할 수 없습니다.")
        return frame, selected_categories

    # 고유 업태 목록 정제
    cats_all = (
        frame["category"]
        .dropna()
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .unique()
        .tolist()
    )

    if len(cats_all) == 0:
        st.info("업태(category) 값이 비어 있어 전체 목록을 표시합니다.")
        return frame, selected_categories

    # 멀티셀렉트 (전체 업태 한 번에 표시)
    selected_categories = st.multiselect(
        multiselect_label,
        options=cats_all,
        default=[],
        key=f"cat_multiselect_{group_name}|{choice}",
    )

    # 선택된 업태가 있으면 필터링
    filtered = (
        frame[frame["category"].isin(selected_categories)].copy()
        if selected_categories else frame
    )

    # 선택 결과 캡션 표기
    if selected_categories:
        st.caption(f"선택된 업태: {', '.join(selected_categories)} (총 {len(filtered)}곳)")
    else:
        st.caption("업태를 선택하지 않으면 현재 카테고리의 모든 업소가 표시됩니다.")

    return filtered, selected_categories


#이름을 map_link로 하이퍼링크해 표로 그리기 (마크다운 테이블)
def detect_df_col(frame: pd.DataFrame, candidates, fuzzy=()):
    """df에서 후보 컬럼명(정확/부분) 중 첫 매칭을 반환."""
    for cand in candidates:
        if cand in frame.columns:
            return cand
    low_cols = {col.lower(): col for col in frame.columns}
    for substr in fuzzy:
        s = str(substr).lower()
        for low_name, original in low_cols.items():
            if s in low_name:
                return original
    return None

def format_distance(value, colname: str | None) -> str:
    """거리 값을 '123m' 또는 '0.12km' 문자열로 포맷."""
    if value is None or (hasattr(pd, "isna") and pd.isna(value)):
        return ""
    try:
        d = float(value)
    except Exception:
        return str(value)
    if colname and "km" in str(colname).lower():
        return f"{d:.2f}km"
    return f"{int(round(d))}m"


def _to_markdown_table(frame: pd.DataFrame) -> str:
    # 판다스 tabulate 미의존 수동 렌더
    frame2 = frame.fillna("").astype(str)
    headers = [h.replace("|", "\|") for h in frame2.columns.astype(str).tolist()]
    rows = frame2.values.tolist()

    lines = []
    lines.append("| " + " | ".join(esc(h) for h in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        cells = [str(c).replace("|", "\|") for c in row]
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(out)

def render_paginated_clickable_name_table(
    frame: pd.DataFrame,
    *,
    table_key: str,                 # 세션 키(서로 다른 표는 서로 다른 key 사용)
    page_size: int = 10,
    add_optional_cols: bool = False,   # True면 업태/주소도 보여줌
) -> pd.DataFrame:
    if frame is None or frame.empty:
        st.info("표시할 식당이 없습니다.")
        return pd.DataFrame()

    # 정렬: 거리 컬럼이 있으면 가까운 순
    view_df = frame.copy()
    for order_col in ("distance_m", "distance", "dist_m", "distance_km"):
        if order_col in view_df.columns:
            if order_col == "distance_km":
                view_df["distance_m"] = (
                    pd.to_numeric(view_df["distance_km"], errors="coerce") * 1000
                ).round(0).astype("Int64")
                order_col = "distance_m"
            else:
                view_df[order_col] = pd.to_numeric(view_df[order_col], errors="coerce")
            view_df = view_df.sort_values(order_col)
            break
    
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

