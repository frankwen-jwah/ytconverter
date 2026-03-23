"""Constants and cookie config persistence."""

import argparse
import json
import pathlib
from typing import List

# Resolve to the workspace root (parent of the yt_transcript/ package directory)
SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
OUTPUT_DIR = SCRIPT_DIR / "yt_transcripts"
CONFIG_FILE = OUTPUT_DIR / ".config.json"


def load_cookie_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cookie_config(cookie_args: List[str]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"cookie_args": cookie_args}, indent=2))


def build_cookie_args(args: argparse.Namespace) -> List[str]:
    if args.cookies_from_browser:
        return ["--cookies-from-browser", args.cookies_from_browser]
    # Check saved config
    cfg = load_cookie_config()
    return cfg.get("cookie_args", [])
