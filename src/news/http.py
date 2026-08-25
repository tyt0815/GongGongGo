from urllib.request import Request, urlopen


_MAX_BODY_BYTES = 5 * 1024 * 1024
_TIMEOUT_SECONDS = 15


def fetch_html(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "GongGongGo/1.0 personal-local-news-reader",
            "Accept-Language": "ko-KR,ko;q=0.9",
        },
    )
    with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        status = response.status
        if not 200 <= status < 300:
            raise ValueError(f"unexpected HTTP status: {status}")
        body = response.read(_MAX_BODY_BYTES + 1)

    if len(body) > _MAX_BODY_BYTES:
        raise ValueError("response body exceeds 5 MiB")
    return body
