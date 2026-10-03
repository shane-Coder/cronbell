from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.alert_utils import send_discord_alert, send_generic_webhook, send_slack_alert
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.email_utils import send_email
from app.models import User
from app.rate_limit import limiter
from app.security import create_purpose_token, hash_password, verify_password
from app.templating import templates

router = APIRouter(prefix="/account", tags=["account"])
COOKIE_NAME = "access_token"
MAX_WEBHOOK_URL_LENGTH = 500


@router.get("")
def account_home(request: Request, verification_sent: int = 0, user: User = Depends(get_current_user)):
    success = "Verification email sent — check your inbox." if verification_sent else None
    return templates.TemplateResponse(
        "account.html", {"request": request, "user": user, "error": None, "success": success}
    )


@router.post("/display-name")
def update_display_name(
    request: Request,
    display_name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user.display_name = display_name.strip() or None
    db.commit()
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "user": user, "error": None, "success": "Display name updated."},
    )


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(current_password, user.hashed_password):
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "error": "Current password is incorrect.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if len(new_password) < 8:
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "error": "New password must be at least 8 characters.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if len(new_password.encode("utf-8")) > 72:
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "error": "Password must be 72 characters or fewer.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.hashed_password = hash_password(new_password)
    db.commit()
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "user": user, "error": None, "success": "Password updated."},
    )


@router.post("/resend-verification")
@limiter.limit("5/hour")
def resend_verification(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.email_verified:
        token = create_purpose_token(user.email, "verify", 60 * 24)
        verify_url = f"{settings.base_url}verify-email/{token}"
        send_email(
            to=user.email,
            subject="[Cronbell] Verify your email",
            body=f"Confirm this is your email address: {verify_url}\n\nThis link expires in 24 hours.",
        )
    return RedirectResponse(url="/account?verification_sent=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/webhooks")
@limiter.limit("10/minute")
def update_webhooks(
    request: Request,
    slack_webhook_url: str = Form(""),
    discord_webhook_url: str = Form(""),
    generic_webhook_url: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    urls = {
        "slack_webhook_url": slack_webhook_url.strip(),
        "discord_webhook_url": discord_webhook_url.strip(),
        "generic_webhook_url": generic_webhook_url.strip(),
    }
    for value in urls.values():
        if value and not value.startswith("https://"):
            return templates.TemplateResponse(
                "account.html",
                {"request": request, "user": user, "error": "Webhook URLs must start with https://", "success": None},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if len(value) > MAX_WEBHOOK_URL_LENGTH:
            return templates.TemplateResponse(
                "account.html",
                {"request": request, "user": user, "error": "That webhook URL is too long.", "success": None},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    user.slack_webhook_url = urls["slack_webhook_url"]
    user.discord_webhook_url = urls["discord_webhook_url"]
    user.generic_webhook_url = urls["generic_webhook_url"]
    db.commit()
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "user": user, "error": None, "success": "Alert channels updated."},
    )


@router.post("/webhooks/test/{channel}")
@limiter.limit("10/minute")
def test_webhook(
    request: Request,
    channel: str,
    user: User = Depends(get_current_user),
):
    senders = {
        "slack": (user.slack_webhook_url, send_slack_alert, "Cronbell test alert — if you can see this, Slack is wired up correctly."),
        "discord": (user.discord_webhook_url, send_discord_alert, "Cronbell test alert — if you can see this, Discord is wired up correctly."),
    }
    if channel == "generic":
        url = user.generic_webhook_url
        if not url:
            raise HTTPException(status_code=400, detail="Save a generic webhook URL first.")
        send_generic_webhook(url, {"event": "test", "message": "Cronbell test alert"})
    elif channel in senders:
        url, sender, text = senders[channel]
        if not url:
            raise HTTPException(status_code=400, detail=f"Save a {channel} webhook URL first.")
        sender(url, text)
    else:
        raise HTTPException(status_code=404, detail="Unknown channel")

    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "success": f"Test alert sent to {channel} — check the channel for it (each send is best-effort, so no error here doesn't guarantee delivery).",
        },
    )


@router.post("/delete")
def delete_account(
    request: Request,
    current_password: str = Form(...),
    confirm_email: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(current_password, user.hashed_password):
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "error": "Current password is incorrect.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if confirm_email.strip().lower() != user.email.lower():
        return templates.TemplateResponse(
            "account.html",
            {"request": request, "user": user, "error": "Typed email didn't match your account email.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    db.delete(user)
    db.commit()

    response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(COOKIE_NAME)
    return response
