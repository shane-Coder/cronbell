from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Monitor, User
from app.rate_limit import limiter
from app.templating import templates
from app.timeline import build_uptime_timeline

router = APIRouter(tags=["status_page"])


@router.get("/status/{token}")
@limiter.limit("60/minute")
def public_status_page(token: str, request: Request, db: Session = Depends(get_db)):
    """Fully public, no auth — this is the whole point. Only ever shows
    monitors the owner explicitly opted in (Monitor.is_public), and only
    name/status/uptime. Never the ping URL, tags, or notes — those aren't
    meant for whoever has this link."""
    owner = db.query(User).filter(User.status_page_token == token).first()
    if owner is None:
        # Same 404 either way, matching the pattern everywhere else a
        # token doesn't match — doesn't confirm or deny a token ever
        # existed.
        raise HTTPException(status_code=404, detail="Status page not found")

    monitors = (
        db.query(Monitor)
        .filter(Monitor.owner_id == owner.id, Monitor.is_public.is_(True))
        .order_by(Monitor.name)
        .all()
    )
    monitor_rows = [{"monitor": m, "timeline": build_uptime_timeline(m)} for m in monitors]

    return templates.TemplateResponse(
        "status_page.html",
        {
            "request": request,
            "user": None,
            "owner_name": owner.display_name or "Cronbell",
            "monitor_rows": monitor_rows,
        },
    )
