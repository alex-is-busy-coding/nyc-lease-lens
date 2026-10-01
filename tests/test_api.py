import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nyc_lease_lens.api.app import create_app
from nyc_lease_lens.config import Settings
from nyc_lease_lens.tools import build_registry


class FakeAgent:
    """Answers without a model and records the history it was given."""

    system_prompt, model, max_tool_rounds = "test prompt", "fake-model", 1

    def __init__(self, fail: bool = False):
        self.tools = build_registry(None)
        self.histories: list[list[str]] = []
        self.fail = fail

    def run(self, messages):
        if self.fail:
            raise RuntimeError("model unavailable")
        self.histories.append([m["content"] for m in messages])
        return f"echo: {messages[-1]['content']}", [{"name": "lookup_building", "args": {}, "result": "{}"}]


@pytest.fixture
def agent():
    return FakeAgent()


@pytest.fixture
def http(agent):
    with TestClient(create_app(Settings(), agent=agent)) as client:  # `with` runs startup and shutdown
        yield client


STATIC = Path(__file__).parent.parent / "src" / "nyc_lease_lens" / "static"


def test_page_loads_its_stylesheet_and_script(http):
    page = http.get("/").text
    assert '<link rel="stylesheet" href="/static/app.css"' in page
    assert '<script src="/static/app.js" defer></script>' in page
    for asset, kind in [("app.css", "text/css"), ("app.js", "javascript")]:
        response = http.get(f"/static/{asset}")
        assert response.status_code == 200 and kind in response.headers["content-type"]


def test_cdn_scripts_are_pinned_and_integrity_checked():
    page = (STATIC / "index.html").read_text()
    cdn_scripts = re.findall(r"<script\s+src=\"(https://[^\"]+)\"\s+integrity=\"(sha384-[^\"]+)\"", page)
    assert {url.split("/npm/")[1].split("/")[0] for url, _ in cdn_scripts} == {"marked@18.0.14", "dompurify@3.4.16"}
    assert page.count("<script") == 3  # the two libraries and app.js: no inline JavaScript


def test_answers_only_reach_the_page_through_dompurify():
    """Every innerHTML assignment in app.js must be the sanitized Markdown one."""
    script = (STATIC / "app.js").read_text()
    assignments = re.findall(r"\.innerHTML\s*=\s*([^;]+);", script)
    assert assignments and all(a.strip().startswith("DOMPurify.sanitize(") for a in assignments)
    assert '"img"' in script  # images in answers are forbidden


def test_serves_the_chat_page_and_logo(http):
    page = http.get("/")
    assert page.status_code == 200 and "Welcome to NYC Lease Lens" in page.text
    logo = http.get("/static/nyc_lease_lens_logo.jpeg")
    assert logo.status_code == 200 and logo.headers["content-type"] == "image/jpeg"


def test_chat_keeps_history_per_session(http, agent):
    first = http.post("/chat", json={"message": "hello"}).json()
    assert first["response"] == "echo: hello" and first["tool_calls"][0]["name"] == "lookup_building"
    http.post("/chat", json={"message": "again", "session_id": first["session_id"]})
    assert agent.histories[-1] == ["test prompt", "hello", "again"]


def test_clear_starts_the_session_over(http, agent):
    session_id = http.post("/chat", json={"message": "hello"}).json()["session_id"]
    assert http.post(f"/clear?session_id={session_id}").json() == {"status": "ok"}
    http.post("/chat", json={"message": "fresh", "session_id": session_id})
    assert agent.histories[-1] == ["test prompt", "fresh"]


def test_agent_failures_become_a_message_not_a_500():
    with TestClient(create_app(Settings(), agent=FakeAgent(fail=True))) as http:
        response = http.post("/chat", json={"message": "hi"})
    assert response.status_code == 200
    assert response.json()["response"] == "Model call failed: RuntimeError: model unavailable"


@pytest.mark.parametrize(
    ("sent", "kept"),
    [("abc-123", True), ("", False), ("has spaces", False), ("x" * 65, False)],
)
def test_request_ids_are_returned_and_validated(http, sent, kept):
    returned = http.get("/", headers={"X-Request-ID": sent} if sent else {}).headers["x-request-id"]
    assert (returned == sent) is kept
    assert len(returned) <= 64 and " " not in returned


def test_two_apps_do_not_share_sessions():
    agent_a, agent_b = FakeAgent(), FakeAgent()
    with TestClient(create_app(Settings(), agent=agent_a)) as a, TestClient(create_app(Settings(), agent=agent_b)) as b:
        session_id = a.post("/chat", json={"message": "only in a"}).json()["session_id"]
        b.post("/chat", json={"message": "in b", "session_id": session_id})
    assert agent_b.histories[-1] == ["test prompt", "in b"]
