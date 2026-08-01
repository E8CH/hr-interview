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
#
# 칸 이름은 시각이 아니라 **그날의 몇째 자리인지** 를 가리킨다. 몇 시 몇 분인지는
# 인사가 3단계에서 정하는 면접 진행 조건(시작 시각 · 면접 분 · 쉬는 분)이 정한다.
# 그래서 이름을 "13시" 같은 시각으로 붙이면 실제로는 11시 20분인 칸에 13시라고
# 적히는 거짓말이 생긴다. 자리 번호로 부르고, 시각은 그때그때 계산해서 보여 준다.
#
# 칸이 여덟인 이유: 한 팀이 하루에 보는 인원을 8명(8타임)으로 잡았기 때문이다.
# 규칙2(HARD, 같은 팀 동시간 중복 금지)가 (팀, 요일, 칸) 을 유일하게 만들므로,
# 한 팀이 하루에 놓을 수 있는 자리 수는 곧 이 목록의 길이다.
#
# 점심 시간은 따로 두지 않는다. 여덟 칸은 쉬는 시간만 끼고 죽 이어진 한 덩어리다.
DAYS = ["월", "화", "수", "목", "금"]
HOURS = [f"{n}타임" for n in range(1, 9)]

#: 면접 진행 조건을 안 정했을 때 쓰는 기본값 (3단계 입력칸의 초기값과 같다)
DEFAULT_TIMING = {"start": "09:00", "minutes": 30, "rest": 5}

#: 오전과 오후를 가르는 기준 시각 (분 단위)
NOON_MINUTES = 12 * 60

# 면접관이 하루 중 언제 가능한지 — 현업이 칸을 하나씩 고르기는 번거로워서
# 오전 · 오후 두 덩어리로만 받는다. 고른 덩어리가 그대로 배치 제약이 된다.
BAND_ALL, BAND_AM, BAND_PM, BAND_NONE = "오전·오후", "오전만", "오후만", "어려움"


def _minutes_of(clock, fallback: int = 9 * 60) -> int:
    """'09:00' 같은 시각을 자정부터의 분으로 — 못 읽으면 기본값."""
    try:
        hour, _, minute = str(clock).partition(":")
        return int(hour) * 60 + int(minute or 0)
    except (TypeError, ValueError):
        return fallback


def slot_spans(timing=None) -> list[tuple[int, int]]:
    """칸마다 (시작 분, 끝 분) — 쉬는 시간만 끼고 죽 이어진다."""
    timing = {**DEFAULT_TIMING, **(timing or {})}
    start = _minutes_of(timing.get("start"), _minutes_of(DEFAULT_TIMING["start"]))
    try:
        length = max(1, int(timing.get("minutes") or DEFAULT_TIMING["minutes"]))
    except (TypeError, ValueError):
        length = DEFAULT_TIMING["minutes"]
    try:
        rest = max(0, int(timing.get("rest") if timing.get("rest") is not None
                          else DEFAULT_TIMING["rest"]))
    except (TypeError, ValueError):
        rest = DEFAULT_TIMING["rest"]
    step = length + rest
    return [(start + i * step, start + i * step + length) for i in range(len(HOURS))]


def hour_spans(timing=None) -> dict[str, tuple[int, int]]:
    """칸 이름 → (시작 분, 끝 분)"""
    return dict(zip(HOURS, slot_spans(timing)))


def hour_band(hour: str, timing=None) -> str:
    """이 칸이 오전인지 오후인지 — 정오에 걸치면 '오전·오후' 인 사람만 볼 수 있다."""
    spans = hour_spans(timing)
    if hour not in spans:
        return BAND_ALL
    start, end = spans[hour]
    if end <= NOON_MINUTES:
        return BAND_AM
    if start >= NOON_MINUTES:
        return BAND_PM
    return BAND_ALL


def band_hours(band: str, timing=None) -> list[str]:
    """오전·오후 표기를 실제 칸 목록으로 — 모르는 표기는 하루 종일로 본다.

    정오에 걸치는 칸은 '오전만'·'오후만' 어느 쪽에도 안 들어간다. 11시 55분에
    시작해 12시 25분에 끝나는 칸은 오전만 되는 사람에게도, 오후만 되는 사람에게도
    온전히 맞지 않기 때문이다. 그래서 그 칸은 하루 종일 되는 사람 몫이 된다.
    """
    band = str(band or "").strip()
    if band == BAND_NONE:
        return []
    if band not in (BAND_AM, BAND_PM):
        return list(HOURS)
    return [h for h in HOURS if hour_band(h, timing) == band]


def band_availability(band: str, days=DAYS, timing=None) -> dict[str, list[str]]:
    """고른 덩어리를 요일마다 같은 칸으로 펼친다."""
    hours = band_hours(band, timing)
    return {day: list(hours) for day in days} if hours else {}


def band_of(availability, timing=None) -> str:
    """저장된 가용성이 어느 덩어리인지 되읽는다 — 화면 표시용."""
    hours = {h for day_hours in (availability or {}).values() for h in day_hours}
    if not hours:
        return BAND_NONE
    known = hours & set(HOURS)
    if not known:
        return BAND_ALL          # 옛 이름('09시' 등)만 남은 자료 — 하루 종일로 본다
    if known >= set(HOURS):
        return BAND_ALL
    has_am = bool(known & set(band_hours(BAND_AM, timing)))
    has_pm = bool(known & set(band_hours(BAND_PM, timing)))
    if has_am and has_pm:
        return BAND_ALL
    return BAND_AM if has_am else BAND_PM


def _legacy_hour_of(name) -> int | None:
    """'09시' 처럼 시각으로 붙여 놨던 옛 칸 이름에서 시(hour)만 뽑는다."""
    digits = "".join(ch for ch in str(name) if ch.isdigit())
    if not digits:
        return None
    try:
        hour = int(digits[:2])
    except ValueError:
        return None
    return hour if 0 <= hour <= 23 else None


def normalize_availability(availability, timing=None) -> dict[str, list[str]]:
    """저장해 둔 가용성을 지금 쓰는 자리 이름(1타임…8타임)으로 맞춘다.

    예전에는 칸 이름이 '09시'·'14시' 같은 시각이었다. 그 이름을 그대로 두면
    지금은 어느 칸에도 걸리지 않아 그 사람이 통째로 빠져 버린다. 그렇다고
    시각을 그대로 믿을 수도 없다 — 진행 조건이 바뀌면 09시라고 적힌 칸이
    실제로는 다른 시각이기 때문이다. 그래서 이름이 뜻하던 오전/오후만 살려
    지금 칸으로 옮긴다.
    """
    if not availability:
        return {}
    known = set(HOURS)
    result: dict[str, list[str]] = {}
    for day, hours in availability.items():
        hours = list(hours or [])
        kept = {h for h in hours if h in known}
        legacy = [_legacy_hour_of(h) for h in hours if h not in known]
        legacy = [h for h in legacy if h is not None]
        if legacy:
            has_am = any(h < 12 for h in legacy)
            has_pm = any(h >= 12 for h in legacy)
            if has_am and has_pm:
                kept |= set(HOURS)
            elif has_am:
                kept |= set(band_hours(BAND_AM, timing))
            elif has_pm:
                kept |= set(band_hours(BAND_PM, timing))
        if kept:
            result[day] = [h for h in HOURS if h in kept]
    return result


#: 기본 진행 조건에서의 오전 · 오후 칸 (화면 문구 등 어림잡을 때만 쓴다).
#: 정오에 걸치는 칸이 있으므로 AM_HOURS + PM_HOURS 가 HOURS 와 같지 않을 수 있다.
AM_HOURS = band_hours(BAND_AM)
PM_HOURS = band_hours(BAND_PM)
TIME_BANDS = {
    BAND_ALL: list(HOURS),
    BAND_AM: list(AM_HOURS),
    BAND_PM: list(PM_HOURS),
    BAND_NONE: [],
}

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
