import logging
import os

import uvicorn

from .app import build, create_app
from .config import Settings


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    uvicorn.run(create_app(build(settings)), host="0.0.0.0", port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
