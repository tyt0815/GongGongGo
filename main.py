from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from playwright.sync_api import sync_playwright

import json
import os

from src.gongjoonmo_crawler import run_crawler

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 데이터 파일 경로
DATA_FILE = "data/job_posts.json"

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

    keywords = [
        '대구',

        '한국가스공사', '신용보증기금', '한국교육학술정보원', '한국뇌연구원', '한국부동산원',
        '한국사학진흥재단', '한국산업기술기획평가원', '한국산업단지공단', '한국지능정보사회진흥원', '한국장학재단', 
        '농협',

        '전산', 'ICT', 'IT', '정보보호', '디지털', '정보보안',
        '컴퓨터', '소프트웨어', 'SW', '네트워크', '데이터', '인공지능', 'AI', '머신러닝', '딥러닝',
        '프로그래밍', '백엔드', '프론트엔드', '풀스택', 
        '클라우드', '서버', 'DB', '데이터베이스', '플랫폼', '시스템',
    ]

    filtered_posts = [
        p for p in raw_posts 
        if any(k.lower() in p['title'].lower() for k in keywords)
    ]
            
    # 정렬: 마감일 기준 오름차순 (임박순)
    # 1순위: 마감일(deadline) 오름차순, 2순위: 제목(title) 오름차순
    filtered_posts = sorted(
        filtered_posts, 
        key=lambda x: (x.get("deadline", "9999.99.99"), x.get("title", ""))
    )
    
    upcoming_posts = [p for p in filtered_posts if p.get("state") == "지원 예정"]
    unread_posts = [p for p in filtered_posts if p.get("state") == "안읽음"]
    processed_posts = [p for p in filtered_posts if p.get("state") == "완료"]
    undefined_posts = [p for p in filtered_posts if p.get("state") not in ["지원 예정", "안읽음", "완료"]]
    
    for p in undefined_posts:
        print(f"[!] 상태 미정 공고: {p['title']} (링크: {p['link']})")
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "unread_posts": unread_posts, 
        "upcoming_posts": upcoming_posts,
        "processed_posts": processed_posts
    })

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        run_crawler(page)

    import uvicorn
    # 실행 시 브라우저에서 http://127.0.0.1:8000 접속
    uvicorn.run(app, host="127.0.0.1", port=8000)