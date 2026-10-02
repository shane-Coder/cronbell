from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://pulsecheck:pulsecheck@db:5432/pulsecheck"
    redis_url: str = "redis://redis:6379/0"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        # Managed Postgres providers (Fly, Railway, Render, Heroku-style) hand out
        # "postgres://..." or plain "postgresql://..." — SQLAlchemy needs the
        # psycopg2 dialect prefix to pick the right driver.
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+psycopg2://", 1)
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080

    base_url: str = "http://localhost:8000"
    environment: str = "development"

    # When true, every request (except /healthz) gets a static "under
    # maintenance" page instead of the real app, and startup skips touching
    # Postgres entirely — lets the web machine serve something honest and
    # fast off a live demo URL while Postgres/the rest of the stack are
    # deliberately stopped, instead of a 502 after Fly tries and fails to
    # reach a database that isn't running.
    maintenance_mode: bool = False

    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "alerts@cronbell.local"
    smtp_use_tls: bool = True

    overdue_check_interval_seconds: int = 60

    # Shared secret for the /internal/run-*-check endpoints. These replace
    # the old always-on Celery worker: an external scheduler (GitHub Actions
    # cron) hits them over HTTP instead of a process sitting idle 24/7
    # polling a Redis broker. Anyone with this token can trigger a check
    # early, which is harmless (the checks are idempotent), so this is about
    # keeping the endpoint off random internet scanners, not real access
    # control.
    internal_cron_token: str = ""

    # Comma-separated list of emails that get admin access (/admin). No DB
    # role table for this — it's a single-owner tool right now, and an env
    # var is simpler than a migration + role-management UI for one person.
    admin_emails: str = ""

    @property
    def admin_emails_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    # Inactivity cleanup: reminder at N days of no activity, a second
    # reminder later, then deletion if it's still quiet after that.
    # "Activity" = login OR any of the user's monitors receiving a ping —
    # see User.last_login_at for why pings count too.
    inactivity_reminder_days: int = 60
    inactivity_second_reminder_days: int = 75
    inactivity_delete_days: int = 90
    inactivity_check_interval_seconds: int = 86400  # once a day is plenty


settings = Settings()
