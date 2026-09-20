# GongGongGo 인수인계

최종 갱신: 2026-09-20

## 현재 단계

2026-08-03 전체 개선 설계와 2026-08-25 뉴스 Inbox 설계가 구현되었습니다. 앱은 SQLite를 사용하고 서버 준비와 백그라운드 크롤링을 분리하며, 채용 상태 관리와 짧게 보관하는 뉴스·기관소식 Inbox를 제공합니다. 2026-09-20에는 Debian Docker Compose 실행 설정을 추가했습니다.

승인된 인터페이스는 [전체 개선 설계](superpowers/specs/2026-08-03-gonggonggo-modernization-design.md)와 [뉴스 Inbox 설계](superpowers/specs/2026-08-25-news-inbox-design.md)에 있습니다. 범위 변경은 없었으므로 `AGENTS.md`의 핵심 제약은 그대로 유효합니다.

## 실제 코드 구조

- `main.py`: 앱 생성과 공통 런타임 진입점
- `src/config.py`: 로컬 경로, 네 카테고리 URL과 기본 키워드
- `src/domain.py`: 상태, 설정, 파싱·수집 결과 모델
- `src/parsing.py`: 제목·마감 파싱과 기관/직무 필터 순수 함수
- `src/database.py`: SQLite 스키마, WAL/busy timeout, 1회 JSON 마이그레이션
- `src/repository.py`: 상태 보존 upsert, 설정, 영구 삭제 링크와 실행 이력
- `src/crawler.py`: Async Playwright 카테고리 수집과 동시성 제한
- `src/crawl_manager.py`: 단일 실행 가드, 진행 snapshot, 부분 실패와 저장 조정
- `src/web.py`: FastAPI lifespan, HTML/static, 검증된 JSON API
- `src/news/domain.py`, `registry.py`: 뉴스 자료·기간·실행 모델과 다섯 출처/TTL registry
- `src/news/http.py`, `url_normalization.py`, `classification.py`: 제한된 목록 요청, URL 정규화, 한국부동산원 정기 통계 분류
- `src/news/sources/`: 한국경제·매일경제 정적 HTML 목록과 한국부동산원·신용보증기금·한국가스공사 공식 목록 parser
- `src/news/repository.py`: `news_items` 보관·필터·TTL 정리와 `news_dismissals` 임시 억제 트랜잭션
- `src/news/manager.py`: 다섯 출처의 독립 병렬 실행, 출처별 실패 격리와 snapshot
- `templates/index.html`, `static/app.css`, `static/app.js`: 기존 채용 대시보드와 설정 UI
- `static/news.css`, `static/news.js`: 뉴스 전환, 필터·목록·처리 완료와 뉴스 전용 polling
- `src/logging_config.py`, `src/runtime.py`: 날짜별 로그, 보존 정리, 선택적 브라우저 열기, 로컬 Uvicorn 실행
- `ggg_startup.vbs`: 숨김 실행, `ggg_startup.bat`: 기존 스케줄러용 VBS 래퍼
- `ggg_restart.bat`: loopback 8000 포트의 PID만 종료하고 숨김 재시작한 뒤 `/health`를 확인하는 운영 재시작
- `ggg_debug.bat`: `main.py --debug`으로 콘솔과 Playwright Chromium GUI를 함께 표시하는 디버그 실행
- `Dockerfile`, `compose.yaml`, `.dockerignore`: Debian에서 Python 3.12·Playwright Chromium을 설치하고 호스트의 `data/`·`logs/`를 연결하는 실행 설정
- `tests/`: 파서·DB·저장소·크롤러·관리자·API·UI 계약 테스트
- `tests/e2e/test_dashboard_layout.py`: 임시 DB/JSON, 무네트워크 관리자와 실제 Chromium을 사용하는 loopback E2E

## 중요한 동작과 불변 조건

- Windows 직접 실행은 `127.0.0.1:8000`에 바인딩합니다. Docker 실행은 컨테이너 안에서 `0.0.0.0:8000`으로 수신하고 Debian 호스트의 `127.0.0.1:8000`에만 포트를 공개합니다. 다른 PC에서는 Debian 호스트의 Tailscale Serve를 통해 tailnet 내부 HTTPS 주소로 접근합니다.
- lifespan은 저장소를 준비하고 시작 크롤링을 예약하지만 완료를 기다리지 않고 요청을 받습니다.
- 크롤러는 기존 행의 수집 소유 필드만 갱신하며 `status`와 `status_updated_at`을 덮지 않습니다.
- 신규 크롤링 insert는 `is_new=1`로 저장되어 검토 대기 상단에 최신순으로 표시됩니다. 기존 DB 행과 JSON 마이그레이션 행은 `is_new=0`입니다.
- 지원 예정의 `is_pinned`는 사용자 소유 필드입니다. 목록은 기관 필터+핀 → 기관 필터 → 핀 → 일반 순이며 각 핀 그룹은 마감일 오름차순입니다. 핀은 재시작 후에도 유지되고 지원 예정에서 벗어나면 자동 해제되며, 크롤러 upsert는 이 값을 덮지 않습니다.
- `확인` API는 상태를 유지한 채 `is_new`만 해제하고, 상태 API는 상태 변경과 함께 `is_new`를 해제합니다. 공고 제목 링크를 여는 것만으로는 해제되지 않습니다.
- 사용자 API는 연결을 공유하지 않고 짧은 SQLite 트랜잭션을 사용합니다.
- 전체 수집 중 하나의 카테고리가 실패해도 다른 카테고리 결과를 저장하며, 같은 시점의 중복 실행은 거절합니다.
- JSON 마이그레이션은 정확한 `YYYY.MM.DD` 공고만 한 번 가져오고 원본 파일은 그대로 둡니다.
- 수집 결과 저장 시 오늘보다 마감일이 지난 `dated` 공고를 제거하며 오늘 마감·상시·미확인 공고는 유지합니다. 자동 만료에는 링크 차단을 남기지 않습니다.
- 영구 삭제는 본문을 제거하고 `deleted_links`에 링크 차단을 남깁니다.
- 대상 기관은 직무와 무관하게 표시하고, 일반 기관은 파싱된 직무 키워드가 일치할 때만 표시합니다. API의 `is_institution_match`로 기관 필터 일치 여부를 전달하고, UI는 별도 문구나 배지 없이 해당 기관명 글자만 일반 링크와 다른 색으로 구분합니다.
- 설정 drawer는 저장된 값을 모두 읽은 뒤 열어, 로딩 중 사용자 입력이 늦은 응답에 덮이지 않게 합니다.
- 넓은 화면은 현재 선택한 묶음의 두 lane만 표시하고, 좁은 화면은 네 상태 중 한 panel만 표시합니다.
- 일반 및 숨김 실행의 크롤러는 headless 모드이며, `--debug` 실행에서만 실제 Chromium 창을 표시합니다.
- 뉴스는 공식 목록에서 제목·링크·출처·통합/원본 분류·게시일시를 수집합니다. 매일경제 목록에 게시일시가 없으면 현재 최신 목록 전체를 포괄하도록 실행당 최대 100개의 공식 기사 페이지에서 공개 메타데이터만 확인하며, 본문·첨부파일은 파싱하거나 저장하지 않습니다. 상세 요청 실패는 해당 항목의 최초 수집일 fallback으로 격리합니다.
- 뉴스 필터는 소제목 없이 자료 종류·기간·출처·분류·제목 검색을 한 줄씩 쌓습니다. 자료 종류는 전체·뉴스·기관소식 버튼이며 화면을 열 때 전체가 기본입니다. 출처·분류는 복수 체크박스이고 마지막 체크 상태를 브라우저 `localStorage`에 보존합니다. 목록 위의 `총 N건`은 현재 필터 결과와 처리 완료를 즉시 반영합니다. 제목이 원문 링크이므로 별도 `원문 보기` 링크는 표시하지 않습니다.
- 한국경제와 매일경제는 `정치`, `경제`, `사회`, `IT`, `세계`, `주요뉴스`로 통합합니다. 세 기관은 기본 `보도자료`이며 한국부동산원의 명백한 정기 조사·동향 제목만 `정기 통계`입니다.
- `news_items`는 URL 고유 항목과 최초 수집시각을 보관합니다. 저장 경계에서도 서울 달력 날짜로 신문 7일·기관 30일의 inclusive TTL과 오늘 상한을 강제하며, 게시일이 없으면 최초 수집일을 기준으로 합니다.
- `처리 완료`는 한 트랜잭션에서 `news_items` 행을 삭제하고 `news_dismissals`를 원래 TTL 경계까지만 upsert합니다. 억제 만료 뒤에도 원래 게시일이 TTL을 벗어난 항목은 다시 저장하지 않으며, 유효 날짜로 다시 나타난 항목만 재수집될 수 있습니다. 이는 영구 차단이 아닙니다.
- `CrawlManager`와 `NewsCrawlManager`는 실행 가드, 상태, 수동 시작과 실패를 공유하지 않습니다. lifespan은 둘을 각각 한 번 시작하고 완료를 기다리지 않으며, 종료 시 둘을 함께 기다립니다.

## 데이터와 안전한 작업 방법

운영 파일은 런타임 `data/gonggonggo.db`입니다. `data/job_posts.json`은 기존 JSON 원본이 있는 경우에만 사용하며, 2026-09-20 현재 Git에서 제거되어 clone에 포함되지 않습니다. 뉴스도 같은 DB 파일 안의 독립 `news_items`, `news_dismissals` 테이블을 사용하지만 채용 행을 갱신하지 않습니다. 테스트는 항상 `tmp_path` 아래에 별도 JSON과 DB를 만들며 Naver와 뉴스 출처에 접근하지 않는 fake를 사용합니다. 운영 상태 변경, 영구 삭제, 차단 해제, 뉴스 처리 완료 테스트에 운영 DB를 사용하지 마십시오.

백업은 서버를 종료한 상태에서 DB와 남은 WAL sidecar를 함께 복사하고, JSON 원본이 있으면 함께 보관합니다. 실행 중에는 DB 본체만 복사하지 않습니다. `.gitignore`는 DB, DB sidecar, 로그, `.superpowers/` 작업 산출물을 제외합니다.

Debian 이전 시 기존 Windows 서버와 자동 시작 작업을 중지하고 DB와 남은 sidecar를 clone의 `data/`로 복사합니다. JSON 원본이 있으면 함께 복사합니다. Compose는 호스트 `data/`와 `logs/`를 연결하며, 컨테이너 실행 사용자 ID는 `APP_UID`·`APP_GID`로 호스트 사용자에 맞춥니다. Docker 이미지 빌드 컨텍스트에서 운영 데이터와 로그는 제외합니다. 실행·Tailscale Serve 명령은 README의 Debian Docker 실행 절에 있습니다. 이 Windows 작업 환경에는 Docker CLI가 없어 실제 이미지 빌드와 컨테이너 시작 검증은 Debian에서 수행해야 합니다.

## 검증 경로

```powershell
.\.venv\Scripts\python.exe -m pytest -v
git diff --check
```

브라우저 E2E는 다음을 실제 DOM과 API 경계에서 검증합니다.

- 2560×1440에서 두 lane, 1280×1440에서 한 상태 panel
- 크롤링 snapshot이 실행 중일 때 상태 이동
- 제외 후 실행 취소
- 설정 저장 뒤 앱 재시작 시 동시성, 브라우저 열기, 키워드 유지
- 키워드 변경 직후 현재 공고의 표시 직무 재필터링
- `/`가 포함된 실패 카테고리 재시도와 완료 polling
- 실행 단위 저장 실패 표시와 빠른 수집 완료 후 버튼/목록 갱신
- 신규 공고 상단 정렬, `NEW` 배지, 제목 링크 유지, 확인·상태 이동 시 해제
- 지원 예정의 기관 필터+핀 → 기관 필터 → 핀 → 일반 우선순위, 핀 그룹 마감일순 정렬과 재시작 후 유지
- 카드 내부 버튼, 구조화·fallback 제목, 긴 직무 tooltip, 기관 필터 일치 기관명 색상, 설정 drawer, 진행 상태와 한국어 렌더링
- 뉴스 기간·자료 종류 버튼·출처/분류 복수 체크·제목 필터 각각의 비교 행 제외, 체크 상태 재방문 복원, 제목 새 탭 원문과 처리 완료 후 행 제거
- 매일경제 공식 기사 메타데이터 날짜 보강과 개별 상세 요청 실패 시 최초 수집일 fallback
- 390px 뉴스 행의 가로 overflow 방지, 탭 왕복 뒤 채용 상태 panel 유지
- 뉴스 수집이 진행 중이어도 채용 수동 수집 버튼을 사용할 수 있는 manager 독립성
- 뉴스 수동 수집 버튼의 running→부분 실패 terminal polling, 버튼 재활성화와 신규 결과 refresh

2026-08-25 Python 3.12.10 전체 실행 결과는 `222 passed, 0 warnings`입니다. 브라우저 E2E 12개와 다섯 출처 fixture parser 테스트를 포함하며, `node --check static/app.js`와 `node --check static/news.js`도 통과했습니다. 자동화 테스트는 모두 오프라인이고 임시 DB를 사용합니다.

2026-08-25 안전한 시작 smoke는 실제 `create_app`과 Uvicorn을 임시 DB·JSON 및 임의 loopback 포트에서 실행했습니다. 채용과 뉴스 manager가 모두 실행 중인 상태에서 `/health` 200, DB 생성, 실제 socket bind `127.0.0.1`, 정상 종료를 확인했습니다. 운영 DB·JSON과 로그는 사용하지 않았습니다.

같은 날 DB를 사용하지 않는 강화된 기관 공식 목록 read-only smoke는 각 출처의 1·2페이지 URL 집합이 서로 다름을 확인했습니다. 페이지당 10건씩 모두 게시일을 파싱했으며 20건 날짜 범위는 한국부동산원 `2026-07-02..2026-08-20`, 신용보증기금 `2026-06-23..2026-08-25`, 한국가스공사 `2026-05-14..2026-08-21`입니다. 최초 점검에서 세 출처가 모두 날짜 `0/100`과 10페이지 cap에 도달한 원인을 조사해 한국부동산원·신용보증기금의 제목 다음 날짜 열과 한국가스공사의 `YYYY-MM-DD` 형식을 fixture 회귀 테스트로 수정했습니다. 재검증한 30일 crawl은 각각 `13건/2요청`, `9건/1요청`, `3건/1요청`이며 모두 날짜가 있고 cap에 도달하지 않았습니다. 이 smoke는 공식 목록만 열었고 기사 본문·운영 DB·로그를 사용하지 않았습니다.

Task 10 수행 중 E2E가 찾아낸 세 회귀도 테스트로 고정되어 있습니다.

- 넓은 화면 미디어쿼리가 `hidden` 보관함을 다시 표시하던 문제
- 설정 GET이 늦게 완료되면 사용자가 먼저 추가한 키워드를 덮던 문제
- 아주 긴 직무가 줄임 표시되지 않고 카드 lane의 너비를 밀어내던 문제

## 남은 제한

- 외부 접속, 인증, 반복 스케줄 수집, 알림, 별도 작업 큐는 구현하지 않았습니다.
- Naver Cafe와 다섯 공식 뉴스 목록의 네트워크·DOM 변경은 앱이 통제할 수 없습니다. 채용 실패 카테고리는 부분 실패로 남고 수동 재시도가 필요하며, 뉴스 실패는 출처별 오류로 격리됩니다.
- Playwright Chromium이 설치되어 있어야 실제 수집과 브라우저 E2E가 동작합니다.
- `127.0.0.1:8000`을 다른 프로세스가 사용 중이면 실행할 수 없습니다.
- `ggg_startup.fish`는 기존 파일로 남아 있으며 이번 Windows 시작 경로 검증 대상이 아닙니다.

## 구현 커밋

기준 커밋 이후 구현 이력은 다음과 같습니다.

- `bfab73b` typed application foundation
- `769e825`, `2b5a8d6` parsing/filtering and strict deadline parsing
- `08286cb` SQLite schema and safe migration
- `eb52bc4` state-safe repository
- `7c88b49`, `fd7dc84` concurrent crawler and resource cleanup
- `33f0223`, `5bcaaaf` crawl manager and failure-state correction
- `36f81aa`, `fc77325` FastAPI APIs and route/error corrections
- `e77a1a7`, `012e8e1`, `80f370b`, `9e96b1a`, `ffda179` dashboard and interaction corrections
- `4e6dba1`, `1d62e9f` runtime launch, retained logs and logger capture
- Task 10의 `test: verify modernized local workflow` 커밋: E2E, 문서, legacy `test.py` 제거와 E2E 발견 회귀 수정
- `fix: address final modernization review`: 최종 리뷰의 파싱·설정 원자성·재시도 UI·작업 정리·실행 오류·통합 로그·테스트 의존성 수정

## 다음 작업 시 주의

기능 변경 전 `AGENTS.md`, `README.md`, 설계 문서, 이 문서를 순서대로 읽으십시오. 승인된 범위를 바꾸면 설계와 이 문서를 함께 갱신하고, 구현 완료를 주장하기 전에 Python 3.12 전체 테스트와 실제 시작 경로를 다시 검증하십시오.
