import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_current_user_optional
from app.models import Monitor, MonitorStatus, StatusEvent, User, utcnow
from app.rate_limit import limiter
from app.templating import templates
from app.timeline import build_uptime_timeline

router = APIRouter(tags=["monitors"])

# Sanity bounds, not arbitrary red tape: below 60s isn't meaningfully
# different from "always late" given how the check cadence works, and a
# multi-year period/grace is almost certainly a typo (an extra zero) rather
# than an intentional setting. Clamped rather than rejected, matching how
# this codebase already treats these two fields.
MIN_PERIOD_SECONDS = 60
MAX_PERIOD_SECONDS = 60 * 60 * 24 * 365
MAX_GRACE_SECONDS = 60 * 60 * 24 * 30
MAX_NAME_LENGTH = 255
MAX_TAGS_LENGTH = 255
MAX_NOTES_LENGTH = 5000


def _clean_name(name: str) -> str:
    name = name.strip()[:MAX_NAME_LENGTH]
    if name:
        return name
    # An empty name is worse than a generic one — this only happens if
    # someone submits the form with the name field cleared entirely.
    return f"monitor-{int(time.time())}"


@router.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    # Logged out: this is the front door — show what Cronbell is before
    # asking anyone to sign in. Logged in: show the actual dashboard.
    if user is None:
        return templates.TemplateResponse("landing.html", {"request": request, "user": None})

    monitors = (
        db.query(Monitor)
        .filter(Monitor.owner_id == user.id)
        .order_by(Monitor.created_at.desc())
        .all()
    )
    # Scan-first overview strip on the dashboard: counts, not prose — see
    # the status-summary component in style.css.
    status_counts = {
        "up": sum(1 for m in monitors if m.status == MonitorStatus.UP),
        "late": sum(1 for m in monitors if m.status == MonitorStatus.LATE),
        "down": sum(1 for m in monitors if m.status == MonitorStatus.DOWN),
        "new": sum(1 for m in monitors if m.status == MonitorStatus.NEW),
        "paused": sum(1 for m in monitors if m.status == MonitorStatus.PAUSED),
    }
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "monitors": monitors,
            "status_counts": status_counts,
        },
    )


@router.post("/monitors")
@limiter.limit("30/minute")
def create_monitor(
    request: Request,
    name: str = Form(...),
    period_seconds: int = Form(86400),
    grace_seconds: int = Form(3600),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    monitor = Monitor(
        owner_id=user.id,
        name=_clean_name(name),
        period_seconds=min(max(period_seconds, MIN_PERIOD_SECONDS), MAX_PERIOD_SECONDS),
        grace_seconds=min(max(grace_seconds, 0), MAX_GRACE_SECONDS),
    )
    db.add(monitor)
    db.flush()  # populate monitor.id / created_at before the timeline's first event
    db.add(StatusEvent(monitor_id=monitor.id, status=MonitorStatus.NEW, changed_at=monitor.created_at))
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@router.get("/monitors/{monitor_id}")
def monitor_detail(
    monitor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    monitor = (
        db.query(Monitor)
        .filter(Monitor.id == monitor_id, Monitor.owner_id == user.id)
        .first()
    )
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")

    recent_pings = sorted(monitor.pings, key=lambda p: p.received_at, reverse=True)[:20]
    timeline = build_uptime_timeline(monitor)
    return templates.TemplateResponse(
        "monitor_detail.html",
        {
            "request": request,
            "user": user,
            "monitor": monitor,
            "recent_pings": recent_pings,
            "timeline": timeline,
        },
    )


@router.get("/monitors/{monitor_id}/edit")
def edit_monitor_form(
    monitor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    monitor = (
        db.query(Monitor)
        .filter(Monitor.id == monitor_id, Monitor.owner_id == user.id)
        .first()
    )
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")

    return templates.TemplateResponse(
        "monitor_edit.html", {"request": request, "user": user, "monitor": monitor}
    )


@router.post("/monitors/{monitor_id}/edit")
@limiter.limit("30/minute")
def edit_monitor(
    request: Request,
    monitor_id: int,
    name: str = Form(...),
    period_seconds: int = Form(...),
    grace_seconds: int = Form(...),
    tags: str = Form(""),
    notes: str = Form(""),
    is_public: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    monitor = (
        db.query(Monitor)
        .filter(Monitor.id == monitor_id, Monitor.owner_id == user.id)
        .first()
    )
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")

    monitor.name = _clean_name(name)
    monitor.period_seconds = min(max(period_seconds, MIN_PERIOD_SECONDS), MAX_PERIOD_SECONDS)
    monitor.grace_seconds = min(max(grace_seconds, 0), MAX_GRACE_SECONDS)
    monitor.tags = tags.strip()[:MAX_TAGS_LENGTH]
    monitor.notes = notes.strip()[:MAX_NOTES_LENGTH]
    monitor.is_public = is_public
    db.commit()
    return RedirectResponse(url=f"/monitors/{monitor_id}", status_code=status.HTTP_302_FOUND)


@router.post("/monitors/{monitor_id}/pause")
@limiter.limit("30/minute")
def pause_monitor(
    request: Request,
    monitor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    monitor = (
        db.query(Monitor)
        .filter(Monitor.id == monitor_id, Monitor.owner_id == user.id)
        .first()
    )
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")

    new_status = MonitorStatus.PAUSED if monitor.status != MonitorStatus.PAUSED else MonitorStatus.NEW
    monitor.status = new_status
    db.add(StatusEvent(monitor_id=monitor.id, status=new_status, changed_at=utcnow()))
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@router.post("/monitors/{monitor_id}/delete")
@limiter.limit("30/minute")
def delete_monitor(
    request: Request,
    monitor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    monitor = (
        db.query(Monitor)
        .filter(Monitor.id == monitor_id, Monitor.owner_id == user.id)
        .first()
    )
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")

    db.delete(monitor)
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
