import mysql.connector
import csv
import concurrent.futures

CSV_FILENAME = "서울시 일반음식점(연동)_part3_cleaned.csv"

def connect_to_db():
    conn = mysql.connector.connect(
            host = "localhost",
            user = "계정명",
            password = "비밀번호",
            database = "스키"
        )
    return conn

def check_connection():
    try:
        conn = connect_to_db()

        if conn.is_connected():
            print("데이터베이스 연결 성공!")
        else:
            print("데이터베이스 연결 실패.")

        conn.close()

    except mysql.connector.Error as err:
        print(f"오류 발생 : {err}")

def create_table():
    conn = connect_to_db()
    cursor = conn.cursor()
    sql = """
            CREATE TABLE IF NOT EXISTS restaurants_in_seoul (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255),
                roadname_address VARCHAR(255),
                jibun_address VARCHAR(255),
                rating FLOAT,
                category1 VARCHAR(100),
                category2 VARCHAR(100),
                review_cnt INT,
                latitude VARCHAR(255),
                longitude VARCHAR(255),
                map_link TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
    cursor.execute(sql)
    conn.commit()
    cursor.close()
    conn.close()

    print("테이블이 생성되었습니다.")

def insert_data():
    conn = connect_to_db()
    cursor = conn.cursor()
    sql = """
            INSERT INTO restaurants_in_seoul (name, roadname_address, jibun_address, rating, category1, category2, review_cnt, latitude, longitude, map_link)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,%s)
            """
    with open(CSV_FILENAME, "r", encoding="cp949", errors="replace") as infile:
        reader = csv.DictReader(infile)
        reader.fieldnames = [f.strip() for f in reader.fieldnames] 
        print(reader.fieldnames)
        for row in reader:
            
            cursor.execute(sql, (
                row['사업장명'].strip(),
                row['도로명주소'].strip(),
                row['지번주소'].strip(),
                row['별점'].strip(),
                row['업태구분명'].strip(),
                row['카테고리'].strip(),
                row['리뷰수'].strip(),
                row['위도'].strip(),
                row['경도'].strip(),
                row['지도링크'].strip()
                ))

        conn.commit()
        cursor.close()
        conn.close()

check_connection()
create_table()
insert_data()




            
            
    
    
