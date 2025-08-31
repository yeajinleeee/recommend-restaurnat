import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import pandas as pd
from haversine import haversine
import requests
import concurrent.futures
import psycopg2
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import re

load_dotenv()
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(url, key)

st.title("날씨 + 위치 기반 음식점 추천🌨️")

#임의 위치 설정
#seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()  # 버튼/권한 요청
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return None, None
    return float(loc["latitude"]), float(loc["longitude"])

#날씨
# ── 카테고리 이름 표준화 (원본 category_map 키에 맞춤)
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",  # 오타 보정
}

def norm_cat(name: str) -> str:
    return CATEGORY_ALIAS.get(name, name)


def _normalize_label(s: str) -> str:
    """비교용 정규화: 공백/슬래시/밑줄/하이픈/괄호 제거 + 소문자"""
    if s is None: return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\-()]+", "", s)

def coerce_tf_bool(df: pd.DataFrame) -> pd.DataFrame:
    """'TRUE'/'FALSE'/1/0/NaN 등도 True/False로 강제"""
    for col in df.columns:
        if df[col].dtype is bool:
            continue
        # 값이 True/False 표처럼 보이는 경우만 변환 시도
        if df[col].dtype == object:
            vals = df[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE","FALSE","1","0","", "NAN"]).mean() > 0.8:
                df[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
    return df


def resolve_tf_column(df: pd.DataFrame, expected_label: str) -> str | None:
    """기대한 라벨을 DF 실제 컬럼으로 해석(완전일치→정규화일치→부분일치)"""
    expected = norm_cat(expected_label)
    if expected in df.columns:
        return expected

    want = _normalize_label(expected)

    #문자열로 강제
    norm_map = {str(c): _normalize_label(str(c)) for c in df.columns}

    # 정규화 일치
    for col, key in norm_map.items():
        if key == want:
            return col
    # 부분 포함
    for col, key in norm_map.items():
        if want in key:
            return col
    return None


# ── 날씨 코드 그룹 정의 (OpenWeather weather.id)
WX_GROUPS = {
    "클리어": [800],
    "구름":   [801, 802, 803, 804],
    "비":     [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우":   [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈":     [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "분위기": [701, 711, 721, 731, 741, 751, 761, 762],
}

# ── 그룹별 추천 카테고리 & 분위기 설명
WX_RECO = {
    "클리어": {
        "mood": "야외활동, 기분전환, 걷기 좋은 날",
        "cats": ["이국적인 음식","디저트/카페","술 한잔 하기 좋은 날","가볍게 간단히","시원한 음식","해산물/생선요리"],
    },
    "구름": {
        "mood": "실내 중심, 무거운 분위기로 인한 편안함, 든든함 추구",
        "cats": ["든든한 한끼","뜨끈한 국물","디저트/카페","시원한 한끼","해산물/생선요리"],
    },
    "비": {
        "mood": "외출 불편, 따뜻하거나 자극적인 음식",
        "cats": ["뜨끈한 국물","매콤한 음식","술 한잔 하기 좋은 날","패스트푸드/배달","시원한 한끼"],
    },
    "이슬비": {
        "mood": "활동 가능하지만 귀찮음, 정적이거나 가벼운 공간",
        "cats": ["디저트/카페","가볍게 간단히","건강/채식/특수식단","해산물/생선요리"],
    },
    "뇌우": {
        "mood": "외출 최소화, 실내고정",
        "cats": ["육류구이/고기파티","든든한 한끼","패스트푸드/배달"],
    },
    "눈": {
        "mood": "실내, 정적인 장소, 감성적, 따뜻함 추구",
        "cats": ["뜨끈한 국물","육류구이/고기파티","가족/단체회식","디저트/카페","해산물/생선요리"],
    },
    "분위기": {
        "mood": "안개/먼지 등: 건강 고려, 따뜻한 국물, 배달 선호",
        "cats": ["건강/채식/특수식단","뜨끈한 국물","패스트푸드/배달"],
    },
}

def weather_group_from_id(weather_id: int) -> str:
    for g, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return g
    return "구름"  #못 찾으면 기본값 구름


def recommended_categories_from_group(group: str, top_k: int | None = None):
    cats = [norm_cat(c) for c in WX_RECO[group]["cats"]]
    mood = WX_RECO[group]["mood"]
    if top_k is None:
        return cats, mood   # ✅ 전체 반환
    return cats[:top_k], mood

def weather_tf_column(group: str) -> str:
    # DF에 있는 날씨 TF 컬럼명 규칙: W_클리어, W_구름, ...
    return f"W_{group}"


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



def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    try:
        response = (
            supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat,
            "user_lng": lon
            }).execute()
            )
        #response에 데이터 들어가있음

        st.subheader("반경 500m 이내 음식점 목록")
        if not response or response.data is None or len(response.data) == 0:
            st.info("주변에 조회 결과가 없습니다.")
            return pd.DataFrame()  # 빈 DF 반환

        df = pd.DataFrame(response.data)

         # (옵션) 표 보여주기
        st.write(f"전체 행 개수: {len(df)}")
        st.dataframe(df)

        return df  #반드시 반환!

    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다. ({e})")
        return pd.DataFrame()  #실패해도 빈 DF 반환


def filter_by_weather_via_categories(df: pd.DataFrame, group: str, use_all_cats: bool = True, top_k: int = 3) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    # 그룹에 대응하는 카테고리들
    df = coerce_tf_bool(df) #bool 강제
    cats_all = [norm_cat(c) for c in WX_RECO[group]["cats"]]
    cats = cats_all if use_all_cats else cats_all[:top_k]

    # 실제 존재하는 TF 컬럼만 사용
    cols = []
    for c in cats:
        col = resolve_tf_column(df, c)
        if col: cols.append(col)

    if not cols:
        st.warning(f"해당 날씨 그룹('{group}')에 매칭되는 TF 컬럼을 못 찾았어요.\n요청: {cats}\n보유: {list(df.columns)}")
        return pd.DataFrame()

    mask = False
    for col in cols:
        mask = mask | (df[col] == True)

    return df[mask].copy()

def filter_by_category_tf(df: pd.DataFrame, theme: str) -> pd.DataFrame:
    """선택 카테고리명 그대로의 TF 컬럼(True)으로 2차 필터, 가까운 순 정렬"""
    if df is None or df.empty:
        return pd.DataFrame()
    df = coerce_tf_bool(df)

    col = resolve_tf_column(df, theme)
    if not col:
        st.warning(f"DataFrame에 '{theme}' 컬럼을 찾지 못했습니다.")
        return pd.DataFrame()

    out = df[df[col] == True].copy()

    # Supabase가 거리까지 계산해줬다면 그 컬럼으로 정렬
    for c in ["distance_m", "distance", "dist_m", "distance_km"]:
        if c in out.columns:
            if c == "distance_km":
                out = out.sort_values(c)
                out["distance_m"] = (out[c] * 1000).round(0).astype(int)
            else:
                out = out.sort_values(c)
            break

    return out

# (선택) 지도 표시
def display_map(lat: float, lon: float):
    try:
        # 사용자 현재 위치
        st.map(pd.DataFrame([{"lat": lat, "lon":lon}]))
    except Exception:
        pass


def main():
    # 위치 가져오기
    user_lat, user_lon = get_user_location()
    if user_lat is None or user_lon is None:
        st.info("이 앱은 위치 권한이 필요합니다.브라우저 팝업에서 위치 공유를 허용해주세요.")
        st.stop() #이후 섹션실행 안함
    #위치 허용됨 -> 계속 진행
    st.success(f"현재 위치(브라우저): 위도 {user_lat:.5f}, 경도 {user_lon:.5f}")

    # 날씨
    try:
        w = fetch_weather(user_lat, user_lon)
        group = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group)

        st.markdown(f"### 오늘의 날씨는 **{w['description']}**, 기온은 **{w['temperature']}°C** 입니다.")
        st.caption(f"({group} / {mood})")
    except Exception as e:
        st.error(f"날씨 정보를 불러오는 데 실패했습니다. ({e})")
        group, opts = "구름", ["가볍게 간단히", "든든한 한끼", "디저트/카페"]

    st.write("지금 날씨에 추천 드리는 카테고리입니다.")
    choice = st.radio("선택해 주세요 👇", options=opts, horizontal=False)

    st.divider()

    # 음식점
    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # 1차: 날씨 TF(W_그룹) 필터
    wx_df = filter_by_weather_via_categories(all_df, group, use_all_cats=True)  # ✅ 교체

    # 2차: 선택 카테고리 TF 필터
    filtered_df = filter_by_category_tf(wx_df, choice)

    st.subheader(f"‘{group}’ 날씨 + ‘{choice}’ 카테고리 결과")
    if filtered_df.empty:
        st.info("조건에 맞는 주변 업소가 없습니다. 다른 카테고리를 선택해 보세요.")
    else:
        st.write(f"총 {len(filtered_df)}개")
        st.dataframe(filtered_df)

    # 지도 (선택)
    display_map(user_lat, user_lon)

if __name__ == "__main__":
    main()