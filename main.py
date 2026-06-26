from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from playwright.sync_api import sync_playwright

import json
import os

import uvicorn

from src.gongjoonmo_crawler import run_crawler

app = FastAPI()
templates = Jinja2Templates(directory="./templates")

# 데이터 파일 경로
DATA_FILE = "data/job_posts.json"

def is_target_post(title):
    title_lower = title.lower()

    # 1. 기관/지역 키워드 (대구 및 특정 공사/재단)
    institutions = [
        '대구', '한국가스공사', '신용보증기금', '한국교육학술정보원', '한국뇌연구원', '한국부동산원',
        '한국사학진흥재단', '한국산업기술기획평가원', '한국산업단지공단', '한국지능정보사회진흥원', '한국장학재단'
    ]
    
    # 2. 직무 키워드
    tech_roles = [
        '전산', 'ict', 'it', '디지털', '컴퓨터', '소프트웨어', 'sw', '네트워크', 
        '데이터', '인공지능', 'ai', '머신러닝', '딥러닝', '프로그래밍', '백엔드', 
        '프론트엔드', '풀스택', '클라우드', '서버', 'db', '데이터베이스', '플랫폼', '시스템'
    ]

    # 키워드 포함 여부 확인 함수
    contains_inst = any(k.lower() in title_lower for k in institutions)
    contains_tech = any(k.lower() in title_lower for k in tech_roles)
    
    has_intern = '인턴' in title_lower
    has_newcomer = '신입' in title_lower
    has_regular = '정규직' in title_lower

    # 조건 로직
    # A. 특정 기관이 포함된 경우: '인턴' 또는 '신입'이 있으면 True
    if contains_inst:
        if has_intern or has_newcomer:
            return True

    # B. 그 외 직무 키워드가 포함된 경우: '신입'과 '정규직'이 모두 있어야 True
    if contains_tech:
        if has_newcomer and has_regular:
            return True

    return False

class StatusUpdate(BaseModel):
    link: str
    state: str

@app.post("/update-status")
def update_status(data: StatusUpdate):
    # 1. 기존 데이터 읽기
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)
    
    # 2. 해당 링크 찾아서 상태 변경
    for post in posts:
        if post["link"] == data.link:
            post["state"] = data.state
            break
            
    # 3. 변경된 데이터 저장
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=4, ensure_ascii=False)
        
    return {"status": "success"}

@app.get("/")
def home(request: Request):
    # JSON 파일 읽기 (없으면 빈 리스트)
    raw_posts = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw_posts = json.load(f)

    filtered_posts = [p for p in raw_posts if is_target_post(p["title"])]
            
    # 정렬: 마감일 기준 오름차순 (임박순)
    # 1순위: 마감일(deadline) 오름차순, 2순위: 제목(title) 오름차순
    filtered_posts = sorted(
        filtered_posts, 
        key=lambda x: (x.get("deadline", "9999.99.99"), x.get("title", ""))
    )
    
    upcoming_posts = [p for p in filtered_posts if p.get("state") == "지원 예정"]
    unread_posts = [p for p in filtered_posts if p.get("state") == "대기"]
    processed_posts = [p for p in filtered_posts if p.get("state") == "완료"]
    undefined_posts = [p for p in filtered_posts if p.get("state") not in ["지원 예정", "대기", "완료"]]
    
    for p in undefined_posts:
        print(f"[!] 상태 미정 공고: {p['title']} (링크: {p['link']})")
    
    return templates.TemplateResponse(
        request=request,
        name="index.html", 
        context={
            "request": request, 
            "unread_posts": unread_posts, 
            "upcoming_posts": upcoming_posts,
            "processed_posts": processed_posts
        }
    )

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        run_crawler(page)

    
    uvicorn.run(app, host="127.0.0.1", port=8000)