import uuid

from nyc_lease_lens.agent import SYSTEM_PROMPT

_sessions: dict[str, list[dict]] = {}


def get_or_create(session_id: str | None) -> tuple[str, list[dict]]:
    """Return the session's id and messages, starting a new session if needed."""
    session_id = session_id or str(uuid.uuid4())
    if session_id not in _sessions:
        _sessions[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return session_id, _sessions[session_id]


def clear(session_id: str | None) -> None:
    _sessions.pop(session_id, None)
