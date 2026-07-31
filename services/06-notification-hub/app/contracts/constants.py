"""공통 상수 — 태그, 규칙"""

# 배포 사유 태그 — 화면·엑셀에 나갈 때는 반드시 이 우리말로 바꿔 적는다
TAGS = {
    "PRIMARY_JOB": "팀 주력 직무 매칭",
    "SECONDARY_JOB": "팀 보조 직무 매칭",
    "PREFERRED_MAJOR": "팀 선호 전공 매칭",
    "ORG_MAIN": "제1기술원 지원자",
    "ORG_ALT_QUOTA": "받아 주는 조직 쿼터",
    "TARGET_LAB": "타겟랩 출신",
    "ADVISOR_ROUTE": "지도교수 연계",
    "GRAD_BALANCE": "학력 비율 맞추려고 옮김",
    "OVERFLOW_REASSIGN": "정원이 차서 차순위 팀으로",
    "DUPLICATE_REVIEW": "두 팀이 함께 볼 사람",
    "HR_MANUAL": "인사 담당자 재량",
    "TEAM_INHERITED": "1단계에서 팀이 적어 낸 사람",
    "ORG_UNMATCHED": "팀 조건과 안 맞지만 팀이 지목",
}


def tag_label(tag: str) -> str:
    """영어 태그를 우리말로 — 모르는 태그는 그대로 둔다."""
    return TAGS.get(tag, tag)


def tag_text(tags) -> str:
    """태그 목록을 화면에 그대로 쓸 수 있는 한 줄로."""
    return " · ".join(tag_label(str(t)) for t in (tags or []))

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

# 면접관이 하루 중 언제 가능한지 — 현업이 시간을 한 칸씩 고르기는 번거로워서
# 오전 · 오후 두 덩어리로만 받는다. 고른 덩어리가 그대로 배치 제약이 된다.
AM_HOURS = ["09시", "10시", "11시"]
PM_HOURS = ["14시", "15시", "16시"]
BAND_ALL, BAND_AM, BAND_PM, BAND_NONE = "오전·오후", "오전만", "오후만", "어려움"
TIME_BANDS = {
    BAND_ALL: AM_HOURS + PM_HOURS,
    BAND_AM: AM_HOURS,
    BAND_PM: PM_HOURS,
    BAND_NONE: [],
}


def band_hours(band: str) -> list[str]:
    """오전·오후 표기를 실제 시간대 목록으로 — 모르는 표기는 하루 종일로 본다."""
    return list(TIME_BANDS.get(str(band or "").strip(), TIME_BANDS[BAND_ALL]))


def band_availability(band: str, days=DAYS) -> dict[str, list[str]]:
    """고른 덩어리를 요일마다 같은 시간대로 펼친다."""
    hours = band_hours(band)
    return {day: list(hours) for day in days} if hours else {}


def band_of(availability) -> str:
    """저장된 가용성이 어느 덩어리인지 되읽는다 — 화면 표시용."""
    hours = {h for day_hours in (availability or {}).values() for h in day_hours}
    if not hours:
        return BAND_NONE
    has_am, has_pm = bool(hours & set(AM_HOURS)), bool(hours & set(PM_HOURS))
    if has_am and has_pm:
        return BAND_ALL
    return BAND_AM if has_am else BAND_PM

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
