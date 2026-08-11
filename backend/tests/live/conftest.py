from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_live_environment(dotenv_path: Path | None = None) -> None:
    if os.getenv("RUN_LIVE_ZEN_TEST") == "1":
        load_dotenv(dotenv_path=dotenv_path, override=False)


load_live_environment()
