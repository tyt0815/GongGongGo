from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import json
import os

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 데이터 파일 경로
DATA_FILE = "data/중앙 공기업.json"

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
    posts = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)

    # 필터링할 키워드 리스트
    keywords = ['전산', 'ICT', 'IT']

    filtered_posts = [
        p for p in posts 
        if '신입' in p['title'] and any(k.lower() in p['title'].lower() for k in keywords)
    ]
            
    # 정렬: 마감일 기준 오름차순 (임박순)
    filtered_posts = sorted(filtered_posts, key=lambda x: x.get("deadline", "9999.99.99"))
    
    upcoming_posts = [p for p in filtered_posts if p.get("state") == "접수 예정"]
    unread_posts = [p for p in filtered_posts if p.get("state") == "안읽음"]
    processed_posts = [p for p in filtered_posts if p.get("state") == "완료"]
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "unread_posts": unread_posts, 
        "upcoming_posts": upcoming_posts,
        "processed_posts": processed_posts
    })

if __name__ == "__main__":
    import uvicorn
    # 실행 시 브라우저에서 http://127.0.0.1:8000 접속
    uvicorn.run(app, host="127.0.0.1", port=8000)