"""기본 템플릿 10종 · 채널 seed 데이터

명세 06 "기본 템플릿 세트 (초기 삽입)" 를 그대로 구현한다.
필수 변수는 StrictUndefined 로 강제하고, 선택 변수만 default 필터를 쓴다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Channel, Template, utcnow

DEFAULT_TEMPLATES: list[dict] = [
    {
        "template_id": "invite",
        "channel": "email",
        "subject": "[LG전자 채용] {{ round_id | default('신규') }} 면접위원 가능시간 회신 요청",
        "body": (
            "<p>{{ name }} 위원님, 안녕하세요.</p>"
            "<p>이번 회차 면접 진행을 위한 <b>가능 시간</b> 회신을 요청드립니다.</p>"
            "<ul>"
            "<li>회신 마감: <b>{{ deadline }}</b></li>"
            "<li>회신 링크: <a href=\"{{ form_link }}\">{{ form_link }}</a></li>"
            "</ul>"
            "<p>감사합니다.<br/>HR 채용팀 드림</p>"
        ),
    },
    {
        "template_id": "reminder_l1",
        "channel": "email",
        "subject": "[리마인더] {{ name }} 위원님, 가능시간 회신 부탁드립니다",
        "body": (
            "<p>{{ name }} 위원님, 안녕하세요.</p>"
            "<p>바쁘신 중 번거롭게 해드려 죄송합니다. 아직 회신이 확인되지 않아 "
            "정중히 리마인드 드립니다.</p>"
            "<p>회신 마감: <b>{{ deadline }}</b><br/>"
            "회신 링크: <a href=\"{{ form_link }}\">{{ form_link }}</a></p>"
            "<p>감사합니다.<br/>HR 채용팀 드림</p>"
        ),
    },
    {
        "template_id": "reminder_l2",
        "channel": "email",
        "subject": "[마감임박] {{ name }} 위원님 회신 마감 {{ deadline }}",
        "body": (
            "<p>{{ name }} 위원님,</p>"
            "<p><b>회신 마감이 임박했습니다.</b> 마감 시각 이후에는 자동 배정이 진행되어 "
            "희망 시간 반영이 어려울 수 있습니다.</p>"
            "<p>마감: <b>{{ deadline }}</b><br/>"
            "회신 링크: <a href=\"{{ form_link }}\">{{ form_link }}</a></p>"
            "<p>HR 채용팀 드림</p>"
        ),
    },
    {
        "template_id": "reminder_l3",
        "channel": "email",
        "subject": "[최종알림] {{ name }} 위원님 회신 미확인 (상급자 참조)",
        "body": (
            "<p>{{ name }} 위원님,</p>"
            "<p>최종 알림입니다. 금일 <b>{{ deadline }}</b> 까지 회신이 없을 경우 "
            "잔여 슬롯으로 자동 배정됩니다.</p>"
            "<p>본 메일은 조직 일정 확정을 위해 "
            "{{ supervisor | default('상급자') }}님을 참조로 발송되었습니다.</p>"
            "<p>회신 링크: <a href=\"{{ form_link }}\">{{ form_link }}</a></p>"
            "<p>HR 채용팀 드림</p>"
        ),
    },
    {
        "template_id": "applicant_invite",
        "channel": "email",
        "subject": "[LG전자] {{ name }}님 면접 일정 안내",
        "body": (
            "<p>{{ name }}님, 안녕하세요. LG전자 채용팀입니다.</p>"
            "<p>면접 일정을 아래와 같이 안내드립니다.</p>"
            "<ul>"
            "<li>일시: <b>{{ day }} {{ hour }}</b></li>"
            "<li>방식: {{ method | default('Webex 화상면접') }}</li>"
            "<li>접속 링크: {{ link | default('추후 개별 안내') }}</li>"
            "</ul>"
            "<p>면접 10분 전까지 접속 부탁드립니다.</p>"
            "<p>LG전자 채용팀</p>"
        ),
    },
    {
        "template_id": "applicant_change",
        "channel": "email",
        "subject": "[LG전자] {{ name }}님 면접 일정 변경 안내",
        "body": (
            "<p>{{ name }}님, 안녕하세요.</p>"
            "<p>부득이한 사유로 면접 일정이 <b>변경</b>되었습니다. 불편을 드려 죄송합니다.</p>"
            "<ul>"
            "<li>변경 전: {{ old_slot | default('기존 일정') }}</li>"
            "<li>변경 후: <b>{{ new_slot }}</b></li>"
            "</ul>"
            "<p>문의: {{ contact | default('hr-team@lge.com') }}</p>"
            "<p>LG전자 채용팀</p>"
        ),
    },
    {
        "template_id": "applicant_defer",
        "channel": "email",
        "subject": "[LG전자] {{ name }}님 면접 회차 이월 안내",
        "body": (
            "<p>{{ name }}님, 안녕하세요.</p>"
            "<p>이번 회차 일정 조정이 어려워 <b>{{ next_round | default('다음 회차') }}</b> 로 "
            "면접이 이월되었습니다.</p>"
            "<p>확정 일정은 별도 안내드리겠습니다. 양해 부탁드립니다.</p>"
            "<p>LG전자 채용팀</p>"
        ),
    },
    {
        "template_id": "interviewer_confirm",
        "channel": "email",
        "subject": "[확정] {{ name }} 위원님 면접 스케줄 확정 안내",
        "body": (
            "<p>{{ name }} 위원님,</p>"
            "<p>면접 스케줄이 <b>확정</b>되었습니다.</p>"
            "<p>배정 건수: {{ assignment_count | default('별첨 참조') }}<br/>"
            "상세 시간표: {{ schedule_link | default('HR 포털 참조') }}</p>"
            "<p>협조해 주셔서 감사합니다.<br/>HR 채용팀 드림</p>"
        ),
    },
    {
        "template_id": "hr_alert_integrity",
        "channel": "slack",
        "subject": "[경고] 데이터 무결성 위반 감지",
        "body": (
            ":rotating_light: *무결성 위반 감지*\n"
            "• 회차: {{ round_id | default('N/A') }}\n"
            "• 상태: {{ status | default('VIOLATED') }}\n"
            "• 중복 배포: {{ duplicate_count | default(0) }}건\n"
            "• 미배포: {{ undistributed_count | default(0) }}건\n"
            "즉시 확인이 필요합니다."
        ),
    },
    {
        "template_id": "hr_alert_repair",
        "channel": "slack",
        "subject": "[알림] 재편성 실행 결과",
        "body": (
            ":wrench: *재편성(Repair) 실행*\n"
            "• 회차: {{ round_id | default('N/A') }}\n"
            "• 플랜: {{ plan_type | default('N/A') }}\n"
            "• 재배정: {{ rebooked | default(0) }}건\n"
            "• 이월: {{ deferred | default(0) }}건"
        ),
    },
]

DEFAULT_CHANNELS: list[dict] = [
    {
        "channel_id": "gmail_smtp",
        "channel_type": "email",
        "config": {"adapter": "smtp"},
        "enabled": True,
    },
    {
        "channel_id": "sendgrid",
        "channel_type": "email",
        "config": {"adapter": "sendgrid", "api_key": ""},
        "enabled": False,
    },
    {
        "channel_id": "slack_hr",
        "channel_type": "slack",
        "config": {"adapter": "slack", "webhook_url": "https://hooks.slack.example/hr"},
        "enabled": True,
    },
    {
        "channel_id": "sms_gateway",
        "channel_type": "sms",
        "config": {"adapter": "sms"},
        "enabled": True,
    },
]


def seed_templates(session: Session, *, overwrite: bool = False) -> int:
    """기본 템플릿을 삽입한다. 기존 항목은 overwrite=True 일 때만 갱신."""
    inserted = 0
    for spec in DEFAULT_TEMPLATES:
        existing = session.get(Template, spec["template_id"])
        if existing is None:
            session.add(Template(**spec))
            inserted += 1
        elif overwrite:
            existing.channel = spec["channel"]
            existing.subject = spec["subject"]
            existing.body = spec["body"]
            existing.updated_at = utcnow()
    session.flush()
    return inserted


def seed_channels(session: Session) -> int:
    inserted = 0
    for spec in DEFAULT_CHANNELS:
        if session.get(Channel, spec["channel_id"]) is None:
            session.add(Channel(**spec))
            inserted += 1
    session.flush()
    return inserted


def seed_all(session: Session, *, overwrite: bool = False) -> dict[str, int]:
    result = {
        "templates": seed_templates(session, overwrite=overwrite),
        "channels": seed_channels(session),
    }
    session.commit()
    return result


def template_count(session: Session) -> int:
    return len(session.execute(select(Template.template_id)).scalars().all())
