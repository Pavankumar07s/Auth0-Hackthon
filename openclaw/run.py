"""OpenClaw entry point."""

import logging
import sys

from main import OpenClawEngine


def setup_logging() -> None:
    """Configure structured logging."""
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/openclaw.log"),
        ],
    )
    # Debug for our modules
    logging.getLogger("src").setLevel(logging.DEBUG)
    logging.getLogger("main").setLevel(logging.DEBUG)
    # Quiet external libs
    logging.getLogger("paho").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


if __name__ == "__main__":
    setup_logging()

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/settings.yaml"
    engine = OpenClawEngine(config_path=config_path)
    engine.run_forever()
