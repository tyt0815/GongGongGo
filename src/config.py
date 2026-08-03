from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "gonggonggo.db"
JSON_PATH = DATA_DIR / "job_posts.json"
LOG_DIR = PROJECT_ROOT / "logs"

TARGET_URLS = {
    "중앙공기업": "https://cafe.naver.com/f-e/cafes/21737991/menus/193",
    "지방공기업": "https://cafe.naver.com/f-e/cafes/21737991/menus/189",
    "대학/기타기관": "https://cafe.naver.com/f-e/cafes/21737991/menus/232",
    "인턴/계약직": "https://cafe.naver.com/f-e/cafes/21737991/menus/198",
}

DEFAULT_INSTITUTION_KEYWORDS = (
    "대구",
    "한국가스공사",
    "신용보증기금",
    "한국교육학술정보원",
    "한국뇌연구원",
    "한국부동산원",
    "한국사학진흥재단",
    "한국산업기술기획평가원",
    "한국산업단지공단",
    "한국지능정보사회진흥원",
    "한국장학재단",
)

DEFAULT_ROLE_KEYWORDS = (
    "전산",
    "ict",
    "it",
    "디지털",
    "컴퓨터",
    "소프트웨어",
    "sw",
    "네트워크",
    "데이터",
    "인공지능",
    "ai",
    "머신러닝",
    "딥러닝",
    "프로그래밍",
    "백엔드",
    "프론트엔드",
    "풀스택",
    "클라우드",
    "서버",
    "db",
    "데이터베이스",
    "플랫폼",
    "시스템",
    "정보",
)
