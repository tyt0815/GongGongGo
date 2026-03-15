from playwright.sync_api import sync_playwright
import json
import os
from datetime import datetime

# --- 설정 변수 (이곳을 직접 수정하세요) ---
TARGET_URL = {
    "중앙 공기업" : "https://cafe.naver.com/f-e/cafes/21737991/menus/193",
    "지방 공기업" : "https://cafe.naver.com/f-e/cafes/21737991/menus/189",
}

def generate_css_selector(idx: int, first_page: bool) -> str:
    if first_page:
        return f"#cafe_content > div.article-board > table > tbody:nth-child(6) > tr:nth-child({idx}) > td:nth-child(2) > div > div > a"
    else:
        return f"#cafe_content > div.article-board > table > tbody > tr:nth-child({idx}) > td:nth-child(2) > div > div > a"
    
def parse_deadline(deadline_str: str) -> str:
    """
    "3.22" 또는 "~3.22"를 받아 "2026.03.22" 형식으로 반환
    """
    # 1. 특수문자 제거 및 공백 정리
    clean_str = deadline_str.replace("~", "").strip()
    
    try:
        # 2. 월, 일 분리
        month, day = map(int, clean_str.split('.'))
        
        # 3. 연도 결정 로직 (현재 연도 사용)
        year = datetime.now().year
        
        # 4. 연말 예외 처리: 현재 12월인데 공고가 1월이면 내년으로 간주
        if datetime.now().month == 12 and month == 1:
            year += 1
            
        # 5. YYYY.MM.DD 형식으로 반환 (앞에 0을 붙여 03.22로 만듦)
        return f"{year}.{month:02d}.{day:02d}"
    
    except Exception:
        # 날짜 형식이 아니면(예: "채용시 마감") 원본 그대로 반환하거나 특정값 리턴
        return clean_str

def clean_post_data(post_data: list) -> list:
    # 비교를 위해 오늘 날짜를 문자열(YYYY.MM.DD)로 변환
    today_str = datetime.now().strftime("%Y.%m.%d")
    final_data = []

    for post in post_data:
        deadline = post["deadline"]
        
        # 1. 날짜 형식이 아닌 경우(예: "채용시 마감")는 무조건 유지
        if len(deadline.split('.')) != 3:
            final_data.append(post)
            continue
            
        # 2. 날짜가 지났는지 비교
        # 문자열 비교(YYYY.MM.DD)는 날짜 객체 비교와 동일한 결과를 줍니다.
        if deadline >= today_str:
            final_data.append(post)
        else:
            print(f"[!] 마감된 공고 제거: {post['title']} (마감일: {deadline})")
    
    return final_data

def load_json(file_path):
    """JSON 파일에서 리스트 데이터를 읽어옵니다."""
    if not os.path.exists(file_path):
        print(f"[!] 파일을 찾을 수 없습니다: {file_path}")
        return []
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] JSON 파일을 읽는 도중 오류 발생: {e}")
        return []

def save_json(file_path, data):
    """
    딕셔너리 리스트를 JSON 파일로 저장합니다.
    """
    try:
        # 디렉토리가 없으면 생성 (안전장치)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, "w", encoding="utf-8") as f:
            # indent=4: 가독성을 위해 들여쓰기 적용
            # ensure_ascii=False: 한글 깨짐 방지
            json.dump(data, f, indent=4, ensure_ascii=False)
            
        print(f"[+] 데이터 저장 완료: {file_path}")
        
    except Exception as e:
        print(f"[!] JSON 파일 저장 중 오류 발생: {e}")

def run_crawler():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        file_path = os.path.join("data", "job_posts.json")
        existing_posts = load_json(file_path)
        existing_links = {post["link"] for post in existing_posts}
        all_new_posts = []

        for category, url in TARGET_URL.items():
            stop_category = False  # 해당 카테고리 중단 플래그

            print(f"\n[{category}] 크롤링 시작...")

            for page_num in range(1, 10):
                if stop_category: break # 이전 페이지에서 중복 발견 시 다음 페이지도 스킵

                page_url = f"{url}?viewType=L&page={page_num}"
                page.goto(page_url, wait_until="networkidle")
                first_page = (page_num == 1)
                
                
                for post_idx in range(1, 15):
                    try:
                        css_selector = generate_css_selector(post_idx, first_page)

                        # 게시글 정보 크롤링 시도
                        page.wait_for_selector(css_selector, timeout=10000)
                        element = page.query_selector(css_selector)

                        link = element.get_attribute("href")
                        # --- 중복 체크 로직 ---
                        if link in existing_links:
                            stop_category = True
                            break 
                        # ---------------------

                        title = element.inner_text().strip()

                        deadline = parse_deadline(title[title.rfind("(") + 1 : title.rfind(")")])

                        post_info = { 
                            "category": category,
                            "title": title,
                            "deadline": deadline,
                            "link": link,
                            "state" : "안읽음",
                        }

                        max_title_length = 30
                        if len(title) > max_title_length:
                            print(f"[+] {title[:max_title_length]}... (마감: {deadline})")
                        else:
                            print(f"[+] {title} (마감: {deadline})")
                        all_new_posts.append(post_info)
                    except Exception:
                        break

        existing_posts = clean_post_data(existing_posts)
        all_new_posts = clean_post_data(all_new_posts)
        posts_data = all_new_posts + existing_posts
        save_json(file_path, posts_data)
        print(f"총 {len(all_new_posts)}개의 새로운 공고를 저장했습니다.")

        browser.close()

if __name__ == "__main__":
    run_crawler()