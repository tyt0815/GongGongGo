# 개인용 Windows·Debian 배포

결정:

- 한 사용자가 쓰는 단일 FastAPI 프로세스를 Windows 직접 실행 또는 Debian Docker Compose로 운영한다. 인터넷 공개와 앱 자체 인증은 범위 밖이다.
- Windows 기본 바인딩은 `127.0.0.1:8000`이다. Docker 내부는 `0.0.0.0:8000`, 호스트 포트는 현재 Compose에 지정된 Tailscale IP로 공개한다. 접속은 tailnet 접근 정책을 따른다.
- 운영 DB와 로그는 호스트의 `data/`, `logs/`에 두고 컨테이너에 연결한다. 이미지에 운영 데이터와 백업을 넣지 않는다. Docker 시간대는 `Asia/Seoul`이다.
- 일반 크롤링은 headless이며 Windows `--debug` 실행에서만 Chromium 창을 표시한다. 브라우저 자동 열기는 기본 꺼짐이며 `/health` 성공 후 실행한다.
- 콘솔과 날짜별 UTF-8 파일에 로그를 남기며 시작 시 14일 초과 로그를 정리한다.

이유: 개인용 운영을 단순하게 유지하고, 컨테이너 교체 때 데이터가 보존되며, 원격 사용 범위를 개인 네트워크로 제한하기 위해서다. Docker 바인딩은 현재 `compose.yaml`을 기준으로 이관했으며 과거의 로컬 전용·Serve 필수 안내를 대체한다.
