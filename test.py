title = '★총60명 [한국재정정보원 채용] 정규직/계약직/체험형인턴 (행정/국제협력/전산/운영지원/연구지원/운전/회계사/장애)'

def is_target_post(title):
    title_lower = title.lower()

    # 1. 기관/지역 키워드 (대구 및 특정 공사/재단)
    institutions = [
        '대구', '한국가스공사', '신용보증기금', '한국교육학술정보원', '한국뇌연구원', '한국부동산원',
        '한국사학진흥재단', '한국산업기술기획평가원', '한국산업단지공단', '한국지능정보사회진흥원', '한국장학재단'
    ]
    
    # 2. 직무 키워드
    tech_roles = [
        '전산', 'ict', 'it', '디지털', '컴퓨터', '소프트웨어', 'sw', '네트워크', 
        '데이터', '인공지능', 'ai', '머신러닝', '딥러닝', '프로그래밍', '백엔드', 
        '프론트엔드', '풀스택', '클라우드', '서버', 'db', '데이터베이스', '플랫폼', '시스템', '정보'
    ]

    # 키워드 포함 여부 확인 함수
    contains_inst = any(k.lower() in title_lower for k in institutions)
    contains_tech = any(k.lower() in title_lower for k in tech_roles)

    return contains_inst or contains_tech
    
    has_intern = '인턴' in title_lower
    has_newcomer = '신입' in title_lower
    has_regular = '정규직' in title_lower

    # 조건 로직
    # A. 특정 기관이 포함된 경우: '인턴' 또는 '신입'이 있으면 True
    if contains_inst:
        if has_intern or has_newcomer:
            return True

    # B. 그 외 직무 키워드가 포함된 경우: '신입'과 '정규직'이 모두 있어야 True
    if contains_tech:
        if has_newcomer and has_regular:
            return True

    return False

print(is_target_post(title))