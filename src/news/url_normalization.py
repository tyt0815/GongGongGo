from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode


_TRACKING_KEYS = {"fbclid", "gclid", "n_cid"}


def normalize_url(value: str) -> str:
    """Normalize URL identity without changing meaningful query parameters."""
    parsed = urlsplit(value)
    scheme = parsed.scheme.casefold()

    hostname = parsed.hostname
    if hostname is None:
        return urlunsplit((scheme, parsed.netloc, parsed.path, _clean_query(parsed.query), ""))

    host = hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += f":{parsed.password}"
        userinfo += "@"

    try:
        port = parsed.port
    except ValueError:
        port = None
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = f"{userinfo}{host}" if port is None or default_port else f"{userinfo}{host}:{port}"

    path = parsed.path
    if path.endswith("/") and path != "/":
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, _clean_query(parsed.query), ""))


def _clean_query(query: str) -> str:
    if not query:
        return ""
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
    ]
    return urlencode(kept, doseq=True)
