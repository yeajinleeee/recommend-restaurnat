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
import random
import pydeck as pdk
from typing import Tuple, List
import math


load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(
    page_title="날씨 + 위치 기반 음식점 추천",
    page_icon="🍜",
    layout="wide",
)

# ✨ 라이트/다크 공통 미니 테마 (파스텔 톤 + 카드/필 UI)
THEME_CSS = """
<style>
:root {
  --pri:#3b82f6; --pri-weak:#e8f0fe;
  --ok:#10b981; --warn:#f59e0b; --bg:#ffffff; --muted:#6b7280;
  --card:#ffffff; --card-bd:#e5e7eb; --chip:#f3f4f6;
}
[data-theme="dark"] :root {
  --pri:#60a5fa; --pri-weak:#1e293b;
  --ok:#34d399; --warn:#fbbf24; --bg:#0b0f19; --muted:#94a3b8;
  --card:#111827; --card-bd:#1f2937; --chip:#0b1220;
}
html, body {background: var(--bg)!important;}
.section {
  padding:18px 22px; border:1px solid var(--card-bd); border-radius:14px; background:var(--card); margin-bottom:14px;
}
.badge {display:inline-block; padding:6px 10px; border-radius:999px; background:var(--pri-weak); color:#1f2937; font-weight:600; font-size:12px; margin-right:8px}
.pills {display:flex; gap:8px; flex-wrap:wrap;}
.pill {border:1px solid var(--card-bd); background:var(--chip); color:#111827; padding:8px 12px; border-radius:999px; cursor:pointer; font-weight:600}
.pill.active {background:var(--pri); border-color:var(--pri); color:white}
.card {
  border:1px solid var(--card-bd); border-radius:16px; background:var(--card);
  padding:14px; height:100%; display:flex; flex-direction:column; gap:10px;
}
.card h4 {margin:0; font-size:16px}
.card .meta {font-size:12px; color:var(--muted)}
.btn {
  display:inline-block; padding:8px 12px; border-radius:10px; border:1px solid var(--pri);
  color:var(--pri); text-decoration:none; font-weight:700; transition:.15s;
}
.btn:hover {background:var(--pri); color:#fff;}
.footerbar {
  position:sticky; bottom:10px; background:var(--card); border:1px solid var(--card-bd);
  border-radius:12px; padding:10px 14px; display:flex; gap:10px; justify-content:flex-end;
}
.tablecap {color:var(--muted); font-size:13px}
.hero {
  padding:16px; border-radius:16px;
  background:linear-gradient(135deg, var(--pri-weak), transparent);
  border:1px solid var(--card-bd);
}
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)

st.title("날씨 + 위치 기반 음식점 추천🌨️")

#임의 위치 설정 서울에서 실행할 경우 아래 코드 주석 처리 후 32번 줄 return None, None로 변경
seoul_lat, seoul_lon = 37.5665, 126.9780

def get_user_location():
    loc = streamlit_geolocation()  # 버튼/권한 요청
    if not loc or loc.get("latitude") is None or loc.get("longitude") is None:
        return seoul_lat, seoul_lon
    return float(loc["latitude"]), float(loc["longitude"])

#날씨
#카테고리 이름 표준화 (원본 category_map 키에 맞춤)
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

#openweather의 숫자id를 우리 내부 그룹명으로 변환
def weather_group_from_id(weather_id: int) -> str:
    for group_name, codes in WX_GROUPS.items():
        if int(weather_id) in codes:
            return group_name
    return "구름"  #못 찾으면 기본값

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
    headers = [h.replace("|", "\\|") for h in frame2.columns.astype(str).tolist()]
    rows = frame2.values.tolist()

    lines = []
    lines.append("| " + " | ".join(esc(h) for h in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        cells = [str(c).replace("|", "\\|") for c in row]
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

    # 2) 페이지 상태
    total = len(view_df)
    total_pages = max(1, math.ceil(total / page_size))
    state_key = f"page_{table_key}"
    page = int(st.session_state.get(state_key, 1))
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    # 3) 컨트롤(가로) - 버튼 먼저
    col_prev, col_center, col_next = st.columns([0.22, 0.56, 0.22])
    with col_prev:
        prev_click = st.button("◀ 이전", key=f"{table_key}_prev", disabled=(page <= 1))
    label_placeholder = col_center.empty()
    with col_next:
        next_click = st.button("다음 ▶", key=f"{table_key}_next", disabled=(page >= total_pages))

    # 4) 클릭 반영
    if prev_click and page > 1:
        page -= 1
    if next_click and page < total_pages:
        page += 1
    st.session_state[state_key] = page

    # 5) 페이징 슬라이스
    start = (page - 1) * page_size
    end = start + page_size
    page_df = view_df.iloc[start:end].copy()

    # 6) 테이블 렌더 (이름 하이퍼링크)
    name_col = detect_df_col(
        page_df,
        ["name", "place_name", "store_name", "상호명", "상호", "식당명", "업체명", "brand", "title", "poi_name"],
        fuzzy=("name", "place", "상호", "brand", "title"),
    ) or "name"
    dist_col = detect_df_col(
        page_df,
        ["distance_m", "distance", "dist_m", "distance_km"],
        fuzzy=("dist", "distance"),
    )

    headers = ["이름"] + (["거리"] if dist_col else [])
    if add_optional_cols:
        for extra_col in ["category", "address", "도로명주소", "지번주소"]:
            if extra_col in page_df.columns:
                headers.append(extra_col)

    rows_html = []
    for _, row in page_df.iterrows():
        nm = row.get(name_col)
        link_url = row.get("map_link")
        if pd.notna(nm) and pd.notna(link_url) and str(link_url).startswith(("http://", "https://")):
            name_html = f'<a href="{str(link_url)}" target="_blank" rel="noopener">{str(nm)}</a>'
        else:
            name_html = str(nm) if pd.notna(nm) else "이름 없음"

        tds = [f"<td>{name_html}</td>"]
        if dist_col:
            tds.append(f"<td>{format_distance(row.get(dist_col), dist_col)}</td>")
        if add_optional_cols:
            for extra_col in ["category", "address", "도로명주소", "지번주소"]:
                if extra_col in page_df.columns:
                    v = row.get(extra_col, "")
                    tds.append(f"<td>{'' if pd.isna(v) else str(v)}</td>")
        rows_html.append("<tr>" + "".join(tds) + "</tr>")

    thead = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    table_html = f"""
    <div style="border:1px solid #e6e6e6; border-radius:8px; overflow:hidden">
      <table style="width:100%; border-collapse:collapse; font-size:14px">
        <thead style="position: sticky; top: 0; background: #fafafa; z-index: 1;">{thead}</thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)

    label_placeholder.markdown(
        f"<div style='text-align:center; font-size:13px; padding-top:6px'>"
        f"Page <b>{page}</b> / {total_pages}</div>",
        unsafe_allow_html=True,
    )

    return page_df

# ── 지도 (현재 페이지 결과에 맞춰 중심/줌 자동)
def render_map_with_markers(user_lat: float, user_lon: float, frame: pd.DataFrame):
    if frame is None or frame.empty:
        st.info("표시할 식당이 없습니다.")
        return
    if "latitude" not in frame.columns or "longitude" not in frame.columns:
        st.warning("'latitude', 'longitude' 컬럼이 필요합니다.")
        return

    df_map = frame.copy()
    df_map["latitude"]  = pd.to_numeric(df_map["latitude"],  errors="coerce")
    df_map["longitude"] = pd.to_numeric(df_map["longitude"], errors="coerce")
    df_map = df_map.dropna(subset=["latitude","longitude"])
    if df_map.empty:
        st.info("유효한 좌표가 없어 지도를 표시할 수 없습니다.")
        return

    # 좌표 중복 약화
    id_col = next((c for c in ["id","place_id","store_id"] if c in df_map.columns), None)
    name_col = next((c for c in ["name","place_name","store_name","상호명","title"] if c in df_map.columns), None)
    df_map["lat_round"] = df_map["latitude"].round(6)
    df_map["lon_round"] = df_map["longitude"].round(6)
    dup_keys = ["lat_round","lon_round"] + ([id_col] if id_col else ([] if not name_col else [name_col]))
    df_map = df_map.drop_duplicates(subset=[k for k in dup_keys if k])

    addr_col = next((col for col in ["address","addr","도로명주소","지번주소"] if col in df_map.columns), None)
    dist_col = next((col for col in ["distance_m","distance","dist_m","distance_km"] if col in df_map.columns), None)

    rest_points = df_map.rename(columns={"latitude":"lat","longitude":"lon"}).copy()
    me_df = pd.DataFrame([{"lat": user_lat, "lon": user_lon}])

    layers = [
        pdk.Layer(
            "ScatterplotLayer",
            data=rest_points,
            get_position="[lon, lat]",
            get_radius=40,
            radius_min_pixels=3,
            radius_max_pixels=10,
            filled=True, stroked=False,
            get_fill_color=[255, 0, 0, 160],
            pickable=True,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            data=me_df,
            get_position="[lon, lat]",
            get_radius=90,
            radius_min_pixels=7,
            radius_max_pixels=14,
            filled=True, stroked=False,
            get_fill_color=[0, 100, 255, 220],
        ),
    ]

    tooltip_lines = []
    if name_col: tooltip_lines.append(f"이름: {{{{{name_col}}}}}")
    if addr_col: tooltip_lines.append(f"주소: {{{{{addr_col}}}}}")
    if dist_col: tooltip_lines.append(f"거리: {{{{{dist_col}}}}}")
    tooltip = {"text": "\n".join(tooltip_lines)} if tooltip_lines else None

    # 중심/줌 자동 계산 (현재 페이지 결과 기준)
    center_lat, center_lon = user_lat, user_lon
    zoom = 14
    if not rest_points.empty:
        center_lat = float(rest_points["lat"].mean())
        center_lon = float(rest_points["lon"].mean())
        lat_span = float(rest_points["lat"].max() - rest_points["lat"].min())
        lon_span = float(rest_points["lon"].max() - rest_points["lon"].min())
        span = max(lat_span, lon_span)
        if span < 0.0015: zoom = 17
        elif span < 0.003: zoom = 16
        elif span < 0.006: zoom = 15
        else: zoom = 14

    st.pydeck_chart(pdk.Deck(
        map_provider="maplibre",
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=zoom),
        layers=layers,
        tooltip=tooltip,
    ))


#selectbox로 하나의 식당을 고르게 하고, 라벨(“이름, 거리”)과 함께 반환
def pick_one_restaurant(frame: pd.DataFrame) -> Tuple[pd.DataFrame | None, str]:
    if frame is None or frame.empty:
        return None, ""
    name_col = next((col for col in ["name","place_name","store_name","상호명","상호","식당명","업체명","brand","title","poi_name"] if col in frame.columns), None)
    dist_col = next((col for col in ["distance_m","distance","dist_m","distance_km"] if col in frame.columns), None)
    if not name_col:
        exclude = ("addr","address","주소","lat","lon","lng","long","dist","distance","km","id","idx","code","category","업태","tf","w_","flag")
        text_cols = [col for col in frame.columns if frame[col].dtype == object and not any(s in col.lower() for s in exclude)]
        name_col = text_cols[0] if text_cols else None

    def label_for_idx(idx: int) -> str:
        row = frame.loc[idx]
        nm = str(row[name_col]).strip() if (name_col and pd.notna(row.get(name_col))) else "이름 없음"
        dstr = ""
        if dist_col and pd.notna(row.get(dist_col)):
            try:
                d = float(row[dist_col])
                dstr = f"{d:.2f}km" if "km" in dist_col.lower() else f"{int(round(d))}m"
            except Exception:
                dstr = str(row[dist_col])
        return f"{nm} · {dstr}" if dstr else nm

    options = list(frame.index)
    picked_idx = st.selectbox("아래에서 식당을 선택하세요", options=options, format_func=label_for_idx, key="pick_restaurant_select")
    picked_row = frame.loc[[picked_idx]].copy() if picked_idx is not None else None
    return picked_row, (label_for_idx(picked_idx) if picked_row is not None else "")

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
        group_name = weather_group_from_id(w["id"])
        opts, mood = recommended_categories_from_group(group_name)
        st.markdown(f"### 오늘의 날씨는 **{w['description']}**, 기온은 **{w['temperature']}°C** 입니다.")
        st.caption(f"({group_name} / {mood})")
    except Exception as e:
        st.error(f"날씨 정보를 불러오는 데 실패했습니다. ({e})")
        group_name, opts = "구름", ["가볍게 간단히", "든든한 한끼", "디저트/카페"]

    st.write("지금 날씨에 추천 드리는 카테고리입니다.")
    choice = st.radio("선택해 주세요 👇", options=opts, horizontal=False)

    st.divider()

    # 식당
    all_df = get_restaurant_within_500m_from_supabase(user_lat, user_lon)
    # 반경 500m 이내 전체 목록도 동일 스타일(이름에 링크)로 표시
    st.caption("반경 500m 이내 음식점 목록")
    st.write(f"총 {len(all_df)}개")
    _ = render_paginated_clickable_name_table(all_df, table_key="all_df", page_size=10, add_optional_cols=False)

    st.divider()

    # 1차: 날씨 TF(W_그룹) 필터
    wx_df = filter_by_weather_via_categories(all_df, group_name, use_all_cats=True)  # ✅ 교체

    # 2차: 선택 카테고리 TF 필터
    filtered_df = filter_by_category_tf(wx_df, choice)

    st.subheader("카테고리에 해당하는 식당입니다. 자세히 알아보고 싶은 식당을 선택해주세요.")
    st.subheader(f"‘{group_name}’ 날씨 + ‘{choice}’ 카테고리 결과")

    #2.5차 필터
    filtered_df, _selected = select_and_filter_by_business_type(filtered_df, group_name, choice)
    # 링크 컬럼(map_link) 클릭 가능하게 표시
    st.write(f"총 {len(filtered_df)}개")

    # 결과(현재 페이지) 테이블 + 지도
    filtered_page_df = render_paginated_clickable_name_table(
        filtered_df, table_key=f"filtered__{group_name}__{choice}", page_size=10, add_optional_cols=False
    )

    st.subheader("지도에서 보기")
    render_map_with_markers(user_lat, user_lon, filtered_page_df)

    #최종
    st.subheader("하나를 골라볼까요?")
    picked_df, picked_label = pick_one_restaurant(filtered_page_df)

    if st.button("맛집을 정했어요!"):
        if picked_df is not None and not picked_df.empty:
            name_col = next((col for col in
                             ["name", "place_name", "store_name", "상호명", "상호", "식당명", "업체명", "brand", "title",
                              "poi_name"] if col in picked_df.columns), None) or "name"
            nm = str(picked_df.iloc[0].get(name_col, "이름 없음"))
            map_link = picked_df.iloc[0].get("map_link")
            if pd.notna(map_link) and str(map_link).startswith(("http://", "https://")):
                st.markdown(f"**맛집을 정했어요! 🎉 선택한 곳:** [{nm}]({map_link})")
            else:
                st.success(f"맛집을 정했어요! 🎉 선택한 곳: {nm}")

            # 선택한 1행도 같은 스타일(페이지네이션 표)로 미리보기
            _ = render_paginated_clickable_name_table(picked_df, table_key="picked_preview", page_size=10)
        else:
            st.warning("먼저 식당을 하나 선택해 주세요.")

    if st.button("조금 더 둘러볼래요!"):
        st.info("다시 로딩 됩니다. 🔄")
        # st.rerun()  # 필요 시 활성화


if __name__ == "__main__":
    main()
