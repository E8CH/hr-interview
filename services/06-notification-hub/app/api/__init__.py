"""API 라우터 모음"""
from app.api import channels, events, notify, templates, track

ROUTERS = [notify.router, templates.router, channels.router, track.router, events.router]

__all__ = ["ROUTERS", "channels", "events", "notify", "templates", "track"]
