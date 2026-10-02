from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Monitor, MonitorStatus, User

router = APIRouter(tags=["metrics"])


def _escape_label(value: str) -> str:
    # Prometheus text exposition format: backslash, double-quote, and
    # newline need escaping inside a label value.
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics/{token}")
def metrics(token: str, db: Session = Depends(get_db)):
    """Prometheus-compatible scrape endpoint, scoped to one account via a
    bearer token in the path (same pattern as a monitor's ping URL) — not a
    single public /metrics, since that would leak every user's monitor names
    across the whole instance to anyone who found the URL."""
    user = db.query(User).filter(User.metrics_token == token).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown metrics token")

    monitors = db.query(Monitor).filter(Monitor.owner_id == user.id).order_by(Monitor.id).all()
    now = datetime.now(timezone.utc)

    lines: list[str] = []

    lines.append("# HELP cronbell_monitor_up Whether the monitor is currently up (1) or not (0)")
    lines.append("# TYPE cronbell_monitor_up gauge")
    for m in monitors:
        labels = f'monitor="{_escape_label(m.name)}",monitor_id="{m.id}"'
        lines.append(f"cronbell_monitor_up{{{labels}}} {1 if m.status == MonitorStatus.UP else 0}")

    lines.append("")
    lines.append("# HELP cronbell_monitor_last_ping_timestamp_seconds Unix timestamp of the last received ping")
    lines.append("# TYPE cronbell_monitor_last_ping_timestamp_seconds gauge")
    for m in monitors:
        if m.last_ping_at is None:
            continue
        labels = f'monitor="{_escape_label(m.name)}",monitor_id="{m.id}"'
        lines.append(f"cronbell_monitor_last_ping_timestamp_seconds{{{labels}}} {m.last_ping_at.timestamp():.0f}")

    lines.append("")
    lines.append("# HELP cronbell_monitor_seconds_since_last_ping Seconds since the last received ping")
    lines.append("# TYPE cronbell_monitor_seconds_since_last_ping gauge")
    for m in monitors:
        if m.last_ping_at is None:
            continue
        labels = f'monitor="{_escape_label(m.name)}",monitor_id="{m.id}"'
        seconds = (now - m.last_ping_at).total_seconds()
        lines.append(f"cronbell_monitor_seconds_since_last_ping{{{labels}}} {seconds:.0f}")

    lines.append("")
    lines.append("# HELP cronbell_monitor_period_seconds Expected interval between pings, in seconds")
    lines.append("# TYPE cronbell_monitor_period_seconds gauge")
    for m in monitors:
        labels = f'monitor="{_escape_label(m.name)}",monitor_id="{m.id}"'
        lines.append(f"cronbell_monitor_period_seconds{{{labels}}} {m.period_seconds}")

    lines.append("")
    lines.append("# HELP cronbell_monitors_total Number of monitors in this account, by status")
    lines.append("# TYPE cronbell_monitors_total gauge")
    counts: dict[str, int] = {}
    for m in monitors:
        counts[m.status.value] = counts.get(m.status.value, 0) + 1
    for status_name in [s.value for s in MonitorStatus]:
        lines.append(f'cronbell_monitors_total{{status="{status_name}"}} {counts.get(status_name, 0)}')

    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
