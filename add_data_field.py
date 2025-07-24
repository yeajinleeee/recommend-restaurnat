from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import re
import csv
import math
import urllib.parse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import concurrent.futures
import threading


SCROLL_PAUSE_TIME = 5
api_data_cnt = 0
api_data_lock = threading.Lock()

def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('disable-gpu')
    option.add_argument('headless')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(options=options)
    return driver

def encode_url(url):
    return urllib.parse.quote(url, safe=":/?&=+")

def calculate_cosine_similarity(text1, text2):
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform([text1, text2])
    similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
    return similarity

def haversine(lat1, lon1, lat2, lon2):
    # 지구의 반지름 (단위: km)
    R = 6371.0

    # 위도와 경도를 라디안으로 변환
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    # Haversine 공식을 이용한 거리 계산
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance = R * c  # 결과값은 킬로미터 단위
    return distance


def search_google_map(driver, keyword, jibun, roadname, lat, lng):
    driver.get('https://www.google.com/maps/')
    wait = WebDriverWait(driver, 10)

    searchbox = driver.find_element(By.CSS_SELECTOR, 'input#searchboxinput')

    searchbox.send_keys(keyword)

    searchbutton = driver.find_element(By.CSS_SELECTOR, "button#searchbox-searchbutton")
    searchbutton.click()

    time.sleep(3)

    data = {k: "" for k in ["title", "address", "rating", "category", "review_cnt", "lat", "lng", "link"]}
    #print(f'도로명 : {roadname} \n 지번 : {jibun} ')
    print(f'{keyword}의 위경도  : {lat} , {lng}')

    current_url = driver.current_url
    get_data_idx = 1
    try:
        lat = float(lat)  # lat 값을 float로 변환
        lng = float(lng)  # lng 값을 float로 변환
    except ValueError:
        # 위도 또는 경도가 숫자가 아닌 경우, 비어있는 경우 예외 처리
        #print("위도와 경도가 잘못된 값입니다. 주소유사도(get_data 함수)를 사용.")
        get_data_idx = 2
        return 0
    if "search" in current_url:
        scroll = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[1]/div[1]')
        childs = scroll.find_elements(By.CLASS_NAME, 'hfpxzc')
        childs_len = len(childs)

        print(f"🔍 검색된 맛집 개수: {childs_len}개")

        for i in range(3, 3+2*childs_len, 2):
            time.sleep(1)
            global last_height
            global new_height
            
            last_height = driver.execute_script("return document.body.scrollHeight")
            number = 0
            while True:
                number = number+1
                scroll = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[1]/div[1]')
                driver.execute_script('arguments[0].scrollBy(0, 1000);', scroll)
                time.sleep(SCROLL_PAUSE_TIME)
                #print(f'last height: {last_height}')
                scroll = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[1]/div[1]')
                new_height = driver.execute_script("return arguments[0].scrollHeight", scroll)

                #print(f'new height: {new_height}')

                if number == i:
                    break

                if new_height == last_height:
                    break

                last_height = new_height
            try:
                wait.until(EC.element_to_be_clickable(
                    (By.XPATH, f'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[1]/div[1]/div[{i}]/div/a')
                )).click()
            except Exception as e:
                return {"title": "", "address": "", "rating": "", "category": "", "review_cnt": "", "lat": "", "lng": "", "link":""}

            time.sleep(2)
            #data = get_data(data, roadname, jibun)
            if get_data_idx == 1:
                data = get_data_haversine(driver, data, lat, lng)
            else:
                data = get_data(driver, data, roadname, jibun)
                
            if not (data == 0):
                return data
            else:
                data = {k: "" for k in ["title", "address", "rating", "category", "review_cnt", "lat", "lng", "link"]}
    elif "place" in current_url:
        #data = get_data(data, roadname, jibun)
        if get_data_idx == 1:
            data = get_data_haversine(driver, data, lat, lng)
        else:
            data = get_data(driver, data, roadname, jibun)
        if (data == 0):
            data = {k: "" for k in ["title", "address", "rating", "category", "review_cnt", "lat", "lng", "link"]}

    return data

def get_data(driver, data, roadname, jibun):
    try:
        address = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[9]/div[3]/button/div/div[2]/div[1]').text
    except:
        try:
            address = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[7]/div[3]/button/div/div[2]/div[1]').text
        except:
            try:
                address = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[8]/div[3]/button/div/div[2]/div[1]').text
            except:
                driver.back()
                return 0
    #print(f'검색 주소 : {address}')
    roadname_similarity = calculate_cosine_similarity(roadname ,address)
    jibun_similarity = calculate_cosine_similarity(jibun ,address)

    print(f"지번 주소 유사도 : {roadname_similarity}, 도로명 주소 유사도 : {jibun_similarity}\n")
    if not (roadname_similarity >= 0.7 or jibun_similarity >= 0.7):
        driver.back()
        return 0
    

    data["title"] = driver.find_element(By.XPATH,'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[2]/div/div[1]/div[1]/h1').text
    data["address"] = address
    
        
    try:
        data["rating"] = driver.find_elementd_element(By.XPATH,'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[2]/div/div[1]/div[2]/div/div[1]/div[2]/span[1]/span[1]').text
    except:
        data["rating"] = "none"
    try:
        data["category"] = driver.find_element(By.XPATH,'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[2]/div/div[1]/div[2]/div/div[2]/span[1]/span/button').text
    except:
        data["category"] = "none"
    try:
        data["review_cnt"] = driver.find_element(By.XPATH,'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[2]/div/div[1]/div[2]/div/div[1]/div[2]/span[2]/span/span').text
    except:
        data["review_cnt"] = "none"
    url = driver.current_url
    data["link"] = url
    match = re.search(r'!3d([0-9\.\-]+)!4d([0-9\.\-]+)', url)
    if match:
        data["lat"], data["lng"] = match.group(1), match.group(2)
    else:
        data["lat"], data["lng"] = "", ""

    return data

def get_data_haversine(driver, data, lat, lng):
    
    url = driver.current_url
    match = re.search(r'!3d([0-9\.\-]+)!4d([0-9\.\-]+)', url)
    if match:
        data["lat"], data["lng"] = float(match.group(1)), float(match.group(2))
    else:
        data["lat"], data["lng"] = "", ""
    #print(f'검색 위경도  : {data["lat"]}, {data["lng"]}')
    
    distance = haversine(lat, lng, data["lat"], data["lng"])
    if(haversine(lat, lng, data["lat"], data["lng"]) >= 0.025):
        driver.back()
        return 0
    else:
        print(f"{distance}차이는 같은 장소입니다. csv에 추가하였습니다.")
    try:
        address = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[9]/div[3]/button/div/div[2]/div[1]').text
    except:
        try:
            address = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[7]/div[3]/button/div/div[2]/div[1]').text
        except:
            try:
                address = driver.find_element(By.XPATH, '//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[8]/div[3]/button/div/div[2]/div[1]').text
            except:
                driver.back()
                return 0
    
    data["title"] = driver.find_element(By.XPATH,'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[2]/div/div[1]/div[1]/h1').text
    data["address"] = address
    data["link"] = url    
    try:
        data["rating"] = driver.find_element(By.XPATH,'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[2]/div/div[1]/div[2]/div/div[1]/div[2]/span[1]/span[1]').text
    except:
        data["rating"] = "none"
    try:
        data["category"] = driver.find_element(By.XPATH,'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[2]/div/div[1]/div[2]/div/div[2]/span[1]/span/button').text
    except:
        data["category"] = "none"
    try:
        data["review_cnt"] = driver.find_element(By.XPATH,'//*[@id="QA0Szd"]/div/div/div[1]/div[2]/div/div[1]/div/div/div[2]/div/div[1]/div[2]/div/div[1]/div[2]/span[2]/span/span').text
    except:
        data["review_cnt"] = "none"

    #print(f'link : {data["link"]}')
    return data

def process_row(row):
    global api_data_cnt
    
    keyword = row["사업장명"] + " " + row["구"]
    jibun = row["지번주소"]
    roadname = row["도로명주소"]
    lat = row["위도"]
    lng = row["경도"]

    driver = get_driver()

    try:
        extra_data = search_google_map(driver, keyword, jibun, roadname, lat, lng)
        if extra_data == 0:
            extra_data = {k: "" for k in ["title", "address", "rating", "category", "review_cnt", "lat", "lng", "link"]}
        else:
            with api_data_lock:
                api_data_cnt += 1
    except Exception as e:
        print(f"에러 발생: {e}")
        extra_data = {k: "" for k in ["title", "address", "rating", "category", "review_cnt", "lat", "lng", "link"]}
    
    driver.quit()

    row.update({
        "구글가게명": extra_data["title"],
        "구글주소": extra_data["address"],
        "별점": extra_data["rating"],
        "카테고리": extra_data["category"],
        "리뷰수": extra_data["review_cnt"],
        "구글위도": extra_data["lat"],
        "구글경도": extra_data["lng"],
        "지도링크": encode_url(extra_data["link"])
    })
    return row
        
    

with open("part_2-1.csv", "r", encoding='utf-8-sig', errors='replace') as infile,\
open("서울시 일반음식점(연동)_part_2-1.csv", "w", encoding='utf-8-sig', newline="") as outfile:
    reader = csv.DictReader(infile)
    fieldnames = reader.fieldnames + ["구글가게명", "구글주소", "별점", "카테고리", "리뷰수", "구글위도", "구글경도", "지도링크"]
    writer = csv.DictWriter(outfile, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(process_row, reader)

        for row in results:
            writer.writerow(row)                         

print(f'api 연동된 개수 : {api_data_cnt}')

        
