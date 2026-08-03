import re
from datetime import date

from src.domain import DeadlineKind, ParsedDeadline, ParsedTitle


_TITLE_PATTERN = re.compile(
    r"^(?:.*?\s+)?\[(?P<institution>[^\]]+)\]\s*"
    r"(?P<details>.*?)\s*\((?P<roles>[^()]*)\)\s*$"
)
_BRACKETED_INSTITUTION_PATTERN = re.compile(r"^[^[]*\[(?P<institution>[^\]]+)\]")
_CAREER_PATTERN = re.compile(r"\b(신입/경력|신입|경력)\b")
_OPEN_DEADLINES = {
    "채용시마감",
    "채용시까지",
    "상시채용",
    "상시모집",
    "수시채용",
}


def parse_title(title: str) -> ParsedTitle | None:
    match = _TITLE_PATTERN.match(title)
    if match is None:
        return None

    institution = _clean_institution(match.group("institution"))
    roles = tuple(
        role.strip()
        for role in re.split(r"[/,]", match.group("roles"))
        if role.strip()
    )
    if not institution or not roles:
        return None

    details = match.group("details").strip()
    career_match = _CAREER_PATTERN.search(details)
    career = career_match.group(1) if career_match else ""
    employment = _CAREER_PATTERN.sub("", details).strip()
    return ParsedTitle(
        institution=institution,
        employment=employment,
        career=career,
        roles=roles,
    )


def extract_institution(title: str) -> str:
    match = _BRACKETED_INSTITUTION_PATTERN.match(title)
    if match is None:
        return ""
    return _clean_institution(match.group("institution"))


def _clean_institution(value: str) -> str:
    return re.sub(r"\s*채용\s*$", "", value).strip()


def parse_deadline(raw: str, today: date) -> ParsedDeadline:
    compact = re.sub(r"\s+", "", raw)
    if compact in _OPEN_DEADLINES:
        return ParsedDeadline(raw=raw, kind=DeadlineKind.OPEN, value=None)

    dated = _parse_dated_deadline(compact.removeprefix("~"), today)
    if dated is not None:
        return ParsedDeadline(raw=raw, kind=DeadlineKind.DATED, value=dated)
    return ParsedDeadline(raw=raw, kind=DeadlineKind.UNKNOWN, value=None)


def _parse_dated_deadline(raw: str, today: date) -> date | None:
    full_match = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", raw)
    if full_match:
        year, month, day = (int(value) for value in full_match.groups())
        return _valid_date(year, month, day)

    month_day_match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})", raw)
    if month_day_match is None:
        return None
    month, day = (int(value) for value in month_day_match.groups())
    candidate = _valid_date(today.year, month, day)
    if candidate is None:
        return None
    if candidate < today:
        return _valid_date(today.year + 1, month, day)
    return candidate


def _valid_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def filter_roles(
    parsed: ParsedTitle | None,
    institution_keywords: tuple[str, ...],
    role_keywords: tuple[str, ...],
) -> tuple[bool, tuple[str, ...]]:
    if parsed is None:
        return False, ()
    institution_match = contains_keyword(parsed.institution, institution_keywords)
    matched = tuple(role for role in parsed.roles if contains_keyword(role, role_keywords))
    if institution_match:
        return True, matched or parsed.roles
    return bool(matched), matched


def contains_keyword(value: str, keywords: tuple[str, ...]) -> bool:
    normalized = value.casefold()
    return any(keyword.casefold() in normalized for keyword in keywords if keyword)
