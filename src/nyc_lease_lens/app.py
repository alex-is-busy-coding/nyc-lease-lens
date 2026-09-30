import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from nyc_lease_lens.agent import Agent
from nyc_lease_lens.config import get_settings
from nyc_lease_lens.context import request_id
from nyc_lease_lens.log import configure_logging
from nyc_lease_lens.opendata import OpenDataClient
from nyc_lease_lens.schemas import ChatRequest, ChatResponse
from nyc_lease_lens.sessions import SessionStore
from nyc_lease_lens.tools import build_registry

STATIC_DIR = Path(__file__).parent / "static"

settings = get_settings()
configure_logging(settings.log_level, settings.log_format)
logger = logging.getLogger(__name__)

if settings.vertexai_project:
    # Bill Vertex calls to our project; gcloud user credentials have no quota project by default.
    os.environ.setdefault("GOOGLE_CLOUD_QUOTA_PROJECT", settings.vertexai_project)
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.vertexai_project)

client = OpenDataClient(
    geosearch_url=settings.geosearch_url,
    socrata_url=settings.socrata_url,
    timeout=settings.opendata_timeout,
    retries=settings.opendata_retries,
)
agent = Agent(
    tools=build_registry(client),
    model=settings.model,
    vertex_project=settings.vertexai_project,
    vertex_location=settings.vertexai_location,
    max_tool_rounds=settings.max_tool_rounds,
)
sessions = SessionStore(agent.system_prompt)
logger.info(
    "app ready",
    extra={
        "model": settings.model,
        "vertex_location": settings.vertexai_location,
        "tools": len(agent.tools.schemas),
        "max_tool_rounds": settings.max_tool_rounds,
    },
)

app = FastAPI(title="NYC Lease Lens")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Tag everything logged during a request with one ID, returned to the client as X-Request-ID."""
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if re.fullmatch(r"[\w-]{1,64}", incoming) else uuid.uuid4().hex[:12]
    token = request_id.set(rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed", extra={"method": request.method, "path": request.url.path})
        raise
    else:
        response.headers["X-Request-ID"] = rid
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000),
            },
        )
        return response
    finally:
        request_id.reset(token)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    session_id, messages = sessions.get_or_create(request.session_id)
    logger.info("chat", extra={"session_id": session_id, "turn": len(messages), "message_chars": len(request.message)})
    logger.debug("chat message", extra={"text": request.message})

    # Append user's message to the context
    messages += [{"role": "user", "content": request.message}]

    try:
        response, tool_calls = agent.run(messages)
    except Exception as e:
        logger.exception("agent failed", extra={"session_id": session_id})
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.clear(session_id)
    logger.info("session cleared", extra={"session_id": session_id})
    return {"status": "ok"}
