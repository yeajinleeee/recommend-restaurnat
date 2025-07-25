import pandas as pd
import chardet
from io import StringIO

def read_csv_with_detected_encoding(filepath):
    # 파일 바이너리로 열어서 인코딩 감지
    with open(filepath, 'rb') as f:
        raw_data = f.read()
        result = chardet.detect(raw_data)
        encoding = result['encoding']
        print(f"{filepath} 인코딩 감지됨: {encoding}")

    # 감지된 인코딩으로 문자열로 읽은 뒤, pandas로 파싱
    with open(filepath, 'r', encoding=encoding, errors='replace') as f:
        text_data = f.read()
        return pd.read_csv(StringIO(text_data))

# 각 파일 읽기
#추가 해야함 

df1 = df1.rename(columns={'열1':'사업장명'})
df2 = df2.rename(columns={'열1':'사업장명'})
df3 = df3.rename(columns={'열1':'사업장명'})
df4 = df4.rename(columns={'열1':'사업장명'})

#데이터 붙이기
total = pd.concat([df1, df2])
total1 = pd.concat([total, df3])
df = pd.concat([total1, df4])

#index reset
df = df.reset_index(drop=True)

# case1 : 구글크롤링 전체 NaN 행 삭제
essential_cols_case1 = ['구글가게명', '구글주소', '별점', '카테고리', '리뷰수', '구글위도', '구글경도']
df = df.dropna(subset=essential_cols_case1, how='all')


# 슬라이스 DataFrame일 경우 copy() 먼저 해주세요
df = df.copy()

# 문자열 "none"을 NaN으로 변환 (한 번만 처리)
df[['리뷰수', '별점', '카테고리']] = df[['리뷰수', '별점', '카테고리']].replace("none", pd.NA)

# 리뷰수 전처리: 괄호, 쉼표 제거
df['리뷰수'] = (
    df['리뷰수']
    .astype(str)
    .str.replace('(', '', regex=False)
    .str.replace(')', '', regex=False)
    .str.replace(',', '', regex=False)
    .str.strip()
)

# 숫자로 변환 + 절댓값 + 정수화 (nullable Int64)
df['리뷰수'] = pd.to_numeric(df['리뷰수'], errors='coerce')
df['리뷰수'] = df['리뷰수'].abs().astype('Int64')

# 별점도 숫자로 변환
df['별점'] = pd.to_numeric(df['별점'], errors='coerce')


# case2: 리뷰수, 별점, 카테고리 모두 NaN인 행 제거
df = df.dropna(subset=['리뷰수', '별점', '카테고리'], how='all')

# case3: 리뷰수, 별점 모두 NaN인 행 제거
df = df.dropna(subset=['리뷰수', '별점'], how='all')

# 리뷰수 결측 삭제 + 인덱스 초기화
df = df.dropna(subset=['리뷰수']).reset_index(drop=True)


#구 채워넣기 

df.loc[df['사업장명'] == '반장떡맥(역삼점)', '구'] = '강남구'
df.loc[df['사업장명'] == '비비큐세곡점', '구'] = '강남구'
df.loc[df['사업장명'] == '단골손님영등포점', '구'] = '영등포구'
df.loc[df['사업장명'] == '주식회사 부여집', '구'] = '영등포구'
df.loc[df['사업장명'] == '희정식당', '구'] = '영등포구'
df.loc[df['사업장명'] == '브이아이피참치 뉴서울호텔점', '구'] = '중구'
df.loc[df['사업장명'] == '술취한고양이', '구'] = '용산구'
df.loc[df['사업장명'] == '낫투두', '구'] = '용산구'
df.loc[df['사업장명'] == '바오쯔', '구'] = '종로구'
df.loc[df['사업장명'] == '메가박스중앙(주) 신촌지점', '구'] = '서대문구'
df.loc[df['사업장명'] == '알 커피 컴퍼니(R coffee company)', '구'] = '서대문구'
df.loc[df['사업장명'] == '소점', '구'] = '마포구'
df.loc[df['사업장명'] == '정광수의 돈까스가게', '구'] = '마포구'
df.loc[df['사업장명'] == '송추가마골 마포지점', '구'] = '마포구'


#업태구분명 채워넣기

df.loc[df['사업장명'] == '소점', '업태구분명'] = '호프/통닭'
df.loc[df['사업장명'] == '불닭발땡초동대문엽기떡볶이대치점', '업태구분명'] = '분식'



# 제거 대상 업태 리스트 정의
remove = [
    '출장조리', '키즈카페', '라이브카페', '룸살롱',
    '다방', '식품소분업', '식품등 수입판매업', '이동조리'
]

# 해당 업태 제거
df = df[~df['업태구분명'].isin(remove)]
