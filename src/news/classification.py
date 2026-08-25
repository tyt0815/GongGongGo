import re


_REGULAR_STATISTICS_PHRASES = (
    "주간아파트가격동향",
    "전국주택가격동향",
    "상업용부동산 임대동향조사",
)


def classify_institution_title(title: str) -> str:
    normalized = re.sub(r"\s+", " ", title).strip().casefold()
    if any(phrase.casefold() in normalized for phrase in _REGULAR_STATISTICS_PHRASES):
        return "정기 통계"
    return "보도자료"
