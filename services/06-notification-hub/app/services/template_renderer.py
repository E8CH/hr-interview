"""Jinja2 템플릿 렌더링 + 트래킹 픽셀 삽입"""
from __future__ import annotations

import re
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError
from jinja2 import UndefinedError

from app.domain.models import Template

PIXEL_PATH = "/api/v1/notify/track/open/{notification_id}.png"
_PIXEL_MARKER = 'data-notif-pixel="1"'


class TemplateRenderError(Exception):
    """템플릿 문법 오류 또는 컨텍스트 변수 누락."""


class TemplateRenderer:
    def __init__(self) -> None:
        # 이메일 본문은 HTML/텍스트 혼용 → autoescape 는 끄고 템플릿 작성자가 책임
        self.env = Environment(undefined=StrictUndefined, autoescape=False)

    def render_string(self, source: str, context: dict[str, Any]) -> str:
        try:
            return self.env.from_string(source).render(**context)
        except UndefinedError as exc:
            raise TemplateRenderError(f"템플릿 변수 누락: {exc.message}") from exc
        except TemplateSyntaxError as exc:
            raise TemplateRenderError(f"템플릿 문법 오류: {exc.message}") from exc

    def render(
        self, template: Template, context: dict[str, Any]
    ) -> tuple[str | None, str]:
        """(subject, body) 반환."""
        subject = (
            self.render_string(template.subject, context) if template.subject else None
        )
        body = self.render_string(template.body, context)
        return subject, body

    def required_variables(self, source: str) -> list[str]:
        """템플릿이 참조하는 최상위 변수 이름 목록."""
        from jinja2 import meta

        ast = self.env.parse(source)
        return sorted(meta.find_undeclared_variables(ast))

    # --- 트래킹 픽셀 ---
    @staticmethod
    def pixel_url(notification_id: str, base_url: str) -> str:
        return base_url.rstrip("/") + PIXEL_PATH.format(notification_id=notification_id)

    @classmethod
    def inject_tracking_pixel(
        cls, body: str, notification_id: str, base_url: str
    ) -> str:
        """이메일 본문 끝에 1x1 픽셀 <img> 를 삽입 (중복 삽입 방지)."""
        if _PIXEL_MARKER in body:
            return body
        url = cls.pixel_url(notification_id, base_url)
        tag = (
            f'<img src="{url}" width="1" height="1" alt="" '
            f'style="display:none" {_PIXEL_MARKER} />'
        )
        if re.search(r"</body\s*>", body, flags=re.IGNORECASE):
            return re.sub(
                r"</body\s*>", tag + "</body>", body, count=1, flags=re.IGNORECASE
            )
        return body + "\n" + tag


_renderer: TemplateRenderer | None = None


def get_renderer() -> TemplateRenderer:
    global _renderer
    if _renderer is None:
        _renderer = TemplateRenderer()
    return _renderer
