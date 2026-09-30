import uvicorn

from nyc_lease_lens import config
from nyc_lease_lens.app import app


def main() -> None:
    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
