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
    # 부서가 보낸 자리를 최종 시간표가 어떻게 물려받았는지 — 자리가 옮겨졌을 때
    # 왜 옮겼는지까지 남겨야 부서가 결과를 납득할 수 있다.
    "DEPT_SEAT": "부서가 잡아 둔 자리 그대로",
    "SEAT_MOVED_TAKEN": "부서 자리가 이미 차서 옮김",
    "SEAT_MOVED_BAND": "부서 자리가 담당자 가능 시간 밖이라 옮김",
    "SEAT_MOVED_CAP": "담당자 하루 한도가 차서 옮김",
    "SEAT_MOVED_BUSY": "담당자가 그 시각에 다른 면접이 있어 옮김",
    "SEAT_MOVED_OWNER": "그 자리 담당자를 찾을 수 없어 옮김",
    "SEAT_MOVED_DAY": "부서가 적은 일차가 이 팀 면접 요일 수보다 커서 옮김",
    # 두 팀이 같이 보는 사람이 같은 시각에 두 곳에 잡혔을 때, 그 사람만 옮긴 자리
    "DUP_FIXED": "두 팀 면접이 같은 시각이라 옮김",
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

# 담당자가 하루 중 언제 있어 줄 수 있는지 — 칸을 하나씩 고르기는 번거로워서
# 두 덩어리로만 받는다. 고른 덩어리가 그대로 배치 제약이 된다.
#
# 예전에는 오전 · 오후로 **갈라서** 받았다. 그러면 정오에 걸치는 칸(기본 조건에서
# 11시 55분 ~ 12시 25분)을 어느 쪽도 맡을 수 없어 점심때가 통째로 빈다.
# 그래서 두 덩어리가 **겹치게** 둔다 — 앞타임은 14시까지, 뒤타임은 12시부터.
# 12시 ~ 14시 사이의 칸은 양쪽 다 맡을 수 있으므로 빈 칸이 생기지 않는다.
FRONT_END_MINUTES = 14 * 60    # 앞타임 — 아침부터 이 시각까지 있어 준다
BACK_START_MINUTES = 12 * 60   # 뒤타임 — 이 시각부터 와서 끝까지 있어 준다

BAND_ALL, BAND_FRONT, BAND_BACK, BAND_NONE = "둘 다", "앞타임", "뒤타임", "어려움"

#: 예전 표기 → 지금 표기. 저장해 둔 자료와 현업이 적어 낸 엑셀에 남아 있다.
LEGACY_BANDS = {
    "오전·오후": BAND_ALL, "오전 · 오후": BAND_ALL, "오전오후": BAND_ALL,
    "오전만": BAND_FRONT, "오후만": BAND_BACK,
}


def band_name(band) -> str:
    """예전 표기(오전만 · 오후만 · 오전·오후)를 지금 이름으로 맞춘다."""
    text = str(band or "").strip()
    return LEGACY_BANDS.get(text, text)


def _minutes_of(clock, fallback: int = 9 * 60) -> int:
    """'09:00' 같은 시각을 자정부터의 분으로 — 못 읽으면 기본값."""
    try:
        hour, _, minute = str(clock).partition(":")
        return int(hour) * 60 + int(minute or 0)
    except (TypeError, ValueError):
        return fallback


def slot_spans(timing=None, count: int | None = None) -> list[tuple[int, int]]:
    """칸마다 (시작 분, 끝 분) — 쉬는 시간만 끼고 죽 이어진다.

    칸 수는 기본이 계약의 `HOURS` 길이지만, 부서 화면처럼 하루 칸 수를 직접
    바꿔 보는 자리도 있어서 `count` 로 따로 줄 수 있다.
    """
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
    if count is None:
        count = len(HOURS)
    return [(start + i * step, start + i * step + length) for i in range(max(0, count))]


def hour_spans(timing=None) -> dict[str, tuple[int, int]]:
    """칸 이름 → (시작 분, 끝 분)"""
    return dict(zip(HOURS, slot_spans(timing)))


def band_slots(band, timing=None, count: int | None = None) -> set[int]:
    """그 덩어리가 맡을 수 있는 자리 번호(0부터) — 칸 수를 직접 줄 수 있다."""
    band = band_name(band)
    if band == BAND_NONE:
        return set()
    spans = slot_spans(timing, count)
    if band not in (BAND_FRONT, BAND_BACK):
        return set(range(len(spans)))
    if band == BAND_FRONT:
        return {i for i, (_s, end) in enumerate(spans) if end <= FRONT_END_MINUTES}
    return {i for i, (start, _e) in enumerate(spans) if start >= BACK_START_MINUTES}


def hour_bands(hour: str, timing=None) -> list[str]:
    """이 칸을 맡을 수 있는 덩어리들 — 겹치는 시간대의 칸은 둘 다 나온다.

    둘 다 안 나오는 칸(앞타임이 끝난 뒤에 시작해서 뒤타임이 오기 전에 끝나는
    칸)은 '둘 다' 라고 답한 사람만 맡을 수 있다. 앞뒤가 겹쳐 있으므로 보통
    조건에서는 생기지 않는다.
    """
    spans = hour_spans(timing)
    if hour not in spans:
        return [BAND_FRONT, BAND_BACK]
    start, end = spans[hour]
    out = []
    if end <= FRONT_END_MINUTES:
        out.append(BAND_FRONT)
    if start >= BACK_START_MINUTES:
        out.append(BAND_BACK)
    return out


def band_hours(band: str, timing=None) -> list[str]:
    """앞타임 · 뒤타임 표기를 실제 칸 목록으로 — 모르는 표기는 하루 종일로 본다."""
    band = band_name(band)
    if band == BAND_NONE:
        return []
    if band not in (BAND_FRONT, BAND_BACK):
        return list(HOURS)
    picked = band_slots(band, timing)
    return [hour for index, hour in enumerate(HOURS) if index in picked]


def band_availability(band: str, days=DAYS, timing=None) -> dict[str, list[str]]:
    """고른 덩어리를 요일마다 같은 칸으로 펼친다."""
    hours = band_hours(band, timing)
    return {day: list(hours) for day in days} if hours else {}


def band_of(availability, timing=None) -> str:
    """저장된 가용성이 어느 덩어리인지 되읽는다 — 화면 표시용.

    앞타임과 뒤타임이 겹치므로 한 칸만 보고는 못 가른다. 고른 칸 전체가 어느
    덩어리 안에 들어가는지로 판단하고, 어느 쪽에도 안 들어가면 '둘 다' 다.
    """
    hours = {h for day_hours in (availability or {}).values() for h in day_hours}
    if not hours:
        return BAND_NONE
    known = hours & set(HOURS)
    if not known:
        return BAND_ALL          # 옛 이름('09시' 등)만 남은 자료 — 하루 종일로 본다
    if known >= set(HOURS):
        return BAND_ALL
    front = set(band_hours(BAND_FRONT, timing))
    back = set(band_hours(BAND_BACK, timing))
    if known == front:
        return BAND_FRONT
    if known == back:
        return BAND_BACK
    if known <= front:
        return BAND_FRONT
    if known <= back:
        return BAND_BACK
    return BAND_ALL


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
                kept |= set(band_hours(BAND_FRONT, timing))
            elif has_pm:
                kept |= set(band_hours(BAND_BACK, timing))
        if kept:
            result[day] = [h for h in HOURS if h in kept]
    return result


#: 한 팀을 몰아 보는 날 수 — 인사 화면과 스케줄러가 같은 값을 써야 한다
DAYS_PER_TEAM = 3


def plan_team_days(sizes_by_team, days_per_team: int = DAYS_PER_TEAM,
                   slots_per_day: int | None = None) -> dict[str, list[str]]:
    """팀마다 어느 요일에 면접을 볼지 정한다 — 큰 팀부터 한산한 요일로.

    이 함수가 여기 있는 까닭은 **같은 답이 두 곳에서 필요해서** 다. 부서 화면은
    '1일차 · 2일차' 로만 자리를 잡는데, 그 일차가 무슨 요일인지는 이 함수가
    정한다. 예전에는 인사팀 시간표를 만드는 순간에야 이 계산을 해서, 부서가
    담당자를 몇 명 빼면 팀 인원이 달라지고 그러면 요일까지 통째로 바뀌었다 —
    같은 자료로 두 번 만들면 결과가 달라 보이던 원인이다. 이제는 인사가 명단을
    보내는 순간 한 번 정해서 그대로 물려준다.
    """
    slots_per_day = slots_per_day or len(HOURS)
    day_load: dict[str, int] = {day: 0 for day in DAYS}
    result: dict[str, list[str]] = {}
    for team, size in sorted((sizes_by_team or {}).items(),
                             key=lambda kv: (-int(kv[1] or 0), str(kv[0]))):
        chosen = sorted(DAYS, key=lambda d: (day_load[d], DAYS.index(d)))[:days_per_team]
        chosen.sort(key=DAYS.index)
        per_day = (min(slots_per_day, -(-int(size or 0) // days_per_team))
                   if days_per_team else 0)
        for day in chosen:
            day_load[day] += per_day
        result[str(team)] = chosen
    return result


#: 기본 진행 조건에서의 앞타임 · 뒤타임 칸 (화면 문구 등 어림잡을 때만 쓴다).
#: 두 덩어리는 겹치므로 FRONT_HOURS 와 BACK_HOURS 에 같은 칸이 함께 들어간다.
FRONT_HOURS = band_hours(BAND_FRONT)
BACK_HOURS = band_hours(BAND_BACK)
TIME_BANDS = {
    BAND_ALL: list(HOURS),
    BAND_FRONT: list(FRONT_HOURS),
    BAND_BACK: list(BACK_HOURS),
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
