from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class Source(BaseModel):
    """A dataset behind the answer, for the page's sources footer."""

    name: str
    url: str
    updated: str  # how often the publisher refreshes it


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]
    sources: list[Source] = []
