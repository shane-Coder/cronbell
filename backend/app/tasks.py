from datetime import datetime, timedelta, timezone

from app.alert_utils import send_discord_alert, send_generic_webhook, send_slack_alert
from app.config import settings
from app.database import SessionLocal
from app.email_utils import send_email
from app.models import Monitor, MonitorStatus, StatusEvent, User, as_utc

# These used to be Celery tasks, run by a worker process that stayed up
# 24/7 polling Redis for a beat schedule — most of that machine's cost for
# two functions that together take milliseconds. They're plain functions
# now, called directly from app/routers/internal.py, which an external
# scheduler (GitHub Actions cron) triggers over HTTP. No broker, no
# always-on process — the app only spends CPU on this when there's
# actually a check to run.


def check_overdue_monitors() -> int:
    """Runs on a fixed interval (see celery_app.py beat schedule). Three-tier
    check, matching Healthchecks.io's model rather than a plain up/down:

    - past period, still inside grace  -> LATE (early warning, no alert)
    - past period + grace              -> DOWN (alert fires)

    Only fires the alert email on the transition into DOWN, not into LATE —
    LATE is a heads-up you see on the dashboard, not something worth waking
    anyone up for. Returns the count of monitors that changed state, mainly
    for tests/logging."""
    db = SessionLocal()
    changed = 0
    try:
        now = datetime.now(timezone.utc)
        candidates = (
            db.query(Monitor)
            .filter(Monitor.status.in_([MonitorStatus.UP, MonitorStatus.NEW, MonitorStatus.LATE]))
            .all()
        )
        for monitor in candidates:
            if monitor.last_ping_at is None:
                # Never pinged — baseline off creation instead of a ping.
                created_at = as_utc(monitor.created_at)
                late_at = created_at + timedelta(seconds=monitor.period_seconds)
                down_at = created_at + timedelta(
                    seconds=monitor.period_seconds + monitor.grace_seconds
                )
            else:
                late_at = monitor.late_at
                down_at = monitor.deadline

            if down_at is not None and now > down_at:
                if monitor.status != MonitorStatus.DOWN:
                    monitor.status = MonitorStatus.DOWN
                    db.add(StatusEvent(monitor_id=monitor.id, status=MonitorStatus.DOWN, changed_at=now))
                    changed += 1
                if not monitor.alert_sent:
                    send_down_alerts(monitor)
                    monitor.alert_sent = True
            elif late_at is not None and now > late_at:
                if monitor.status != MonitorStatus.LATE:
                    monitor.status = MonitorStatus.LATE
                    db.add(StatusEvent(monitor_id=monitor.id, status=MonitorStatus.LATE, changed_at=now))
                    changed += 1

        db.commit()
    finally:
        db.close()

    return changed


def check_inactive_accounts() -> dict:
    """Runs once a day. An account is 'active' if it's been logged into OR
    any of its monitors has received a ping recently — a monitor quietly
    doing its job for months without the owner opening the dashboard is the
    intended use case, not inactivity, so logins alone would be the wrong
    signal here. Admin accounts are never touched.

    0 reminders sent -> 60d inactive: first reminder
    1 reminder sent  -> 75d inactive: second/final reminder
    2 reminders sent -> 90d inactive: account deleted (with a courtesy email
                         sent just before, to the still-valid address)

    Any login or ping resets the stage to 0, so becoming active again cancels
    a reminder in progress. Returns counts, mainly for logging."""
    db = SessionLocal()
    counts = {"first_reminder": 0, "second_reminder": 0, "deleted": 0}
    try:
        now = datetime.now(timezone.utc)
        users = db.query(User).all()

        for user in users:
            if user.email.lower() in settings.admin_emails_set:
                continue

            last_activity = as_utc(user.last_login_at or user.created_at)
            for monitor in user.monitors:
                if monitor.last_ping_at and as_utc(monitor.last_ping_at) > last_activity:
                    last_activity = as_utc(monitor.last_ping_at)

            days_inactive = (now - last_activity).days

            if days_inactive >= settings.inactivity_delete_days and user.inactivity_reminder_stage >= 2:
                send_email(
                    to=user.email,
                    subject="[Cronbell] Your account has been deleted due to inactivity",
                    body=(
                        f"Your Cronbell account has had no activity for over "
                        f"{settings.inactivity_delete_days} days (no logins, and no monitors "
                        "receiving pings), so it's been deleted along with its monitors and "
                        "history, as you were told to expect in two earlier reminder emails.\n\n"
                        "If this was a mistake, you're welcome to sign up again any time."
                    ),
                )
                db.delete(user)
                counts["deleted"] += 1
            elif (
                days_inactive >= settings.inactivity_second_reminder_days
                and user.inactivity_reminder_stage == 1
            ):
                remaining = settings.inactivity_delete_days - days_inactive
                send_email(
                    to=user.email,
                    subject="[Cronbell] Final notice: your account will be deleted soon",
                    body=(
                        f"Your Cronbell account has had no activity for {days_inactive} days. "
                        f"If nothing changes, it'll be deleted in about {max(remaining, 1)} days, "
                        "along with its monitors and history.\n\n"
                        "Log in or let one of your monitors receive a ping to cancel this."
                    ),
                )
                user.inactivity_reminder_stage = 2
                counts["second_reminder"] += 1
            elif days_inactive >= settings.inactivity_reminder_days and user.inactivity_reminder_stage == 0:
                send_email(
                    to=user.email,
                    subject="[Cronbell] Still using this account?",
                    body=(
                        f"Your Cronbell account has had no activity for {days_inactive} days — "
                        "no logins, and no monitors receiving pings.\n\n"
                        "No action needed if you're still using it elsewhere or just haven't "
                        "needed to check in. If it stays quiet, we'll send one more reminder "
                        f"before deleting it after {settings.inactivity_delete_days} days total "
                        "of inactivity."
                    ),
                )
                user.inactivity_reminder_stage = 1
                counts["first_reminder"] += 1

        db.commit()
    finally:
        db.close()

    return counts


def send_down_alerts(monitor: Monitor) -> None:
    """Fires every alert channel the owner has configured — email always,
    plus Slack/Discord/generic webhook if they've set one up. Each channel
    is independently best-effort (see alert_utils), so one broken webhook
    URL never blocks the others or the email."""
    owner = monitor.owner
    send_email(
        to=owner.email,
        subject=f"[Cronbell] {monitor.name} is overdue",
        body=(
            f"Monitor \"{monitor.name}\" has not checked in within its expected window.\n\n"
            "This usually means the scheduled job it's watching didn't run, or failed "
            "before it could send its ping.\n\n"
            "Log in to Cronbell to see details."
        ),
    )
    send_slack_alert(
        owner.slack_webhook_url,
        f":red_circle: *{monitor.name}* is overdue — no ping received within its expected window.",
    )
    send_discord_alert(
        owner.discord_webhook_url,
        f"🔴 **{monitor.name}** is overdue — no ping received within its expected window.",
    )
    send_generic_webhook(
        owner.generic_webhook_url,
        {
            "event": "monitor.down",
            "monitor_id": monitor.id,
            "monitor_name": monitor.name,
            "status": "down",
        },
    )
