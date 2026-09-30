import uvicorn

from nyc_lease_lens.config import get_settings
from nyc_lease_lens.log import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.logging.level, settings.logging.format)
    server = settings.server
    uvicorn.run(
        "nyc_lease_lens.app:app",
        host=server.host,
        port=server.port,
        reload=server.reload,
        reload_dirs=["src"] if server.reload else None,
        log_config=None,  # keep our logging setup instead of uvicorn's
    )


if __name__ == "__main__":
    main()
