import logging
import uuid

logger = logging.getLogger(__name__)


class SessionStore:
    """Conversation history per session. In-memory, single process: lost on restart."""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self._sessions: dict[str, list[dict]] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, list[dict]]:
        """Return the session's id and messages, starting a new session if needed."""
        session_id = session_id or str(uuid.uuid4())
        if session_id not in self._sessions:
            self._sessions[session_id] = [{"role": "system", "content": self.system_prompt}]
            logger.debug("session created", extra={"session_id": session_id, "sessions": len(self._sessions)})
        return session_id, self._sessions[session_id]

    def clear(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)
