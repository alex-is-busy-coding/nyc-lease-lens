import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from nyc_lease_lens.observability.context import request_id
from nyc_lease_lens.observability.log import ms_since

logger = logging.getLogger(__name__)


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
                "duration_ms": ms_since(start),
            },
        )
        return response
    finally:
        request_id.reset(token)
