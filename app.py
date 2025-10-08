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

# ───────────────────────────────
# 0. 환경 설정
# ───────────────────────────────
load_dotenv()

SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
OPENWEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

st.set_page_config(
    page_title="날씨와 위치 기반 맛집 추천 서비스",
    page_icon="🍜",
    layout="wide"
)

st.title("오늘, 내 주변 날씨에 어울리는 맛집 추천 🌨️")
st.caption("현재 위치와 날씨 데이터를 기반으로 지금 가장 어울리는 음식점을 찾아드릴게요.")

# ───────────────────────────────
# 1. 유틸 / 전처리 함수
# ───────────────────────────────
seoul_lat, seoul_lon = 37.5665, 126.9780


def get_user_location():
    """사용자의 현재 위치(또는 기본 서울 좌표)를 반환"""
    loc = streamlit_geolocation()
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])


# ✅ 카테고리 표준화 매핑
CATEGORY_ALIAS = {
    "시원한 한끼": "시원한 음식",
    "술 한잔 하기 좋은 날": "술 한잔 하기 좋은날",
    "가족/단체회식": "가족/단체 외식",
    "패스트푸드/배달": "패스트푸드",
    "헤산물/생선요리": "해산물/생선요리",
}


def norm_cat(name: str) -> str:
    """카테고리 이름을 표준화 (오탈자, 띄어쓰기 보정)"""
    return CATEGORY_ALIAS.get(str(name).strip(), str(name).strip())


def _normalize_label(s: str) -> str:
    """비교용 문자열 정규화 (소문자, 특수문자 제거)"""
    if s is None:
        return ""
    s = str(s).lower()
    return re.sub(r"[\s/_\-()]+", "", s)


def coerce_tf_bool(frame: pd.DataFrame) -> pd.DataFrame:
    """TRUE/FALSE/1/0 문자열을 실제 bool로 변환"""
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
    """문자열 정규화를 통해 TF 컬럼 이름 매칭"""
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
    """한글 컬럼명 변환 및 거리 단위 표시"""
    if df is None or df.empty:
        return df
    df = df.copy()
    if "distance_m" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_m"], errors="coerce").apply(
            lambda x: f"{int(x)}m" if pd.notna(x) else ""
        )
    elif "distance_km" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_km"], errors="coerce").apply(
            lambda x: f"{x:.2f}km" if pd.notna(x) else ""
        )
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
    """OpenWeather의 weather.id를 내부 그룹명으로 변환"""
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"

def recommended_categories_from_group(group_name: str, top_k: int | None = None):
    """그룹에 따른 추천 카테고리와 분위기 설명 반환"""
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    mood = WX_RECO[group_name]["mood"]
    return (cats, mood) if top_k is None else (cats[:top_k], mood)

# 날씨 그룹 기반 TF 필터
def filter_by_weather_via_categories(frame: pd.DataFrame, group_name: str) -> pd.DataFrame:
    """날씨 그룹에 따라 어울리는 음식점 TF 컬럼을 OR 조건으로 필터링"""
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = coerce_tf_bool(frame)
    cats = [norm_cat(c) for c in WX_RECO[group_name]["cats"]]
    cols = [resolve_tf_column(frame, c) for c in cats if resolve_tf_column(frame, c)]
    if not cols:
        return frame
    mask = False
    for col_name in cols:
        mask = mask | (frame[col_name] == True)
    return frame[mask].copy()

# ───────────────────────────────
# 3. API (날씨 + Supabase)
# ───────────────────────────────
def fetch_weather(weather_lat: float, weather_lon: float) -> dict:
    """OpenWeather API로 현재 날씨 정보 호출"""
    url = (
        f"https://api.openweathermap.org/data/2.5/weather?"
        f"lat={weather_lat}&lon={weather_lon}"
        f"&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    )
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    return {
        "id": data["weather"][0]["id"],
        "description": data["weather"][0]["description"],
        "temperature": data["main"]["temp"]
    }


def get_restaurant_within_500m_from_supabase(lat: float, lon: float):
    """Supabase RPC를 통해 반경 500m 내 음식점 정보 호출출"""
    try:
        response = supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat, "user_lng": lon
        }).execute()

        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(response.data)

        # 불리언 컬럼 정리 및 카테고리 이름 표준화
        df = coerce_tf_bool(df)
        df.columns = [norm_cat(c) for c in df.columns]

        # 거리 계산 (미터 단위)
        if "latitude" in df.columns and "longitude" in df.columns:
            df["distance_m"] = df.apply(
                lambda row: haversine(
                    (lat, lon), (row["latitude"], row["longitude"])
                ) * 1000, axis=1
            ).round(0).astype(int)
        return df

    except Exception as e:
        st.error(f"음식점 데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()

# ───────────────────────────────
# 4. 필터링 함수
# ───────────────────────────────
def filter_by_category_tf(frame: pd.DataFrame, theme: str) -> pd.DataFrame:
    """사용자가 선택한 카테고리에 해당하는 음식점만 필터링"""
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


def select_and_filter_by_business_type(frame: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """멀티셀렉트를 통해 업태(category)별로 추가 필터링"""
    if frame.empty or "category" not in frame.columns:
        return frame, []
    cats_all = (
        frame["category"].dropna().astype(str).str.strip()
        .replace("", pd.NA).dropna().unique().tolist()
    )
    selected = st.multiselect("업태를 선택하세요", options=cats_all, default=[])
    filtered = frame[frame["category"].isin(selected)] if selected else frame
    return filtered, selected

# ───────────────────────────────
# 5. Main
# ───────────────────────────────
def main():
    """Streamlit 메인 페이지 (3단계 구조)"""
    if "page" not in st.session_state:
        st.session_state.page = "page1"

    # ① 사용자 위치 감지
    user_lat, user_lon = get_user_location()

    # ② 날씨 API 호출
    try:
        w = fetch_weather(user_lat, user_lon)
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
    except:
        w = {"description": "알수없음", "temperature": "?"}
        group_name, opts, mood = "구름", ["가볍게 간단히", "든든한 한끼", "디저트/카페"], "실내 중심"

    # ───── 사이드바 정보 카드 ─────
    with st.sidebar:
        st.markdown(
            f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;'>"
            f"<h3>📍 현재 위치</h3><p>위도: {user_lat:.4f}, 경도: {user_lon:.4f}</p></div>",
            unsafe_allow_html=True
        )
        st.markdown(
            f"<div style='background:#fff; border-radius:10px; padding:15px; margin-bottom:15px;'>"
            f"<h3>🌤️ 현재 날씨</h3><p>{w['description']}, {w['temperature']}°C</p></div>",
            unsafe_allow_html=True
        )
        st.markdown(
            f"<div style='background:#fff; border-radius:10px; padding:15px;'>"
            f"<h3>💡 추천 키워드</h3><p>{mood}</p></div>",
            unsafe_allow_html=True
        )

    # ───── Supabase에서 반경 500m 데이터 호출 ─────
    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # ───────────────────────────────
    # Page 1 : 날씨 + 카테고리 선택
    # ───────────────────────────────
    if st.session_state.page == "page1":
        st.header("현재 날씨에 어울리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)

        #  날씨 기반 1차 필터
        wx_df = filter_by_weather_via_categories(all_df, group_name)

        # 선택 카테고리 2차 필터
        filtered_df = filter_by_category_tf(wx_df, choice)

        st.subheader(f"‘{choice}’ 카테고리에 해당되는 반경 500M 내 음식점 (거리순)")

        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)[["이름", "거리"]]
            df = df.reset_index(drop=True)
            df.index = df.index + 1
            st.dataframe(df, use_container_width=True, height=500)
        else:
            st.warning("해당 카테고리 음식점이 없습니다.")

        # 페이지 이동 버튼 (오른쪽 정렬)
        col1, col2 = st.columns([9, 1])
        with col2:
            if st.button("➡ 다음"):
                st.session_state.choice = choice
                st.session_state.page = "page2"
                st.rerun()

    # ───────────────────────────────
    # Page 2 : 업태 선택 + 탐색 + 지도 보기
    # ───────────────────────────────
    elif st.session_state.page == "page2":
        choice = st.session_state.get("choice")
        st.header(f"‘{choice}’ 카테고리 결과")

        filtered_df = filter_by_category_tf(all_df, choice)

        # 업태 선택 필터링
        filtered, selected_types = select_and_filter_by_business_type(filtered_df)
        st.session_state.selected_types = selected_types  # 3페이지 전달용

        tabs = st.tabs(["거리순", "별점순", "리뷰순", "지도"])

        # 거리순
        with tabs[0]:
            df = prettify_dataframe(filtered.sort_values("distance_m"))
            df = df.reset_index(drop=True)
            df.index = df.index + 1
            st.dataframe(df[["이름", "거리"]], use_container_width=True, height=420)

        # 별점순
        with tabs[1]:
            if "rating" in filtered.columns:
                df = prettify_dataframe(filtered.sort_values("rating", ascending=False))
                df = df.reset_index(drop=True)
                df.index = df.index + 1
                st.dataframe(df[["이름", "별점"]], use_container_width=True, height=420)

        # 리뷰순
        with tabs[2]:
            if "review_cnt" in filtered.columns:
                df = prettify_dataframe(filtered.sort_values("review_cnt", ascending=False))
                df = df.reset_index(drop=True)
                df.index = df.index + 1
                st.dataframe(df[["이름", "리뷰 수"]], use_container_width=True, height=420)

        # 지도 탭
        with tabs[3]:
            if not filtered.empty:
                df_map = filtered.rename(columns={"latitude": "lat", "longitude": "lon"}).copy()
                df_map["표시이름"] = df_map["name_g"] if "name_g" in df_map.columns else df_map["이름"]

                me_df = pd.DataFrame([{"lat": user_lat, "lon": user_lon}])

                icon_url = "https://cdn-icons-png.flaticon.com/512/11448/11448259.png"
                icon_data = {"url": icon_url, "width": 512, "height": 512, "anchorY": 512}
                df_map["icon_data"] = [icon_data] * len(df_map)

                icon_layer = pdk.Layer(
                    "IconLayer", data=df_map, get_icon="icon_data",
                    get_position=["lon", "lat"], get_size=5, size_scale=8, pickable=True,
                )

                name_layer = pdk.Layer(
                    "TextLayer", data=df_map, get_position=["lon", "lat"],
                    get_text="표시이름", get_color=[60, 60, 60, 255],
                    get_size=13, get_alignment_baseline="'top'",
                )

                me_layer = pdk.Layer(
                    "ScatterplotLayer", data=me_df,
                    get_position=["lon", "lat"],
                    get_fill_color=[25, 25, 112, 255],
                    get_line_color=[255, 255, 255, 255],
                    get_radius=40, line_width_min_pixels=2, stroked=True,
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
                st.warning("지도에 표시할 음식점이 없습니다.")

        # 페이지 이동 버튼
        col1, col2 = st.columns([9, 1])
        with col1:
            if st.button("⬅ 이전"):
                st.session_state.page = "page1"
                st.rerun()
        with col2:
            if st.button("➡ 다음"):
                st.session_state.page = "page3"
                st.rerun()

    # ───────────────────────────────
    # Page 3 : 최종 선택 + 상세 카드
    # ───────────────────────────────
    elif st.session_state.page == "page3":
        st.header("최종 선택")

        choice = st.session_state.get("choice")
        filtered_df = filter_by_category_tf(all_df, choice)

        selected_types = st.session_state.get("selected_types", [])
        if selected_types:
            filtered_df = filtered_df[filtered_df["category"].isin(selected_types)]

        if filtered_df.empty:
            st.warning("선택 가능한 식당이 없습니다. 2페이지에서 다시 선택해주세요.")
        else:
            df = prettify_dataframe(filtered_df).copy()
            df = df.reset_index(drop=True)

            # 오늘의 분위기
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

            # 업태 태그
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

            selected_name = st.selectbox("식당 선택", df["이름"])
            selected_row = df[df["이름"] == selected_name].iloc[0]

            def info_line(icon, label, value):
                if value not in [None, "", "nan", "정보 없음"]:
                    return f"<p style='margin:5px 0;'>{icon} <b>{label}:</b> {value}</p>"
                return ""

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
                    <a href="{selected_row['map_link']}" target="_blank" style="text-decoration:none;">
                      <button style="background-color:#E2EAFC;color:black;border:none;
                        padding:10px 18px;border-radius:8px;cursor:pointer;font-size:16px;
                        font-weight:500;box-shadow:0 2px 4px rgba(0,0,0,0.1);
                        transition:0.2s;margin-top:10px;">지도에서 보기</button>
                    </a>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # 선택 완료 메시지
        st.markdown("""
            <style>
            .custom-success {
                background-color:#e6f4ea;
                color:#1e4620;
                border:1px solid #b6dfb9;
                padding:10px 15px;
                border-radius:6px;
                font-size:16px;
                font-weight:500;
                margin-top:12px;
                display:flex;
                justify-content:flex-end;
                box-shadow:0 1px 3px rgba(0,0,0,0.08);
            }
            </style>
        """, unsafe_allow_html=True)

        col1, col2 = st.columns([9, 1])
        with col1:
            if st.button("⬅ 이전"):
                st.session_state.page = "page2"
                st.rerun()
        with col2:
            if st.button("✅ 선택 완료"):
                st.markdown("<div class='custom-success'>🎉 ✅ 선택이 완료되었습니다!</div>", unsafe_allow_html=True)
                time.sleep(1)
                st.session_state.page = "page1"
                st.rerun()


if __name__ == "__main__":
    main()
