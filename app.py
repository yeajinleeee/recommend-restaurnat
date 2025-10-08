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
import webbrowser

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


# ───────────────────────────────
# prettify: 컬럼명 + 거리 단위
# ───────────────────────────────
def prettify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()

    # 거리 처리
    if "distance_m" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_m"], errors="coerce").apply(
            lambda x: f"{int(x)}m" if pd.notna(x) else ""
        )
    elif "distance_km" in df.columns:
        df["거리"] = pd.to_numeric(df["distance_km"], errors="coerce").apply(
            lambda x: f"{x:.2f}km" if pd.notna(x) else ""
        )

    # 컬럼명 매핑
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
    "클리어": {
        "mood": "야외활동, 기분전환, 걷기 좋은 날",
        "cats": ["이국적인 음식", "디저트/카페", "술 한잔 하기 좋은 날",
                 "가볍게 간단히", "시원한 한끼", "해산물/생선요리"]
    },
    "구름": {
        "mood": "실내 중심, 편안함, 든든함 추구",
        "cats": ["든든한 한끼", "뜨끈한 국물", "디저트/카페",
                 "시원한 한끼", "해산물/생선요리"]
    },
    "비": {
        "mood": "외출 불편, 따뜻하거나 자극적인 음식",
        "cats": ["뜨끈한 국물", "매콤한 음식", "술 한잔 하기 좋은 날",
                 "패스트푸드/배달", "시원한 한끼"]
    },
    "이슬비": {
        "mood": "활동 가능하지만 귀찮음",
        "cats": ["디저트/카페", "가볍게 간단히",
                 "건강/채식/특수식단", "해산물/생선요리"]
    },
    "뇌우": {
        "mood": "외출 최소화, 실내 고정",
        "cats": ["육류구이/고기파티", "든든한 한끼", "패스트푸드/배달"]
    },
    "눈": {
        "mood": "실내, 감성적, 따뜻함 추구",
        "cats": ["뜨끈한 국물", "육류구이/고기파티",
                 "가족/단체회식", "디저트/카페", "해산물/생선요리"]
    },
    "분위기": {
        "mood": "안개/먼지 등 건강 고려",
        "cats": ["건강/채식/특수식단", "뜨끈한 국물", "패스트푸드/배달"]
    },
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
    try:
        response = supabase.rpc("get_restaurant_within_500m", {
            "user_lat": lat, "user_lng": lon
        }).execute()

        if not response or response.data is None or len(response.data) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(response.data)
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


def select_and_filter_by_business_type(frame: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
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

    # 사이드바 카드
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

    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)

    # Page 1
    if st.session_state.page == "page1":
        st.header("현재 날씨에 추천 드리는 카테고리입니다.")
        choice = st.radio("카테고리를 선택하세요 👇", options=opts)
        filtered_df = filter_by_category_tf(all_df, choice)

        st.subheader(f"‘{choice}’ 카테고리에 해당되는 반경 500M 내 음식점 (거리순)")

        if not filtered_df.empty:
            df = prettify_dataframe(filtered_df)[["이름", "거리"]]
            df = df.reset_index(drop=True)
            df.index = df.index + 1
            st.dataframe(df, use_container_width=True, height=500)
        else:
            st.warning("해당 카테고리 음식점이 없습니다.")

        # 버튼: 오른쪽 정렬
        col1, col2 = st.columns([9, 1])
        with col2:
            if st.button("➡ 다음"):
                st.session_state.choice = choice
                st.session_state.page = "page2"
                st.rerun()

    # ───────────────────────────────
    # Page 2 : 업태 선택 + 탐색 + 링크 보기
    # ───────────────────────────────
    elif st.session_state.page == "page2":
        choice = st.session_state.get("choice")
        st.header(f"‘{choice}’ 카테고리 결과")
    
        # 날씨 카테고리 필터링
        filtered_df = filter_by_category_tf(all_df, choice)
    
        # 업태 선택 UI 및 데이터 필터
        filtered, selected_types = select_and_filter_by_business_type(filtered_df)
        st.session_state.selected_types = selected_types  #  3 페이지 전달용
    
        tabs = st.tabs(["거리순", "별점순", "리뷰순", "지도"])
    
        # ───────────────────────────────
        # 거리순 탭
        # ───────────────────────────────
        with tabs[0]:
            df = prettify_dataframe(filtered.sort_values("distance_m"))
            df = df.reset_index(drop=True)
            df.index = df.index + 1
    
            st.dataframe(df[["이름", "거리"]], use_container_width=True, height=420)
    
        # ───────────────────────────────
        # 별점순 탭
        # ───────────────────────────────
        with tabs[1]:
            if "rating" in filtered.columns:
                df = prettify_dataframe(filtered.sort_values("rating", ascending=False))
                df = df.reset_index(drop=True)
                df.index = df.index + 1
                st.dataframe(df[["이름", "별점"]], use_container_width=True, height=420)
    
        # ───────────────────────────────
        # 리뷰순 탭
        # ───────────────────────────────
        with tabs[2]:
            if "review_cnt" in filtered.columns:
                df = prettify_dataframe(filtered.sort_values("review_cnt", ascending=False))
                df = df.reset_index(drop=True)
                df.index = df.index + 1
                st.dataframe(df[["이름", "리뷰 수"]], use_container_width=True, height=420)
    
        # ───────────────────────────────
        # Page 2 : 지도 탭 (3번째 탭)
        # ───────────────────────────────
        with tabs[3]:
            if not filtered.empty:
                df_map = filtered.rename(columns={"latitude": "lat", "longitude": "lon"}).copy()
                df_map["표시이름"] = df_map["name_g"] if "name_g" in df_map.columns else df_map["이름"]
        
                # 내 위치 데이터프레임
                me_df = pd.DataFrame([{"lat": user_lat, "lon": user_lon}])
        
                # 🍴 음식점 아이콘 (Flaticon PNG 사용)
                icon_url = "https://cdn-icons-png.flaticon.com/512/11448/11448259.png"
                icon_data = {
                    "url": icon_url,
                    "width": 512,
                    "height": 512,
                    "anchorY": 512,  # 아이콘의 아래쪽이 위치를 기준으로 오도록 설정
                }
        
                # 아이콘 데이터 추가
                df_map["icon_data"] = [icon_data] * len(df_map)
        
                # 🍴 음식점 레이어 (아이콘)
                icon_layer = pdk.Layer(
                    "IconLayer",
                    data=df_map,
                    get_icon="icon_data",
                    get_position=["lon", "lat"],
                    get_size=3,  # 아이콘 크기 (조절 가능)
                    size_scale=8,
                    pickable=True,
                )
        
                # 🏷️ 음식점 이름 레이어
                name_layer = pdk.Layer(
                    "TextLayer",
                    data=df_map,
                    get_position=["lon", "lat"],
                    get_text="표시이름",
                    get_color=[60, 60, 60, 255],
                    get_size=40,
                    get_alignment_baseline="'top'",
                )
        
                # 💙 내 위치 (남색 점 + 흰색 테두리)
                me_layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=me_df,
                    get_position=["lon", "lat"],
                    get_fill_color=[25, 25, 112, 255],   # 남색
                    get_line_color=[255, 255, 255, 255], # 흰색 테두리
                    get_radius=30,
                    line_width_min_pixels=2,
                    stroked=True,
                )
        
                # pydeck 지도 렌더링
                st.pydeck_chart(
                    pdk.Deck(
                        map_provider="maplibre",
                        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style-nolabels.json",
                        initial_view_state=pdk.ViewState(
                            latitude=user_lat,
                            longitude=user_lon,
                            zoom=15.3,
                        ),
                        layers=[icon_layer, name_layer, me_layer],
                        tooltip={"text": "{표시이름}"},
                    )
                )
        
            else:
                st.warning("지도에 표시할 음식점이 없습니다.")

        
                # ───────────────────────────────
                # 페이지 이동 버튼
                # ───────────────────────────────
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
    
        # 2페이지에서 선택한 업태 반영
        selected_types = st.session_state.get("selected_types", [])
        if selected_types:
            filtered_df = filtered_df[filtered_df["category"].isin(selected_types)]
    
        if filtered_df.empty:
            st.warning("선택 가능한 식당이 없습니다. 2페이지에서 다시 선택해주세요.")
        else:
            df = prettify_dataframe(filtered_df).copy()
            df = df.reset_index(drop=True)
    
            # 오늘의 분위기: 연한 빨강 배경 태그 스타일
            st.markdown(
                f"""
                <div style="margin-bottom:8px;">
                  <span style="font-weight:600; font-size:18px;">오늘의 분위기:</span>
                  <span style="
                      background:#ffeaea;
                      color:#d9534f;
                      padding:4px 10px;
                      border-radius:8px;
                      margin-left:8px;
                      font-size:16px;
                      font-weight:600;
                      ">
                      {choice}
                  </span>
                </div>
                """,
                unsafe_allow_html=True,
            )
    
            # 선택한 업태: 연한 파랑 배경 태그 스타일
            if selected_types:
                st.markdown(
                    "<b>선택한 업태:</b> " + " · ".join(
                        [
                            f"<span style='background:#e8f5ff; padding:3px 8px; "
                            f"border-radius:6px; margin-right:4px;'>{t}</span>"
                            for t in selected_types
                        ]
                    ),
                    unsafe_allow_html=True,
                )
                
            st.markdown("---")
            st.markdown("#### 최종으로 방문할 식당을 선택하세요 👇")
    
            # 식당 선택
            selected_name = st.selectbox("식당 선택", df["이름"])
            selected_row = df[df["이름"] == selected_name].iloc[0]
    
            # 카드 정보 표시
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
    
            # 카드형 정보 출력
            st.markdown(
                f"""
                <div style="
                    background-color:#ffffff;
                    border-radius:12px;
                    box-shadow:0 2px 10px rgba(0,0,0,0.1);
                    padding:20px;
                    margin-top:10px;
                    margin-bottom:20px;
                    border:1px solid #e8e8e8;">
                    <h3 style="margin-bottom:5px;">🍴 {selected_row['이름']}</h3>
                    {info_html}
                    <a href="{selected_row['map_link']}" target="_blank" style="text-decoration:none;">
                      <button style="
                        background-color:#E2EAFC;
                        color:black;
                        border:none;
                        padding:10px 18px;
                        border-radius:8px;
                        cursor:pointer;
                        font-size:16px;
                        font-weight:500;
                        box-shadow:0 2px 4px rgba(0,0,0,0.1);
                        transition:0.2s;
                        margin-top:10px;"
                        onmouseover="this.style.backgroundColor='#5ec2e0'"
                        onmouseout="this.style.backgroundColor='#87CEEB'">
                        지도에서 보기
                      </button>
                    </a>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # ───────────────────────────────
        # 페이지 이동 버튼 (이전 / 선택 완료)
        # ───────────────────────────────
        # CSS: 버튼 고정 + 오른쪽 정렬된 success 스타일
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
                display: inline-block;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            }
        
            /* 오른쪽 정렬용 wrapper */
            .right-wrapper {
                display: flex;
                justify-content: flex-end;
                width: 100%;
            }
            </style>
        """, unsafe_allow_html=True)
        
        # ──────────────── 버튼 영역 ────────────────
        col1, col2 = st.columns([9, 1])
        
        with col1:
            if st.button("⬅ 이전"):
                st.session_state.page = "page2"
                st.rerun()
        
        with col2:
            if st.button("✅ 선택 완료"):
                # 🎉 오른쪽 정렬된 success 박스
                st.markdown("""
                    <div class="right-wrapper">
                        <div class="custom-success">🎉 ✅ 선택이 완료되었습니다!</div>
                    </div>
                """, unsafe_allow_html=True)
                time.sleep(1)
                st.session_state.page = "page1"
                st.rerun()
                
                
if __name__ == "__main__":
    main()
