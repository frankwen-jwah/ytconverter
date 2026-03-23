"""Constants and config file management."""

import argparse
import json
import pathlib
from typing import List

# Resolve to the workspace root (parent of the yt_transcript/ package directory)
SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
OUTPUT_DIR = SCRIPT_DIR / "yt_transcripts"
CONFIG_FILE = OUTPUT_DIR / ".config.json"

# Config keys that map to boolean CLI flags (argparse default: False)
_BOOL_KEYS = {"prefer_auto", "no_chapters", "include_description", "polish"}

# Config keys that map to valued CLI args (argparse default: None or a specific value)
_VALUED_KEYS = {"cookies_from_browser", "lang", "retries"}

# Built-in defaults for valued args (must match argparse defaults in cli.py)
_BUILTIN_DEFAULTS = {"retries": 3}


def load_config() -> dict:
    """Load config from .config.json. Returns empty dict if missing/corrupt."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    # Migrate old cookie_args format → cookies_from_browser
    if "cookie_args" in cfg and "cookies_from_browser" not in cfg:
        cookie_args = cfg.pop("cookie_args", [])
        if "--cookies-from-browser" in cookie_args:
            idx = cookie_args.index("--cookies-from-browser")
            if idx + 1 < len(cookie_args):
                cfg["cookies_from_browser"] = cookie_args[idx + 1]

    return cfg


def apply_config_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Apply config file defaults to args where CLI didn't override."""
    cfg = load_config()
    if not cfg:
        return args

    # URLs: append config URLs if no URLs were passed on CLI
    if not args.urls and not args.file and "urls" in cfg:
        config_urls = cfg["urls"]
        if isinstance(config_urls, str):
            config_urls = [config_urls]
        args.urls = config_urls

    # Boolean flags: apply config value only if CLI left it at False (default)
    for key in _BOOL_KEYS:
        if key in cfg and not getattr(args, key, False):
            setattr(args, key, bool(cfg[key]))

    # Valued flags: apply config value only if CLI left it at its built-in default
    for key in _VALUED_KEYS:
        if key not in cfg:
            continue
        current = getattr(args, key, None)
        builtin = _BUILTIN_DEFAULTS.get(key)  # None for most keys
        if current == builtin:
            setattr(args, key, cfg[key])

    return args


def build_cookie_args(args: argparse.Namespace) -> List[str]:
    """Build yt-dlp cookie arguments from the (config-merged) args."""
    if args.cookies_from_browser:
        return ["--cookies-from-browser", args.cookies_from_browser]
    return []
