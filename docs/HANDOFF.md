# GongGongGo 인수인계

최종 갱신: 2026-08-20

## 현재 단계

2026-08-03 전체 개선 설계가 구현되었습니다. 앱은 SQLite를 사용하고 서버 준비와 백그라운드 크롤링을 분리하며, 반응형 대시보드와 저장되는 실행·필터 설정을 제공합니다.

승인된 인터페이스는 [전체 개선 설계](superpowers/specs/2026-08-03-gonggonggo-modernization-design.md)에 있습니다. 범위 변경은 없었으므로 `AGENTS.md`의 핵심 제약은 그대로 유효합니다.

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
- `templates/index.html`, `static/app.css`, `static/app.js`: 반응형 대시보드와 설정 UI
- `src/logging_config.py`, `src/runtime.py`: 날짜별 로그, 보존 정리, 선택적 브라우저 열기, 로컬 Uvicorn 실행
- `ggg_startup.vbs`: 숨김 실행, `ggg_startup.bat`: 기존 스케줄러용 VBS 래퍼
- `ggg_debug.bat`: `main.py --debug`으로 콘솔과 Playwright Chromium GUI를 함께 표시하는 디버그 실행
- `tests/`: 파서·DB·저장소·크롤러·관리자·API·UI 계약 테스트
- `tests/e2e/test_dashboard_layout.py`: 임시 DB/JSON, 무네트워크 관리자와 실제 Chromium을 사용하는 loopback E2E

## 중요한 동작과 불변 조건

- 앱은 `127.0.0.1:8000`에만 바인딩합니다.
- lifespan은 저장소를 준비하고 시작 크롤링을 예약하지만 완료를 기다리지 않고 요청을 받습니다.
- 크롤러는 기존 행의 수집 소유 필드만 갱신하며 `status`와 `status_updated_at`을 덮지 않습니다.
- 신규 크롤링 insert는 `is_new=1`로 저장되어 검토 대기 상단에 최신순으로 표시됩니다. 기존 DB 행과 JSON 마이그레이션 행은 `is_new=0`입니다.
- `확인` API는 상태를 유지한 채 `is_new`만 해제하고, 상태 API는 상태 변경과 함께 `is_new`를 해제합니다. 공고 제목 링크를 여는 것만으로는 해제되지 않습니다.
- 사용자 API는 연결을 공유하지 않고 짧은 SQLite 트랜잭션을 사용합니다.
- 전체 수집 중 하나의 카테고리가 실패해도 다른 카테고리 결과를 저장하며, 같은 시점의 중복 실행은 거절합니다.
- JSON 마이그레이션은 정확한 `YYYY.MM.DD` 공고만 한 번 가져오고 원본 파일은 그대로 둡니다.
- 수집 결과 저장 시 오늘보다 마감일이 지난 `dated` 공고를 제거하며 오늘 마감·상시·미확인 공고는 유지합니다. 자동 만료에는 링크 차단을 남기지 않습니다.
- 영구 삭제는 본문을 제거하고 `deleted_links`에 링크 차단을 남깁니다.
- 대상 기관은 직무와 무관하게 표시하고, 일반 기관은 파싱된 직무 키워드가 일치할 때만 표시합니다.
- 설정 drawer는 저장된 값을 모두 읽은 뒤 열어, 로딩 중 사용자 입력이 늦은 응답에 덮이지 않게 합니다.
- 넓은 화면은 현재 선택한 묶음의 두 lane만 표시하고, 좁은 화면은 네 상태 중 한 panel만 표시합니다.
- 일반 및 숨김 실행의 크롤러는 headless 모드이며, `--debug` 실행에서만 실제 Chromium 창을 표시합니다.

## 데이터와 안전한 작업 방법

운영 파일은 `data/job_posts.json`과 런타임 `data/gonggonggo.db`입니다. 테스트는 항상 `tmp_path` 아래에 별도 JSON과 DB를 만들며 Naver에 접근하지 않는 fake를 사용합니다. 운영 상태 변경, 영구 삭제, 차단 해제 테스트에 운영 DB를 사용하지 마십시오.

백업은 서버를 종료한 상태에서 JSON과 DB를 함께 복사합니다. 실행 중에는 WAL sidecar가 존재할 수 있으므로 DB 본체만 복사하지 않습니다. `.gitignore`는 DB, DB sidecar, 로그, `.superpowers/` 작업 산출물을 제외합니다.

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
- 카드 내부 버튼, 구조화·fallback 제목, 긴 직무 tooltip, 설정 drawer, 진행 상태와 한국어 렌더링

2026-08-20 Python 3.12.10 전체 실행 결과는 경고를 오류로 처리한 상태에서 `112 passed, 0 warnings`입니다. Starlette `TestClient`는 개발 의존성 `httpx2`를 사용하며 별도 Node.js 테스트 전제는 없습니다.

안전한 smoke에서는 실제 `create_app`과 Uvicorn을 임시 loopback 포트에서 실행하고, 임시 JSON 3건 마이그레이션, `/health` 200, 백그라운드 진행 `1/4`, 상태 변경과 재시작 설정 유지를 확인했습니다. `ggg_debug.bat`의 고정 포트 `8000`은 기존 구 UI 서버(PID 23684)가 이미 점유하고 `/health`에 404를 반환해 이번 세션에서는 실행하지 않았습니다. 사용자 프로세스를 임의로 종료하지 않았으며, 해당 서버를 정상 종료한 뒤 debug BAT의 운영 데이터 read-only smoke와 로그 생성을 별도로 확인해야 합니다.

Task 10 수행 중 E2E가 찾아낸 세 회귀도 테스트로 고정되어 있습니다.

- 넓은 화면 미디어쿼리가 `hidden` 보관함을 다시 표시하던 문제
- 설정 GET이 늦게 완료되면 사용자가 먼저 추가한 키워드를 덮던 문제
- 아주 긴 직무가 줄임 표시되지 않고 카드 lane의 너비를 밀어내던 문제

## 남은 제한

- 외부 접속, 인증, 반복 스케줄 수집, 알림, 별도 작업 큐는 구현하지 않았습니다.
- Naver Cafe 네트워크와 DOM 변경은 앱이 통제할 수 없습니다. 실패 카테고리는 부분 실패로 남고 수동 재시도가 필요합니다.
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
