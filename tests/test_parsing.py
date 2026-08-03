from datetime import date

import pytest

from src.domain import DeadlineKind
from src.parsing import filter_roles, parse_deadline, parse_title


def test_parse_structured_title_hides_recruitment_prefix():
    parsed = parse_title("★총20명 [한국가스공사 채용] 정규직 신입 (기계/전산/데이터)")

    assert parsed is not None
    assert parsed.institution == "한국가스공사"
    assert parsed.employment == "정규직"
    assert parsed.career == "신입"
    assert parsed.roles == ("기계", "전산", "데이터")


@pytest.mark.parametrize(
    "title",
    [
        "경기도 공공기관 통합채용 사전공고",
        "[창원경상국립대학교병원]",
        "[계명대학교채용]",
        "★총133명! 경기도 공공기관 통합채용 (~8.14)",
    ],
)
def test_parse_title_returns_none_for_unstructured_text(title):
    assert parse_title(title) is None


def test_parse_deadline_classifies_date_open_and_unknown():
    assert parse_deadline("8/5", date(2026, 8, 3)).value == date(2026, 8, 5)
    assert parse_deadline("채용시마감", date(2026, 8, 3)).kind is DeadlineKind.OPEN
    assert parse_deadline("7.24/7.31", date(2026, 8, 3)).kind is DeadlineKind.UNKNOWN


@pytest.mark.parametrize("raw", ["", "채용시까지"])
def test_parse_deadline_preserves_original_text_for_non_dated_values(raw):
    parsed = parse_deadline(raw, date(2026, 8, 3))

    assert parsed.raw == raw
    assert parsed.value is None


def test_institution_match_keeps_post_but_general_post_shows_only_matching_roles():
    target = parse_title("[한국가스공사 채용] 정규직 신입 (기계/화공)")
    assert filter_roles(target, ("한국가스공사",), ("전산",)) == (True, ("기계", "화공"))

    general = parse_title("[일반기관 채용] 정규직 신입 (행정/전산/회계)")
    assert filter_roles(general, ("한국가스공사",), ("전산",)) == (True, ("전산",))


def test_filter_roles_matches_uppercase_ict_case_insensitively():
    parsed = parse_title("[일반기관 채용] 계약직 경력 (행정/ICT 운영)")

    assert filter_roles(parsed, (), ("ict",)) == (True, ("ICT 운영",))


def test_parse_title_preserves_original_text_without_mutating_it():
    title = "★총20명 [한국가스공사 채용] 정규직 신입 (기계, 전산)"

    parsed = parse_title(title)

    assert title == "★총20명 [한국가스공사 채용] 정규직 신입 (기계, 전산)"
    assert parsed is not None
    assert parsed.roles == ("기계", "전산")
