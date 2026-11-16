"""Shared template helpers + module registry (used for isolation: a broken module never breaks the app)."""
from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import WEB_DIR

templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

MODULES: dict[str, dict] = {
    "parcel": {"name": "Parcel Intelligence", "short": "Parcels", "path": "/parcel", "case": "Case 1", "enabled": True, "error": ""},
    "compliance": {"name": "Compliance Check", "short": "Compliance", "path": "/compliance", "case": "Case 2", "enabled": True, "error": ""},
    "pnl": {"name": "FC P&L Monitor", "short": "FC P&L", "path": "/pnl", "case": "Case 3", "enabled": True, "error": ""},
}

STATUS_CLASS = {
    # parcel
    "SUCCESS": "ok", "PARTIAL_EXTRACTION": "warn", "LOW_CONFIDENCE": "warn", "LABEL_UNREADABLE": "bad", "NO_PARCEL": "bad",
    "MULTIPLE_PARCELS": "bad", "PARTIAL_PARCEL": "warn", "LABEL_BLOCKED": "bad", "INVALID_IMAGE": "bad",
    # compliance
    "Matched": "ok", "Name mismatch": "warn", "Amount mismatch": "bad", "Missing": "bad", "Manual review": "review", "Not applicable": "mute",
    # jobs
    "queued": "mute", "processing": "info", "completed": "ok", "completed_with_errors": "warn", "failed": "bad",
    # fc colours
    "Red": "bad", "Yellow": "warn", "Green": "ok", "Blue": "info", "Grey": "mute",
    "critical": "bad", "warning": "warn", "watch": "info",
}


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.setdefault("modules", MODULES)
    ctx.setdefault("active", "")
    ctx.setdefault("error", None)
    ctx["status_class"] = STATUS_CLASS
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def pretty_status(s: str) -> str:
    return {"completed_with_errors": "completed with errors"}.get(s, s.replace("_", " ").lower() if s.isupper() else s)


templates.env.filters["pretty"] = pretty_status
templates.env.filters["inr"] = lambda v: "" if v is None else f"{v:,.0f}"
templates.env.filters["pct"] = lambda v: "" if v is None else f"{v:.1f}%"
