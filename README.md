# GongGongGo

공공기관 채용 공고와 뉴스·기관소식을 확인하는 개인용 FastAPI 웹앱입니다. 채용과 뉴스는 앱 시작 시 한 번씩 백그라운드에서 수집하고, 화면의 `새로 수집`으로 다시 실행할 수 있습니다.

## Windows 설치·실행

Python 3.12가 설치된 PowerShell에서 프로젝트 폴더를 기준으로 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe main.py
```

브라우저에서 <http://127.0.0.1:8000>에 접속합니다. 일반 실행은 headless이며 `ggg_debug.bat` 또는 `main.py --debug`는 콘솔과 Chromium 창을 표시합니다.

숨김 실행은 다음 명령을 사용합니다. 작업 스케줄러에도 `ggg_startup.vbs`를 등록할 수 있습니다. 기존 BAT 경로는 VBS로 실행을 넘깁니다.

```powershell
wscript.exe .\ggg_startup.vbs
```

`ggg_restart.bat`은 로컬 8000 포트를 사용 중인 서버만 강제 종료하고 숨김 모드로 다시 시작하며 `/health`를 확인합니다. 로그는 `logs/gonggonggo-YYYY-MM-DD.log`에 기록합니다.

## Debian Docker 실행

Docker Engine·Compose 플러그인과 저장소가 준비되어 있고, Debian 호스트와 접속할 PC가 Tailscale에 연결되어 있어야 합니다.

현재 `compose.yaml`은 호스트의 Tailscale IP `100.127.95.13:8000`을 컨테이너의 `8000`에 연결합니다. 다른 서버에서는 `tailscale ip -4`로 확인한 주소로 Compose의 호스트 IP를 바꿉니다. Tailscale 인터페이스가 준비된 뒤 컨테이너를 시작합니다.

기존 데이터를 옮기려면 Windows 서버와 자동 시작 작업을 중지한 뒤 `data/gonggonggo.db`와 남은 `gonggonggo.db-wal`·`gonggonggo.db-shm`을 Debian clone의 `data/`로 복사합니다. JSON 원본을 보관 중이면 함께 복사합니다. 운영 DB와 JSON은 Git clone에 포함되지 않습니다.

Debian 저장소 폴더에서 실행합니다. 컨테이너는 현재 사용자 ID로 호스트의 `data/`와 `logs/`에 쓰므로 두 폴더에 쓰기 권한이 있어야 합니다.

```sh
mkdir -p data logs
APP_UID=$(id -u) APP_GID=$(id -g) docker compose up -d --build
curl http://100.127.95.13:8000/health
docker compose logs --tail=100 gonggonggo
```

브라우저에서 <http://100.127.95.13:8000>에 접속합니다. Compose에서 IP를 바꾸었다면 확인 명령과 접속 주소도 바꿉니다. 접속 허용 여부는 tailnet 접근 정책을 따릅니다.

종료는 `docker compose stop`, 재실행·코드 반영은 위의 `APP_UID`·`APP_GID`를 지정한 `up -d --build` 명령을 사용합니다. 데이터와 로그는 호스트 폴더에 유지됩니다.

## 설정과 사용

우측 상단 `설정`에서 동시 수집 작업 수(`1/2/4`, 기본 `2`), 기관·IT 직무 키워드, 시작 시 브라우저 열기를 저장합니다. 브라우저 열기는 기본 꺼짐이며 서버 노트북에서는 꺼 둡니다.

채용은 검토 대기·지원 예정·지원 완료·제외로 관리합니다. 대상 기관 공고는 직무와 무관하게, 일반 기관 공고는 일치하는 IT 직무가 있을 때 표시합니다.

뉴스·기관소식은 한국경제·매일경제와 한국부동산원·신용보증기금·한국가스공사의 제목과 원문 링크를 제공합니다. 신문은 7일, 기관은 30일 보관하며 `처리 완료`로 현재 목록에서 제거합니다. 본문은 저장하지 않습니다.

수집 실패는 화면의 출처·카테고리 오류와 로그에서 확인합니다. 채용과 뉴스 수집은 독립적이며 주기적 수집·알림은 없습니다.

## 데이터와 백업

- 운영 DB: `data/gonggonggo.db`
- 기존 JSON 원본(있는 경우): `data/job_posts.json`
- 로그: `logs/`

JSON 원본이 있으면 정상 날짜 공고만 한 번 가져오며 원본을 수정하지 않습니다. 이후 사용자 상태와 설정은 SQLite에 저장됩니다.

서버를 완전히 종료한 뒤 DB와 남은 sidecar를 함께 복사합니다. Docker에서는 먼저 `docker compose stop`을 실행합니다. 실행 중인 DB 본체만 복사하거나 백업 없이 DB를 삭제하지 마십시오.

Windows 백업 예시:

```powershell
New-Item -ItemType Directory -Force backup | Out-Null
Copy-Item data\gonggonggo.db* backup\
if (Test-Path data\job_posts.json) { Copy-Item data\job_posts.json backup\job_posts.json }
```

## 테스트와 설계 결정

자동 테스트는 임시 DB·JSON과 외부 네트워크 없는 fake·fixture를 사용합니다. 브라우저 E2E에는 Playwright Chromium이 필요합니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -v
git diff --check
```

프로젝트 작업 규칙은 [AGENTS.md](AGENTS.md), 설계 결정 목록은 [docs/adr.md](docs/adr.md)에 있습니다. ADR 파일은 `docs/`에서 확인합니다.
