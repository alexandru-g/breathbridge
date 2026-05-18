"""Entry point: python -m breathbridge"""

import asyncio
import logging
import sys

from .bridge import Bridge
from .config import Settings


def main() -> None:
    try:
        settings = Settings()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        print("Set PB_WS_HOST at minimum. See README for all options.", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    bridge = Bridge(settings)
    asyncio.run(bridge.run())


if __name__ == "__main__":
    main()
