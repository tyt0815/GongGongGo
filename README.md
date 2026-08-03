# GongGongGo

공공기관 채용 공고를 수집하고 검토 상태를 관리하는 Windows 로컬 전용 웹앱입니다. FastAPI 서버는 `127.0.0.1:8000`에서 먼저 응답하고, 시작 시 1회 크롤링은 백그라운드에서 진행됩니다.

## 주요 기능

- `검토 대기`, `지원 예정`, `지원 완료`, `제외`의 네 상태로 공고 관리
- QHD 이상 넓은 화면에서는 선택한 묶음의 두 목록을 나란히 표시하고, 1,400px 미만에서는 한 상태 탭만 표시
- 기관·고용 형태·경력·직무를 구조화해 표시하고, 파싱에 실패하면 원본 제목으로 표시
- SQLite의 짧은 트랜잭션으로 크롤링 중에도 사용자 상태 변경을 보존
- Async Playwright 카테고리 수집과 `1/2/4` 동시 작업 설정
- 수집 진행률, 부분 실패 사유와 실패 카테고리 재시도
- 기관·IT 직무 키워드, 브라우저 자동 열기 설정 저장
- 제외 실행 취소, 영구 삭제 링크 차단과 차단 해제
- 날짜별 UTF-8 로그와 시작 시 14일 초과 로그 정리

## 설치

Python 3.12가 설치된 PowerShell에서 프로젝트 폴더를 기준으로 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

`requirements-dev.txt`는 실행 의존성과 pytest 의존성을 함께 설치합니다. 이미 `.venv`와 Chromium이 준비되어 있으면 이 단계는 다시 실행할 필요가 없습니다.

## 실행

문제를 확인하며 실행할 때는 다음 BAT를 사용합니다. 현재 콘솔에 로그가 표시되고 서버 종료 뒤 창이 유지됩니다.

```powershell
.\ggg_debug.bat
```

브라우저에서 <http://127.0.0.1:8000>으로 접속합니다. 설정의 `시작 시 브라우저 열기`를 켜면 다음 시작부터 `/health` 응답 후 브라우저를 엽니다. 기본값은 꺼짐입니다.

작업 스케줄러처럼 창 없이 실행하려면 `ggg_startup.vbs`를 직접 등록하는 것이 가장 조용합니다. 기존 작업 스케줄러가 `ggg_startup.bat`을 가리키고 있어도 BAT가 VBS에 실행을 넘기고 즉시 끝납니다.

```powershell
wscript.exe .\ggg_startup.vbs
```

두 실행 경로 모두 `logs/gonggonggo-YYYY-MM-DD.log`에 같은 앱 로그를 남깁니다. 서버는 로컬 주소에만 바인딩되며 외부 접속용 인증이나 배포 기능은 없습니다.

## 웹 설정

우측 상단 `설정`에서 다음 값을 저장할 수 있습니다.

- 동시 수집 작업 수: `1`, `2`, `4` 중 하나, 기본 `2`
- 시작 시 브라우저 열기: 기본 꺼짐
- 대상 기관 키워드: 파싱된 기관명에 대소문자 무시 부분 일치
- IT 직무 키워드: 파싱된 직무에 대소문자 무시 부분 일치

대상 기관 공고는 직무와 무관하게 표시합니다. 일반 기관 공고는 직무 키워드가 일치할 때만 표시하고, 일치한 직무만 카드에 보여 줍니다. 키워드를 저장하면 재크롤링 없이 현재 목록에 바로 다시 적용됩니다.

## 데이터와 마이그레이션

- 운영 DB: `data/gonggonggo.db`
- 기존 원본: `data/job_posts.json`

DB가 처음 준비될 때 기존 JSON에서 마감일이 정확한 `YYYY.MM.DD` 형식인 공고만 한 번 가져옵니다. 날짜를 파싱할 수 없는 기존 공고는 건너뛰며 JSON 파일은 수정하거나 삭제하지 않습니다. 이후 새로 수집한 상시·날짜 미확인 공고는 SQLite에 저장할 수 있습니다.

서버를 완전히 종료한 뒤 DB와 원본 JSON을 함께 복사하면 백업할 수 있습니다.

```powershell
New-Item -ItemType Directory -Force backup | Out-Null
Copy-Item data\gonggonggo.db backup\gonggonggo.db
Copy-Item data\job_posts.json backup\job_posts.json
```

SQLite가 WAL 파일을 사용하므로 실행 중인 DB 파일 하나만 복사하지 마십시오. 운영 데이터 초기화나 재마이그레이션은 자동 복구 동작이 아니므로, DB 파일을 직접 삭제하기 전에 반드시 백업하십시오.

## 테스트

전체 테스트는 임시 DB와 임시 JSON을 사용합니다. 브라우저 E2E는 무네트워크 가짜 크롤링 관리자를 주입하므로 네이버에 접속하거나 운영 상태를 변경하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

브라우저 실행 파일이 없다면 먼저 다음을 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

## 운영 범위와 제한

- 시작 시 1회 및 웹의 수동 수집만 지원하며 주기적 반복 수집과 알림은 없습니다.
- Naver Cafe의 네트워크 상태나 화면 구조 변경으로 일부 카테고리가 실패할 수 있습니다. 성공 결과는 보존되고 실패 카테고리는 화면에서 다시 시도할 수 있습니다.
- 한 컴퓨터의 한 사용자를 위한 로컬 앱이며 외부 접속, 인증, 데스크톱 앱 패키징은 범위 밖입니다.
- 상세 설계는 [전체 개선 설계](docs/superpowers/specs/2026-08-03-gonggonggo-modernization-design.md), 다음 작업자를 위한 실제 구현 정보는 [인수인계](docs/HANDOFF.md)에 있습니다.
