"""공통 상수 — 태그, 규칙"""

# 배포 사유 태그
TAGS = {
    "PRIMARY_JOB": "팀 주력 직무 매칭",
    "SECONDARY_JOB": "팀 보조 직무 매칭",
    "PREFERRED_MAJOR": "팀 선호 전공 매칭",
    "ORG_MAIN": "제1기술원 배정",
    "ORG_ALT_QUOTA": "제2사업부 쿼터 배정",
    "TARGET_LAB": "타겟랩 지정 배정",
    "ADVISOR_ROUTE": "지도교수 관계 배정",
    "GRAD_BALANCE": "대학원 비율 조정용",
    "DUPLICATE_REVIEW": "복수 검토 대상",
    "HR_MANUAL": "HR 담당자 재량",
}

# 4대 배치 규칙
RULES = {
    "RULE1_GRAD_BALANCE": "SOFT",   # 학사/대학원 요일 분산
    "RULE2_TEAM_CONFLICT": "HARD",   # 같은 팀 동시간 중복 금지
    "RULE3_VERTICAL_GROUP": "SOFT",  # 세로 연속 배치
    "RULE4_FIRST_SLOT": "SOFT",      # 첫 타임 소규모 조 우선
}

# 요일 · 시간대
DAYS = ["월", "화", "수", "목", "금"]
HOURS = ["09시", "10시", "11시", "14시", "15시", "16시"]

# 서비스 포트
SERVICE_PORTS = {
    "version-manager": 8001,
    "distributor": 8002,
    "response-collector": 8003,
    "scheduler": 8004,
    "repair-engine": 8005,
    "notification-hub": 8006,
    "audit-analytics": 8007,
}
