from app.config import Settings
from tests.conftest import register


def test_base_url_always_ends_with_exactly_one_slash():
    assert Settings(base_url="https://example.com").base_url == "https://example.com/"
    assert Settings(base_url="https://example.com/").base_url == "https://example.com/"
    assert Settings(base_url="https://example.com//").base_url == "https://example.com/"


def test_verification_email_link_is_well_formed(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.routers.auth.send_email", lambda **kw: sent.append(kw))
    register(client, email="links@example.com")
    assert len(sent) == 1
    assert "http://testserver/verify-email/" in sent[0]["body"]


def test_resend_sends_a_well_formed_link_then_redirects(client, monkeypatch):
    register(client, email="resend@example.com")
    sent = []
    monkeypatch.setattr("app.routers.account.send_email", lambda **kw: sent.append(kw))

    resp = client.post("/account/resend-verification", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/account?verification_sent=1"
    assert len(sent) == 1
    assert "http://testserver/verify-email/" in sent[0]["body"]


def test_favicon_and_link_preview_tags_are_served(client):
    page = client.get("/").text
    assert 'rel="icon" href="/static/favicon.svg"' in page
    assert 'property="og:title"' in page
    assert client.get("/static/favicon.svg").status_code == 200
    legacy = client.get("/favicon.ico", follow_redirects=False)
    assert legacy.status_code == 301
    assert legacy.headers["location"] == "/static/favicon.svg"


def test_account_page_confirms_a_sent_verification_email(client):
    register(client, email="confirm@example.com")
    assert "Verification email sent" in client.get("/account?verification_sent=1").text
    assert "Verification email sent" not in client.get("/account").text
