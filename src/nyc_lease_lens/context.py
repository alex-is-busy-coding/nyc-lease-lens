import contextvars
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """A ThreadPoolExecutor whose tasks see the caller's context variables, such as the request ID.

    Plain worker threads start with an empty context, so logs from tools would lose the request ID.
    """

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        context = contextvars.copy_context()
        return super().submit(context.run, fn, *args, **kwargs)
