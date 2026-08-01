"""템플릿 렌더링 — Jinja2 변수 정상 치환 · seed 10종"""
from __future__ import annotations

import pytest

from app.domain.models import Template
from app.services.seed import DEFAULT_TEMPLATES
from app.services.template_renderer import TemplateRenderError, TemplateRenderer


def test_template_render_substitutes_variables():
    renderer = TemplateRenderer()
    template = Template(
        template_id="t1",
        channel="email",
        subject="{{ name }}님께",
        body="마감은 {{ deadline }} 입니다. 링크: {{ form_link }}",
    )
    subject, body = renderer.render(
        template,
        {
            "name": "이지훈",
            "deadline": "2026-07-31 18:00",
            "form_link": "https://hr.lge.com/form/abc123",
        },
    )
    assert subject == "이지훈님께"
    assert "2026-07-31 18:00" in body
    assert "https://hr.lge.com/form/abc123" in body
    assert "{{" not in body


def test_template_render_missing_variable_raises():
    renderer = TemplateRenderer()
    with pytest.raises(TemplateRenderError) as exc:
        renderer.render_string("{{ name }} / {{ deadline }}", {"name": "이지훈"})
    assert "변수 누락" in str(exc.value)


def test_template_render_syntax_error_raises():
    renderer = TemplateRenderer()
    with pytest.raises(TemplateRenderError):
        renderer.render_string("{% for x in %}", {})


def test_default_filter_allows_optional_variables():
    renderer = TemplateRenderer()
    assert renderer.render_string("{{ x | default('없음') }}", {}) == "없음"


def test_required_variables_listed():
    renderer = TemplateRenderer()
    assert renderer.required_variables("{{ a }}{{ b }}") == ["a", "b"]


def test_seed_inserts_ten_templates(session):
    rows = session.query(Template).all()
    assert len(rows) == 10
    expected = {spec["template_id"] for spec in DEFAULT_TEMPLATES}
    assert {row.template_id for row in rows} == expected
    assert expected == {
        "invite",
        "reminder_l1",
        "reminder_l2",
        "reminder_l3",
        "applicant_invite",
        "applicant_change",
        "applicant_defer",
        "interviewer_confirm",
        "hr_alert_integrity",
        "hr_alert_repair",
    }


def test_seed_is_idempotent(session):
    from app.services.seed import seed_all

    result = seed_all(session)
    assert result == {"templates": 0, "channels": 0}
    assert session.query(Template).count() == 10


def test_seed_overwrite_updates_body(session):
    from app.services.seed import seed_all

    template = session.get(Template, "invite")
    template.body = "변경됨"
    session.commit()

    seed_all(session, overwrite=True)
    assert session.get(Template, "invite").body != "변경됨"


@pytest.mark.parametrize("spec", DEFAULT_TEMPLATES, ids=lambda s: s["template_id"])
def test_every_seed_template_renders(spec):
    """기본 템플릿은 대표 컨텍스트로 모두 렌더링되어야 한다."""
    context = {
        "name": "홍길동",
        "deadline": "2026-07-31 18:00",
        "form_link": "https://hr.lge.com/form/x",
        "day": "2일차",
        "hour": "10시",
        "new_slot": "3일차 14시",
    }
    renderer = TemplateRenderer()
    subject, body = renderer.render(
        Template(
            template_id=spec["template_id"],
            channel=spec["channel"],
            subject=spec["subject"],
            body=spec["body"],
        ),
        context,
    )
    assert body.strip()
    assert "{{" not in body
    if subject:
        assert "{{" not in subject


def test_tracking_pixel_injected_once():
    body = "<html><body><p>본문</p></body></html>"
    once = TemplateRenderer.inject_tracking_pixel(body, "abc-123", "http://testserver")
    twice = TemplateRenderer.inject_tracking_pixel(once, "abc-123", "http://testserver")
    assert once.count("<img") == 1
    assert twice == once
    assert "/api/v1/notify/track/open/abc-123.png" in once
    assert once.endswith("</body></html>")


def test_tracking_pixel_appended_when_no_body_tag():
    out = TemplateRenderer.inject_tracking_pixel("plain", "id1", "http://testserver/")
    assert out.startswith("plain")
    assert "track/open/id1.png" in out
