import hmac
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import Base, engine
from app.maintenance import MAINTENANCE_HTML
from app.rate_limit import limiter
from app.routers import account, admin, auth, internal, metrics, monitors, pages, ping, status_page

# Paths a POST can hit before a session (and therefore a csrf_token cookie)
# exists yet, or that don't use cookie auth at all — nothing here has an
# authenticated action to hijack, so there's nothing for CSRF to protect.
# Prefix match, not exact, so /reset-password/<token> matches regardless
# of the token in the path.
CSRF_EXEMPT_PREFIXES = ("/register", "/login", "/forgot-password", "/reset-password", "/ping", "/internal")

# docs_url/redoc_url disabled: FastAPI's built-in interactive API docs default
# to "/docs" too, which silently wins the route over our own docs page since
# it's registered before app.include_router() runs. We don't expose a public
# API surface here, so there's nothing worth keeping Swagger UI around for.
app = FastAPI(title="Cronbell", docs_url=None, redoc_url=None)

app.state.limiter = limiter

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(account.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(internal.router)
app.include_router(metrics.router)
app.include_router(monitors.router)
app.include_router(pages.router)
app.include_router(ping.router)
app.include_router(status_page.router)


@app.middleware("http")
async def maintenance_mode_middleware(request: Request, call_next):
    # /healthz stays live either way — Fly's health check hitting this
    # shouldn't depend on maintenance mode, and it never touches the DB.
    if settings.maintenance_mode and request.url.path != "/healthz":
        return HTMLResponse(MAINTENANCE_HTML, status_code=503)
    return await call_next(request)


@app.middleware("http")
async def csrf_protect_middleware(request: Request, call_next):
    """Double-submit cookie CSRF check. Cookie-based JWT auth means a
    logged-in visitor's browser will happily attach the access_token
    cookie to a form an *attacker's* page submits on their behalf —
    SameSite=Lax on that cookie already blocks this for a cross-site POST,
    but this is real defense in depth rather than relying on one browser
    behavior alone. Only enforced when there's actually a session to
    protect (an access_token cookie present) and the path isn't one of the
    pre-auth/non-cookie endpoints in CSRF_EXEMPT_PREFIXES."""
    path = request.url.path
    if (
        request.method == "POST"
        and request.cookies.get("access_token")
        and not path.startswith(CSRF_EXEMPT_PREFIXES)
    ):
        cookie_token = request.cookies.get("csrf_token")
        # request.body() specifically, not request.form() — Starlette's
        # BaseHTTPMiddleware only replays the body to the downstream route
        # handler when it was read via .body() (which it caches); .form()
        # reads through .stream() internally, which Starlette deliberately
        # replays as empty afterward "so downstream things don't hang
        # forever". Every form on this app is plain application/x-www-form
        # -urlencoded (no file uploads anywhere), so parsing it by hand
        # here is safe and keeps the real body intact for the route below.
        body = await request.body()
        submitted_token = parse_qs(body.decode("utf-8")).get("csrf_token", [None])[0]
        if (
            not cookie_token
            or not submitted_token
            or not isinstance(submitted_token, str)
            or not hmac.compare_digest(cookie_token, submitted_token)
        ):
            return PlainTextResponse("Security check failed — please refresh the page and try again.", status_code=403)
    return await call_next(request)


@app.on_event("startup")
def on_startup() -> None:
    if settings.maintenance_mode:
        # Nothing downstream runs in maintenance mode (the middleware above
        # returns before any route or DB session is reached), so there's no
        # reason to require Postgres be reachable just to boot.
        return
    # v1: no migrations yet, just create tables if missing. Swap for Alembic
    # once the schema needs to evolve under real user data.
    Base.metadata.create_all(bind=engine)


@app.exception_handler(HTTPException)
async def auth_redirect_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="/login")
    return await http_exception_handler(request, exc)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return PlainTextResponse(
        "Too many attempts — please wait a bit and try again.",
        status_code=429,
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
