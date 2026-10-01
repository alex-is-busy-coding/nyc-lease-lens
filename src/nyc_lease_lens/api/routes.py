import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from nyc_lease_lens.agent.loop import Agent
from nyc_lease_lens.agent.sessions import SessionStore
from nyc_lease_lens.api.schemas import ChatRequest, ChatResponse, Source
from nyc_lease_lens.observability.context import request_id

STATIC_DIR = Path(__file__).parent.parent / "static"
# Without this, browsers may reuse an old page for hours after a deploy. With it they still cache,
# but check the ETag first (a cheap 304 when nothing changed).
REVALIDATE = {"Cache-Control": "no-cache"}
logger = logging.getLogger(__name__)


def get_agent(request: Request) -> Agent:
    return request.app.state.agent


def get_sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


AgentDep = Annotated[Agent, Depends(get_agent)]
SessionsDep = Annotated[SessionStore, Depends(get_sessions)]


router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers=REVALIDATE)


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, agent: AgentDep, sessions: SessionsDep) -> ChatResponse:
    session_id, messages = sessions.get_or_create(body.session_id)
    logger.info("chat", extra={"session_id": session_id, "turn": len(messages), "message_chars": len(body.message)})
    logger.debug("chat message", extra={"text": body.message})

    messages += [{"role": "user", "content": body.message}]

    try:
        response, tool_calls = agent.run(messages)
    except Exception:
        messages.pop()  # a retry shouldn't send the message twice
        logger.exception("agent failed", extra={"session_id": session_id})
        response = f"Sorry, something went wrong on our side. Please try again. (Reference: {request_id.get()})"
        tool_calls = []

    datasets = agent.tools.sources_for(call["name"] for call in tool_calls)
    sources = [Source(name=d.label, url=d.url, updated=d.refreshed) for d in datasets]
    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls, sources=sources)


@router.post("/clear")
def clear(sessions: SessionsDep, session_id: str | None = None) -> dict[str, str]:
    sessions.clear(session_id)
    logger.info("session cleared", extra={"session_id": session_id})
    return {"status": "ok"}
