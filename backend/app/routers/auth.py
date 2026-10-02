from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.email_utils import send_email
from app.models import User, utcnow
from app.rate_limit import limiter
from app.security import (
    create_access_token,
    create_purpose_token,
    decode_purpose_token,
    generate_csrf_token,
    hash_password,
    verify_password,
)
from app.templating import templates

router = APIRouter(tags=["auth"])

COOKIE_NAME = "access_token"
CSRF_COOKIE_NAME = "csrf_token"
RESET_TOKEN_MINUTES = 30
VERIFY_TOKEN_MINUTES = 60 * 24


def _set_auth_cookies(response, email: str) -> None:
    """The two cookies a logged-in session needs: the JWT that proves who
    you are, and a separate random value the CSRF middleware (main.py)
    checks against a hidden form field on every state-changing request.
    httponly on both — the CSRF cookie doesn't need JS to read it, since
    the server embeds its value into forms itself (see csrf_field() in
    templating.py), so there's no reason to expose it to script at all."""
    response.set_cookie(COOKIE_NAME, create_access_token(subject=email), httponly=True, samesite="lax")
    response.set_cookie(CSRF_COOKIE_NAME, generate_csrf_token(), httponly=True, samesite="lax")


def _send_verification_email(email: str) -> None:
    token = create_purpose_token(email, "verify", VERIFY_TOKEN_MINUTES)
    verify_url = f"{settings.base_url}verify-email/{token}"
    send_email(
        to=email,
        subject="[Cronbell] Verify your email",
        body=(
            f"Confirm this is your email address: {verify_url}\n\n"
            f"This link expires in 24 hours. If you didn't create a Cronbell account, "
            "you can ignore this."
        ),
    )


@router.get("/register")
def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None, "sent": False})


@router.post("/register")
@limiter.limit("5/hour")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # Normalized once, here, so every future lookup (login, admin grants,
    # password reset if that's ever added) can compare case-insensitively
    # without remembering to re-normalize each time. Without this,
    # "Foo@x.com" and "foo@x.com" register as two different accounts, and
    # someone who typed their email in a different case at login than at
    # signup gets a confusing "invalid password" instead of logging in.
    email = email.strip().lower()

    if "@" not in email or "." not in email.rsplit("@", 1)[-1] or len(email) > 255:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Enter a valid email address."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "An account with that email already exists."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if len(password) < 8:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Password must be at least 8 characters."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # bcrypt silently ignores anything past 72 bytes rather than erroring —
    # without this check, someone who sets a 100-character password would
    # have their account effectively "protected" by only its first 72
    # bytes, with no indication that's what happened.
    if len(password.encode("utf-8")) > 72:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Password must be 72 characters or fewer."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = User(email=email, hashed_password=hash_password(password), last_login_at=utcnow())
    db.add(user)
    db.commit()

    _send_verification_email(user.email)

    # Logged in immediately (cookie set below) — verification isn't a
    # gate, just a nudge. This screen is only about the sequence: show
    # "check your email" as the direct result of signing up, instead of
    # silently dropping them into the dashboard and hoping they notice a
    # banner. "Continue to dashboard" on this page takes them in either way.
    response = templates.TemplateResponse(
        "register.html", {"request": request, "error": None, "sent": True, "email": user.email}
    )
    _set_auth_cookies(response, user.email)
    return response


@router.get("/login")
def login_form(request: Request, reset: str | None = None):
    success = "Password reset — log in with your new password." if reset == "success" else None
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "success": success})


@router.post("/login")
@limiter.limit("10/minute")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user.last_login_at = utcnow()
    user.inactivity_reminder_stage = 0
    db.commit()

    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    _set_auth_cookies(response, user.email)
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE_NAME)
    return response


@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request, "sent": False})


@router.post("/forgot-password")
@limiter.limit("5/hour")
def forgot_password(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is not None:
        token = create_purpose_token(user.email, "reset", RESET_TOKEN_MINUTES)
        reset_url = f"{settings.base_url}reset-password/{token}"
        send_email(
            to=user.email,
            subject="[Cronbell] Reset your password",
            body=(
                f"Someone (hopefully you) asked to reset the password on this account.\n\n"
                f"Reset it here: {reset_url}\n\n"
                f"This link expires in {RESET_TOKEN_MINUTES} minutes. If you didn't ask for "
                "this, ignore it — your password hasn't changed."
            ),
        )
    # Same response whether or not that email exists — the alternative
    # ("no account with that email") lets anyone check which emails are
    # registered just by trying them here.
    return templates.TemplateResponse("forgot_password.html", {"request": request, "sent": True})


@router.get("/reset-password/{token}")
def reset_password_form(token: str, request: Request):
    email = decode_purpose_token(token, "reset")
    return templates.TemplateResponse(
        "reset_password.html", {"request": request, "token": token, "invalid": email is None, "error": None}
    )


@router.post("/reset-password/{token}")
@limiter.limit("10/hour")
def reset_password(
    token: str,
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = decode_purpose_token(token, "reset")
    if email is None:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "invalid": True, "error": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if len(new_password) < 8:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "invalid": False, "error": "Password must be at least 8 characters."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(new_password.encode("utf-8")) > 72:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "invalid": False, "error": "Password must be 72 characters or fewer."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        # Account was deleted after the link was sent — treat like an
        # invalid link rather than a 500.
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "invalid": True, "error": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.hashed_password = hash_password(new_password)
    db.commit()
    return RedirectResponse(url="/login?reset=success", status_code=status.HTTP_302_FOUND)


@router.get("/verify-email/{token}")
def verify_email(token: str, request: Request, db: Session = Depends(get_db)):
    email = decode_purpose_token(token, "verify")
    success = False
    if email is not None:
        user = db.query(User).filter(User.email == email).first()
        if user is not None:
            user.email_verified = True
            db.commit()
            success = True
    return templates.TemplateResponse("verify_email.html", {"request": request, "success": success})
