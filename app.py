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
import time

# ──────────────────────────────────────────────────────────────────────────────
# 0) 환경 설정
#    - .env에서 키 로드
#    - Streamlit 페이지 기본 옵션
# ──────────────────────────────────────────────────────────────────────────────
load_dotenv()

SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
OPENWEATHER_API_KEY: str = os.getenv("WEATHER_API_KEY")

# Supabase 클라이언트 생성 (실패 시 오류 메시지)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(
    page_title="날씨와 위치 기반 맛집 추천 서비스",
    page_icon="🍜",
    layout="wide"
)

st.title("오늘, 내 주변 날씨에 어울리는 맛집 추천 🌨️")
st.caption("현재 위치와 날씨 데이터를 기반으로 지금 가장 어울리는 음식점을 찾아드릴게요.")

# ──────────────────────────────────────────────────────────────────────────────
# 1) 유틸 / 전처리
#    - 위치 획득
#    - 카테고리 이름 표준화, TF(Boolean) 컬럼 보정
#    - 한글 컬럼 / 거리 포맷팅
# ──────────────────────────────────────────────────────────────────────────────

# 기본 좌표(서울 시청) — 브라우저 권한 거부/실패 시 사용
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location() -> Tuple[float, float]:
    """
    브라우저에서 현재 위치를 가져옵니다.
    - 권한 거부/실패 시: 서울 좌표 반환
    """
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])


# 카테고리 표준화 테이블(오탈자/표기 차이 흡수)
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
}

def norm_cat(name: str) -> str:
    """카테고리 라벨을 표준화"""
    return CATEGORY_ALIAS.get(str(name).strip(), str(name).strip())

def _normalize_label(s: str) -> str:
    """
    비교/매칭용 문자열 정규화:
    - 소문자화
    - 공백/슬래시/밑줄/하이픈/괄호 제거
    """
    if s is None:
        return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\-()]+", "", s)

def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    """
    TRUE/FALSE/1/0/빈값 등 문자열 기반 TF 컬럼을 실제 bool로 변환.
    - 각 컬럼에 대해 값의 80% 이상이 TF 패턴일 때만 변환(오검 방지)
    """
    for col in frame.columns:
        if frame[col].dtype is bool:
            continue
        if frame[col].dtype == object:
            vals = frame[col].astype(str).str.strip().str.upper()
            if vals.isin(["TRUE", "FALSE", "1", "0", "", "NAN"]).mean() > 0.8:
                frame[col] = vals.map({"TRUE": True, "FALSE": False, "1": True, "0": False}).fillna(False)
    return frame

def resolve_tf_column(frame: pd.DataFrame, expected_label: str) -> str | None:
    """
    기대 라벨(카테고리) → 실제 컬럼명 매칭.
    1) 표준화 일치 → 2) 정규화 일치 → 3) 부분 포함
    """
    expected = norm_cat(expected_label)
    if expected in frame.columns:
        return expected
    want = _normalize_label(expected)
    normalized = {str(c): _normalize_label(str(c)) for c in frame.columns}
    # 완전 일치
    for col, key in normalized.items():
        if key == want:
            return col
    # 부분 포함
    for col, key in normalized.items():
        if want in key:
            return col
    return None

def prettify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    표시용 DataFrame 가공:
    - 거리 컬럼을 'm' 또는 'km'로 포맷
    - 이름/업태/주소/별점/리뷰수 한글 컬럼명으로 통일
    """
    if df is None or df.empty:
        return df
    df = df.copy()

    # 거리 포맷
    if "distance_m" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_m"], errors="coerce").apply(
            lambda x: f"{int(x)}m" if pd.notna(x) else ""
        )
    elif "distance_km" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_km"], errors="coerce").apply(
            lambda x: f"{x:.2f}km" if pd.notna(x) else ""
        )

    # 한글 컬럼명 통일
    rename_map = {
        "name_g": "이름",
        "name": "이름",
        "place_name": "이름",
        "store_name": "이름",
        "상호명": "이름",
        "category": "업태",
        "rating": "별점",
        "review_cnt": "리뷰 수",
        "address": "주소",
        "도로명주소": "주소",
        "지번주소": "주소",
    }
    df.rename(columns=rename_map, inplace=True)
    return df

# ──────────────────────────────────────────────────────────────────────────────
# 2) 날씨 그룹 / 추천 카테고리
#    - OpenWeather weather.id → 내부 그룹
#    - 그룹별 mood/추천 카테고리
#    - 날씨 기반 TF 필터
# ──────────────────────────────────────────────────────────────────────────────

# OpenWeather weather.id 그룹핑
WX_GROUPS = {
    "클리어": [800],
    "구름": [801, 802, 803, 804],
    "비": [500, 501, 502, 503, 504, 511, 520, 521, 522, 531],
    "이슬비": [300, 301, 302, 310, 311, 312, 313, 314, 321],
    "뇌우": [200, 201, 202, 210, 211, 212, 221, 230, 231, 232],
    "눈": [600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622],
    "분위기": [701, 711, 721, 731, 741, 751, 761, 762],
}

# 그룹별 추천 카테고리/분위기
WX_RECO = {
    "클리어": {"mood": "야외활동, 기분전환, 걷기 좋은 날",
               "cats": ["이국적인 음식", "디저트/카페", "술 한잔 하기 좋은 날", "가볍게 간단히", "시원한 한끼", "해산물/생선요리"]},
    "구름": {"mood": "실내 중심, 편안함, 든든함 추구",
             "cats": ["든든한 한끼", "뜨끈한 국물", "디저트/카페", "시원한 한끼", "해산물/생선요리"]},
    "비": {"mood": "외출 불편, 따뜻하거나 자극적인 음식",
           "cats": ["뜨끈한 국물", "매콤한 음식", "술 한잔 하기 좋은 날", "패스트푸드/배달", "시원한 한끼"]},
    "이슬비": {"mood": "활동 가능하지만 귀찮음",
              "cats": ["디저트/카페", "가볍게 간단히", "건강/채식/특수식단", "해산물/생선요리"]},
    "뇌우": {"mood": "외출 최소화, 실내 고정",
            "cats": ["육류구이/고기파티", "든든한 한끼", "패스트푸드/배달"]},
    "눈": {"mood": "실내, 감성적, 따뜻함 추구",
           "cats": ["뜨끈한 국물", "육류구이/고기파티", "가족/단체회식", "디저트/카페", "해산물/생선요리"]},
    "분위기": {"mood": "안개/먼지 등 건강 고려",
             "cats": ["건강/채식/특수식단", "뜨끈한 국물", "패스트푸드/배달"]},
}

def weather_group_from_id(weather_id: int) -> str:
    """OpenWeather weather.id를 내부 그룹명으로 변환."""
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"

def recommended_categories_from_group(group_name: str, top_k: int | None = None):
    """그룹별 추천 카테고리/분위기 반환 (top_k 제공 시 상위 K개만)."""
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return (cats, mood) if top_k is None else (cats[:top_k], mood)

def filter_by_weather_via_categories(frame: pd.DataFrame, group_name: str) -> pd.DataFrame:
    """
    날씨 그룹에 맞는 여러 TF 컬럼을 OR 조건으로 묶어 필터링.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]

    # 실제 존재하는 TF 컬럼만 수집
    cols = []
    for c in cats:
        col = resolve_tf_column(frame, c)
        if col:
            cols.append(col)

    # 해당하는 TF 컬럼이 하나도 없으면 원본 그대로 반환
    if not cols:
        return frame

    # 여러 TF 컬럼을 OR 로 결합
    mask = False
    for col_name in cols:
        mask = mask | (frame[col_name] == True)
    return frame[mask].copy()

# ──────────────────────────────────────────────────────────────────────────────
# 3) API
#    - OpenWeather 현재 날씨
#    - Supabase RPC (반경 500m 음식점)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_weather(weather_lat: float, weather_lon: float) -> dict:
    """
    OpenWeather API로 현재 날씨 정보 호출.
    - 네트워크 오류/타임아웃 등 예외 처리 및 사용자 피드백
    - 실패 시 보수적 fallback (흐림/803) 사용
    """
    url = (
        f"https://api.openweathermap.org/data/2.5/weather?"
        f"lat={weather_lat}&lon={weather_lon}"
        f"&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    )
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        return {
            "id": data["weather"][0]["id"],
            "description": data["weather"][0]["description"],
            "temperature": data["main"]["temp"]
        }
    except requests.exceptions.Timeout:
        st.error("⏳ 날씨 API 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요.")
    except requests.exceptions.RequestException as e:
        st.error(f"🌧️ 날씨 정보를 불러오지 못했습니다: {e}")
    except Exception:
        st.error("⚠️ 알 수 없는 오류로 날씨 정보를 불러오지 못했습니다.")
    # 실패 시 기본값(흐림/803)과 온도 미상('?')으로 진행
    return {"id": 803, "description": "흐림", "temperature": "?"}

def get_restaurant_within_500m_from_supabase(lat: float, lon: float) -> pd.DataFrame:
    """
    Supabase RPC(get_restaurant_within_500m) 호출로 반경 500m 음식점 목록을 가져옴.
    - TF/불리언 보정
    - 카테고리 라벨 표준화
    - 거리(distance_m, 미터) 계산
    """
    try:
        response = supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat, "user_lng": lon
        }).execute()

        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(response.data)

        # 전처리: 불리언/라벨 표준화
        df = coerce_tf_bool(df)
        df.columns = [norm_cat(c) for c in df.columns]

        # 거리(m) 계산 (lat/lon 있을 때만)
        if "latitude" in df.columns and "longitude" in df.columns:
            df["distance_m"] = df.apply(
                lambda row: haversine((lat, lon), (row["latitude"], row["longitude"])) * 1000,
                axis=1,
            ).round(0).astype(int)

        return df

    except Exception as e:
        st.error(f"🍽️ 음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

# ──────────────────────────────────────────────────────────────────────────────
# 4) 필터링
#    - 선택 카테고리 TF 필터
#    - 업태(category) 멀티 선택 필터
# ──────────────────────────────────────────────────────────────────────────────

def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    """
    사용자가 고른 단일 카테고리에 해당하는 TF 컬럼이 True인 행만 필터
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    col_name = resolve_tf_column(frame, theme)
    if not col_name:
        return pd.DataFrame()
    out = frame[frame[col_name] is True if frame[col_name].dtype is bool else (frame[col_name] == True)].copy()
    if "distance_m" in out.columns:
        out = out.sort_values("distance_m")
    return out

def select_and_filter_by_business_type(frame: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    업태(category)를 멀티 선택하여 추가 필터링.
    - 'category' 컬럼이 없으면 필터 스킵
    """
    if frame.empty or "category" not in frame.columns:
        return frame, []
    cats_all = (
        frame["category"]
        .dropna().astype(str).str.strip()
        .replace("", pd.NA).dropna().unique().tolist()
    )
    selected = st.multiselect("업태를 선택하세요", options=cats_all, default=[])
    filtered = frame[frame["category"].isin(selected)] if selected else frame
    return filtered, selected

# ──────────────────────────────────────────────────────────────────────────────
# 5) 메인 앱 (3페이지 흐름)
#    - page1: 날씨→추천 카테고리 선택→리스트
#    - page2: 업태 필터 + 거리/별점/리뷰/지도 탭
#    - page3: 최종 선택 카드 + 선택 완료
# ──────────────────────────────────────────────────────────────────────────────

def main():
    # 세션 스테이트 초기화 (페이지 상태)
    if "page" not in st.session_state:
        st.session_state.page = "page1"

    # ① 위치
    user_lat, user_lon = get_user_location()

    # ② 날씨
    w = fetch_weather(user_lat, user_lon)
    group_name = weather_group_from_id(w["id"])
    opts, mood = recommended_categories_from_group(group_name)

    # ── 사이드바: 위치/날씨/키워드 ──
    with st.sidebar:
        st.markdown(
            f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;'>"
            f"<h3>📍 현재 위치</h3><p>위도: {user_lat:.4f}, 경도: {user_lon:.4f}</p></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;'>"
            f"<h3>🌤️ 현재 날씨</h3><p>{w['description']}, {w['temperature']}°C</p></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='background:#fff; border-radius:10px; padding:15px;'>"
            f"<h3>💡 추천 키워드</h3><p>{mood}</p></div>",
            unsafe_allow_html=True,
        )

    # ③ Supabase: 반경 500m 음식점
    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # ──────────────────────────────────────────────────────────────────────
    # Page 1: 날씨 기반 카테고리 → 선택 목록
    # ──────────────────────────────────────────────────────────────────────
    if st.session_state.page == "page1":
        st.header("현재 날씨에 어울리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)

        # 1차: 날씨 기반 TF 필터
        wx_df = filter_by_weather_via_categories(all_df, group_name)
        # 2차: 선택 카테고리 TF 필터
        filtered_df = filter_by_category_tf(wx_df, choice)

        st.subheader(f"‘{choice}’ 카테고리에 해당되는 반경 500M 내 음식점 (거리순)")

        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)[["이름", "거리"]].reset_index(drop=True)
            df.index = df.index + 1
            st.dataframe(df, use_container_width=True, height=500)
        else:
            st.warning("선택하신 조건에 맞는 음식점이 없습니다.")

        # 다음 페이지로 이동
        _, c2 = st.columns([9, 1])
        with c2:
            if st.button("➡ 다음"):
                st.session_state.choice = choice
                st.session_state.page = "page2"
                st.rerun()

    # ──────────────────────────────────────────────────────────────────────
    # Page 2: 업태 선택 + 거리/별점/리뷰/지도 탭
    # ──────────────────────────────────────────────────────────────────────
    elif st.session_state.page == "page2":
        choice = st.session_state.get("choice")
        st.header(f"‘{choice}’ 카테고리 결과")

        filtered_df = filter_by_category_tf(all_df, choice)

        # 업태 멀티 필터
        filtered, selected_types = select_and_filter_by_business_type(filtered_df)
        st.session_state.selected_types = selected_types  # 3페이지 전달

        tabs = st.tabs(["거리순", "별점순", "리뷰순", "지도"])

        # 거리순 탭
        with tabs[0]:
            df = prettify_dataframe(filtered.sort_values("distance_m")).reset_index(drop=True)
            df.index = df.index + 1
            st.dataframe(df[["이름", "거리"]], use_container_width=True, height=420)

        # 별점순 탭
        with tabs[1]:
            if "rating" in filtered.columns:
                df = prettify_dataframe(filtered.sort_values("rating", ascending=False)).reset_index(drop=True)
                df.index = df.index + 1
                st.dataframe(df[["이름", "별점"]], use_container_width=True, height=420)
            else:
                st.info("별점 정보가 없는 데이터입니다.")

        # 리뷰순 탭
        with tabs[2]:
            if "review_cnt" in filtered.columns:
                df = prettify_dataframe(filtered.sort_values("review_cnt", ascending=False)).reset_index(drop=True)
                df.index = df.index + 1
                st.dataframe(df[["이름", "리뷰 수"]], use_container_width=True, height=420)
            else:
                st.info("리뷰 수 정보가 없는 데이터입니다.")

        # 지도 탭
        with tabs[3]:
            if not filtered.empty:
                # 지도에 표시할 이름 컬럼 추출
                name_col = None
                for cand in ["name_g", "name", "place_name", "store_name", "상호명", "이름"]:
                    if cand in filtered.columns:
                        name_col = cand
                        break

                # 지도 레이어 데이터 준비
                df_map = filtered.rename(columns={"latitude": "lat", "longitude": "lon"}).copy()
                if name_col:
                    df_map["표시이름"] = df_map[name_col].astype(str)
                else:
                    df_map["표시이름"] = ""

                # 내 위치 포인트
                me_df = pd.DataFrame([{"lat": user_lat, "lon": user_lon}])

                # 아이콘(식당) + 텍스트(이름) + 내 위치(점)
                icon_url = "https://cdn-icons-png.flaticon.com/512/11448/11448259.png"
                icon_data = {"url": icon_url, "width": 512, "height": 512, "anchorY": 512}
                df_map["icon_data"] = [icon_data] * len(df_map)

                icon_layer = pdk.Layer(
                    "IconLayer",
                    data=df_map,
                    get_icon="icon_data",
                    get_position=["lon", "lat"],
                    get_size=5,
                    size_scale=8,
                    pickable=True,
                )
                name_layer = pdk.Layer(
                    "TextLayer",
                    data=df_map,
                    get_position=["lon", "lat"],
                    get_text="표시이름",
                    get_color=[60, 60, 60, 255],
                    get_size=13,
                    get_alignment_baseline="'top'",
                )
                me_layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=me_df,
                    get_position=["lon", "lat"],
                    get_fill_color=[25, 25, 112, 255],   # 남색
                    get_line_color=[255, 255, 255, 255], # 흰색 테두리
                    get_radius=40,
                    line_width_min_pixels=2,
                    stroked=True,
                )

                st.pydeck_chart(
                    pdk.Deck(
                        map_provider="maplibre",
                        map_style="https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json",
                        initial_view_state=pdk.ViewState(latitude=user_lat, longitude=user_lon, zoom=15.3),
                        layers=[icon_layer, name_layer, me_layer],
                        tooltip={"text": "{표시이름}"},
                    )
                )
            else:
                st.warning("선택하신 조건에 맞는 음식점이 없습니다.")

        # 이전/다음 네비게이션
        c1, c2 = st.columns([9, 1])
        with c1:
            if st.button("⬅ 이전"):
                st.session_state.page = "page1"
                st.rerun()
        with c2:
            if st.button("➡ 다음"):
                st.session_state.page = "page3"
                st.rerun()

    # ──────────────────────────────────────────────────────────────────────
    # Page 3: 최종 선택 카드 + 선택 완료
    # ──────────────────────────────────────────────────────────────────────
    elif st.session_state.page == "page3":
        st.header("최종 선택")

        choice = st.session_state.get("choice")
        filtered_df = filter_by_category_tf(all_df, choice)

        # 2페이지에서 선택된 업태 반영
        selected_types = st.session_state.get("selected_types", [])
        if selected_types:
            filtered_df = filtered_df[filtered_df["category"].isin(selected_types)]

        if filtered_df.empty:
            st.warning("선택 가능한 식당이 없습니다. 2페이지에서 다시 선택해주세요.")
        else:
            df = prettify_dataframe(filtered_df).reset_index(drop=True)

            # 선택 분위기 태그
            st.markdown(
                f"""
                <div style="margin-bottom:8px;">
                  <span style="font-weight:600; font-size:18px;">오늘의 분위기:</span>
                  <span style="background:#ffeaea;color:#d9534f;padding:4px 10px;
                      border-radius:8px;margin-left:8px;font-size:16px;font-weight:600;">
                      {choice}
                  </span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # 선택 업태 태그
            if selected_types:
                st.markdown(
                    "<b>선택한 업태:</b> " + " · ".join(
                        [f"<span style='background:#e8f5ff;padding:3px 8px;border-radius:6px;margin-right:4px;'>{t}</span>"
                         for t in selected_types]
                    ),
                    unsafe_allow_html=True,
                )

            st.markdown("---")
            st.markdown("#### 최종으로 방문할 식당을 선택하세요 👇")

            # 식당 하나 선택
            selected_name = st.selectbox("식당 선택", df["이름"])
            selected_row = df[df["이름"] == selected_name].iloc[0]

            # 카드 내부 라인 헬퍼
            def info_line(icon, label, value):
                if value not in [None, "", "nan", "정보 없음"]:
                    return f"<p style='margin:5px 0;'>{icon} <b>{label}:</b> {value}</p>"
                return ""

            # 카드 내용 구성
            info_html = (
                info_line("📍", "거리", selected_row.get("거리"))
                + info_line("⭐", "별점", selected_row.get("별점"))
                + info_line("💬", "리뷰 수", selected_row.get("리뷰 수"))
                + info_line("🏠", "주소", selected_row.get("주소"))
            )

            st.markdown(
                f"""
                <div style="background-color:#ffffff;border-radius:12px;
                    box-shadow:0 2px 10px rgba(0,0,0,0.1);padding:20px;
                    margin-top:10px;margin-bottom:20px;border:1px solid #e8e8e8;">
                    <h3 style="margin-bottom:5px;">🍴 {selected_row['이름']}</h3>
                    {info_html}
                    <a href="{selected_row.get('map_link', '')}" target="_blank" style="text-decoration:none;">
                      <button style="background-color:#E2EAFC;color:black;border:none;
                        padding:10px 18px;border-radius:8px;cursor:pointer;font-size:16px;
                        font-weight:500;box-shadow:0 2px 4px rgba(0,0,0,0.1);
                        transition:0.2s;margin-top:10px;">지도에서 보기</button>
                    </a>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # ── 버튼/알림 스타일: 한 번만 정의 (중복 제거) ──
        st.markdown("""
            <style>
            div[data-testid="stButton"] button {
                min-width: 120px !important;
                white-space: nowrap !important;
            }
            .custom-success {
                background-color:#e6f4ea;
                color:#1e4620;
                border:1px solid #b6dfb9;
                padding:10px 15px;
                border-radius:6px;
                font-size:16px;
                font-weight:500;
                margin-top:12px;
                white-space: nowrap;
                display:inline-block;
                box-shadow:0 1px 3px rgba(0,0,0,0.08);
            }
            .right-wrapper {
                display:flex;
                justify-content:flex-end;
                width:100%;
            }
            </style>
        """, unsafe_allow_html=True)

        # ── 버튼 영역: 이전 / 선택 완료 ──
        c1, c2 = st.columns([9, 1])

        with c1:
            if st.button("⬅ 이전"):
                st.session_state.page = "page2"
                st.rerun()

        with c2:
            if st.button("✅ 선택 완료"):
                # 오른쪽 정렬 성공 박스
                st.markdown("""
                    <div class="right-wrapper">
                        <div class="custom-success">🎉 ✅ 선택이 완료되었습니다!</div>
                    </div>
                """, unsafe_allow_html=True)
                time.sleep(1)
                st.session_state.page = "page1"
                st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# 엔트리포인트
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
