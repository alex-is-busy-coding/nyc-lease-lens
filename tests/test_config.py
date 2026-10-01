import pytest
from pydantic import ValidationError

from nyc_lease_lens.config import OpenDataSettings, Settings

VARIABLES = [
    "MODEL",
    "VERTEXAI_PROJECT",
    "PORT",
    "HOST",
    "OPENDATA_TIMEOUT",
    "OPENDATA_SLOW_MS",
    "LOG_LEVEL",
    "LOG_FORMAT",
]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """No real .env (run from an empty directory) and none of the developer's variables."""
    monkeypatch.chdir(tmp_path)
    for name in [*VARIABLES, "TIMEOUT", "LEVEL"]:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_defaults():
    s = Settings()
    assert (s.llm.model, s.llm.max_tool_rounds) == ("vertex_ai/gemini-3.5-flash-lite", 8)
    assert (s.server.host, s.server.port, s.opendata.timeout, s.logging.level) == ("127.0.0.1", 8000, 20.0, "INFO")


def test_dotenv_is_read_and_the_environment_wins(isolated_environment, monkeypatch):
    (isolated_environment / ".env").write_text("PORT=9000\nMODEL=from-dotenv\nOPENDATA_TIMEOUT=5\n")
    monkeypatch.setenv("PORT", "9100")
    s = Settings()
    assert (s.server.port, s.llm.model, s.opendata.timeout) == (9100, "from-dotenv", 5.0)


def test_prefixes_keep_groups_apart(monkeypatch):
    monkeypatch.setenv("TIMEOUT", "1")
    monkeypatch.setenv("LEVEL", "ERROR")
    s = Settings()
    assert (s.opendata.timeout, s.logging.level) == (20.0, "INFO")


@pytest.mark.parametrize(
    ("name", "value", "group"),
    [
        ("PORT", "99999", "ServerSettings"),
        ("LOG_LEVEL", "verbose", "LoggingSettings"),
        ("OPENDATA_TIMEOUT", "0", "OpenDataSettings"),
        ("MAX_TOOL_ROUNDS", "0", "LLMSettings"),
    ],
)
def test_invalid_values_are_rejected_and_name_their_group(monkeypatch, name, value, group):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=group):
        Settings()


def test_one_group_can_be_replaced_in_code():
    s = Settings(opendata=OpenDataSettings(timeout=1))
    assert (s.opendata.timeout, s.opendata.retries, s.server.port) == (1.0, 2, 8000)
