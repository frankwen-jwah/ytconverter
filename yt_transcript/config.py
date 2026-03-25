"""Configuration management — YAML loading, Config dataclasses, migration."""

import argparse
import copy
import json
import pathlib
from dataclasses import dataclass, field, fields as _dc_fields
from typing import Dict, List, Optional

# Resolve to the workspace root (parent of the yt_transcript/ package directory)
SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
OUTPUT_DIR = SCRIPT_DIR / "yt_transcripts"
CONFIG_FILE = OUTPUT_DIR / "config.yaml"
_LEGACY_CONFIG = OUTPUT_DIR / ".config.json"
DEFAULT_COOKIES_FILE = OUTPUT_DIR / "cookies.txt"

# ---------------------------------------------------------------------------
# Builtin defaults — single source of truth for all default values
# ---------------------------------------------------------------------------

_BUILTIN_DEFAULTS: Dict = {
    "output": {
        "dir": str(OUTPUT_DIR),
        "slug_max_length": 80,
        "overwrite": False,
    },
    "subtitles": {
        "lang": None,
        "prefer_auto": False,
    },
    "auth": {
        "cookies_from_browser": None,
        "cookies": None,
    },
    "network": {
        "retries": 3,
        "backoff_base": 2,
    },
    "whisper": {
        "enabled": True,
        "model": "large-v3",
        "device": "auto",
        "beam_size": 5,
        "vad_filter": True,
        "audio_quality": "0",
    },
    "llm": {
        "model": None,
        "polish_model": None,
        "model_preference": ["opus", "sonnet", "haiku"],
        "max_workers": 8,
        "timeout": 600,
        "polish": {
            "chunk_size_cjk": 500,
            "chunk_size": 1000,
            "context_ratio": 0.1,
        },
        "summarize": {
            "chunk_size_cjk": 1500,
            "chunk_size": 3000,
        },
        "error_patterns": [
            "API Error:",
            "You're out of extra usage",
            "rate limit",
            "overloaded",
        ],
    },
    "text": {
        "cjk_threshold": 0.3,
        "paragraph_gap_seconds": 4.0,
        "sentence_break_count": 6,
    },
    "flags": {
        "polish": False,
        "summarize": False,
        "no_chapters": False,
        "include_description": False,
    },
    "urls": [],
}


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OutputConfig:
    dir: str = str(OUTPUT_DIR)
    slug_max_length: int = 80
    overwrite: bool = False


@dataclass
class SubtitlesConfig:
    lang: Optional[str] = None
    prefer_auto: bool = False


@dataclass
class AuthConfig:
    cookies_from_browser: Optional[str] = None
    cookies: Optional[str] = None


@dataclass
class NetworkConfig:
    retries: int = 3
    backoff_base: int = 2


@dataclass
class WhisperConfig:
    enabled: bool = True
    model: str = "large-v3"
    device: str = "auto"
    beam_size: int = 5
    vad_filter: bool = True
    audio_quality: str = "0"


@dataclass
class LLMPolishConfig:
    chunk_size_cjk: int = 500
    chunk_size: int = 1000
    context_ratio: float = 0.1


@dataclass
class LLMSummarizeConfig:
    chunk_size_cjk: int = 1500
    chunk_size: int = 3000


@dataclass
class LLMConfig:
    model: Optional[str] = None
    polish_model: Optional[str] = None
    model_preference: List[str] = field(default_factory=lambda: ["opus", "sonnet", "haiku"])
    max_workers: int = 8
    timeout: int = 600
    polish: LLMPolishConfig = field(default_factory=LLMPolishConfig)
    summarize: LLMSummarizeConfig = field(default_factory=LLMSummarizeConfig)
    error_patterns: List[str] = field(default_factory=lambda: [
        "API Error:", "You're out of extra usage", "rate limit", "overloaded",
    ])


@dataclass
class TextConfig:
    cjk_threshold: float = 0.3
    paragraph_gap_seconds: float = 4.0
    sentence_break_count: int = 6


@dataclass
class FlagsConfig:
    polish: bool = False
    summarize: bool = False
    no_chapters: bool = False
    include_description: bool = False


@dataclass
class Config:
    output: OutputConfig = field(default_factory=OutputConfig)
    subtitles: SubtitlesConfig = field(default_factory=SubtitlesConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    text: TextConfig = field(default_factory=TextConfig)
    flags: FlagsConfig = field(default_factory=FlagsConfig)
    urls: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# YAML template generation (well-commented default config)
# ---------------------------------------------------------------------------

_CONFIG_TEMPLATE = """\
# yt_transcript configuration
# Values here serve as defaults; CLI flags always override.
# Lines starting with # are comments. Remove # to activate a setting.

# --- Output ---
output:
  dir: "{output_dir}"          # Output directory
  slug_max_length: 80           # Max characters in filename slug
  overwrite: false              # Overwrite existing files

# --- Subtitle preferences ---
subtitles:
  lang: null                    # Force subtitle language code (e.g. en, zh-Hans, ja)
  prefer_auto: false            # Prefer auto-generated subs over manual

# --- Authentication ---
auth:
  cookies_from_browser: null    # Browser name (chrome, firefox, edge, safari, opera, brave)
  cookies: null                 # Path to Netscape-format cookies.txt file

# --- Network ---
network:
  retries: 3                    # Retry attempts for network errors
  backoff_base: 2               # Exponential backoff base (seconds): wait = base^attempt

# --- Whisper audio fallback ---
whisper:
  enabled: true                 # Set false to disable Whisper fallback (same as --no-whisper)
  model: "large-v3"             # Model size: tiny, base, small, medium, large-v3
  device: "auto"                # Device: auto, cuda, cpu
  beam_size: 5                  # Beam search width (higher = more accurate, slower)
  vad_filter: true              # Voice activity detection filter
  audio_quality: "0"            # yt-dlp audio quality (0 = best)

# --- LLM (Claude CLI) ---
llm:
  model: null                   # Primary model for summarize (null = auto-detect best)
  polish_model: null            # Model for polish (null = auto-detect second-best)
  model_preference:             # Model fallback order (best first)
    - opus
    - sonnet
    - haiku
  max_workers: 8                # Parallel LLM request workers
  timeout: 600                  # Claude CLI call timeout (seconds)
  polish:
    chunk_size_cjk: 500         # Polish chunk size for CJK text (characters)
    chunk_size: 1000            # Polish chunk size for non-CJK text (characters)
    context_ratio: 0.1          # Context overlap as fraction of chunk size
  summarize:
    chunk_size_cjk: 1500        # Summarize chunk size for CJK text (characters)
    chunk_size: 3000            # Summarize chunk size for non-CJK text (characters)
  error_patterns:               # Patterns that indicate LLM error responses
    - "API Error:"
    - "You're out of extra usage"
    - "rate limit"
    - "overloaded"

# --- Text processing ---
text:
  cjk_threshold: 0.3           # Fraction of CJK chars to trigger CJK mode (0.0-1.0)
  paragraph_gap_seconds: 4.0    # Silence gap (seconds) that triggers a paragraph break
  sentence_break_count: 6       # Number of sentences before forced paragraph break

# --- Processing flags ---
flags:
  polish: false                 # Polish transcript via Claude CLI
  summarize: false              # Generate Pyramid/SCQA summary via Claude CLI
  no_chapters: false            # Ignore chapter markers, output flat transcript
  include_description: false    # Include video description in output

# --- Default URLs (processed when no URLs given on CLI) ---
urls: []
"""


def generate_config_template() -> str:
    """Return a well-commented YAML config template with all defaults."""
    return _CONFIG_TEMPLATE.format(output_dir=str(OUTPUT_DIR).replace("\\", "/"))


# ---------------------------------------------------------------------------
# Deep merge helper
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override dict onto a copy of base. Override wins."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Construct Config from dict
# ---------------------------------------------------------------------------

def _pick_fields(cls, d: dict) -> dict:
    """Filter dict to only keys that are valid dataclass fields."""
    valid = {f.name for f in _dc_fields(cls)}
    return {k: v for k, v in d.items() if k in valid}


def _config_from_dict(d: dict) -> Config:
    """Build a Config dataclass tree from a (merged) dict."""
    llm = d.get("llm") or {}
    _mw = llm.get("max_workers")
    _to = llm.get("timeout")
    return Config(
        output=OutputConfig(**_pick_fields(OutputConfig, d.get("output") or {})),
        subtitles=SubtitlesConfig(**_pick_fields(SubtitlesConfig, d.get("subtitles") or {})),
        auth=AuthConfig(**_pick_fields(AuthConfig, d.get("auth") or {})),
        network=NetworkConfig(**_pick_fields(NetworkConfig, d.get("network") or {})),
        whisper=WhisperConfig(**_pick_fields(WhisperConfig, d.get("whisper") or {})),
        llm=LLMConfig(
            model=llm.get("model"),
            polish_model=llm.get("polish_model"),
            model_preference=llm.get("model_preference") or ["opus", "sonnet", "haiku"],
            max_workers=_mw if _mw is not None else 8,
            timeout=_to if _to is not None else 600,
            polish=LLMPolishConfig(**_pick_fields(LLMPolishConfig, llm.get("polish") or {})),
            summarize=LLMSummarizeConfig(**_pick_fields(LLMSummarizeConfig, llm.get("summarize") or {})),
            error_patterns=llm.get("error_patterns") if llm.get("error_patterns") is not None else [],
        ),
        text=TextConfig(**_pick_fields(TextConfig, d.get("text") or {})),
        flags=FlagsConfig(**_pick_fields(FlagsConfig, d.get("flags") or {})),
        urls=d.get("urls") or [],
    )


# ---------------------------------------------------------------------------
# Migration from .config.json
# ---------------------------------------------------------------------------

# Maps flat .config.json keys to nested config.yaml paths
_LEGACY_KEY_MAP = {
    # Boolean flags
    "prefer_auto": ("subtitles", "prefer_auto"),
    "no_chapters": ("flags", "no_chapters"),
    "include_description": ("flags", "include_description"),
    "polish": ("flags", "polish"),
    "summarize": ("flags", "summarize"),
    "no_whisper": ("whisper", "enabled"),  # inverted
    # Valued keys
    "cookies_from_browser": ("auth", "cookies_from_browser"),
    "cookies": ("auth", "cookies"),
    "lang": ("subtitles", "lang"),
    "retries": ("network", "retries"),
    "whisper_model": ("whisper", "model"),
    "whisper_device": ("whisper", "device"),
    "model": ("llm", "model"),
    "polish_model": ("llm", "polish_model"),
}


def _migrate_json_config(json_path: pathlib.Path, yaml_path: pathlib.Path) -> dict:
    """Read .config.json, map flat keys to nested structure, write config.yaml.

    Returns the nested dict (to be used as config override).
    """
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    # Migrate old cookie_args format
    if "cookie_args" in raw and "cookies_from_browser" not in raw:
        cookie_args = raw.pop("cookie_args", [])
        if "--cookies-from-browser" in cookie_args:
            idx = cookie_args.index("--cookies-from-browser")
            if idx + 1 < len(cookie_args):
                raw["cookies_from_browser"] = cookie_args[idx + 1]

    nested: dict = {}
    for flat_key, value in raw.items():
        if flat_key == "urls":
            urls = value if isinstance(value, list) else [value]
            nested["urls"] = urls
            continue

        if flat_key not in _LEGACY_KEY_MAP:
            continue

        path = _LEGACY_KEY_MAP[flat_key]
        # Special: no_whisper is inverted → whisper.enabled
        if flat_key == "no_whisper":
            value = not value

        # Set nested value
        d = nested
        for part in path[:-1]:
            d = d.setdefault(part, {})
        d[path[-1]] = value

    # Write YAML config
    import yaml
    yaml_path.parent.mkdir(parents=True, exist_ok=True)

    # Start with the template for comments, then overlay migrated values
    # For simplicity, write the migrated data as YAML
    merged = _deep_merge(_BUILTIN_DEFAULTS, nested)

    # Write a header noting migration, then the full config
    header = "# Migrated from .config.json — review and adjust as needed.\n\n"
    content = header + yaml.dump(merged, default_flow_style=False, allow_unicode=True, sort_keys=False)
    yaml_path.write_text(content, encoding="utf-8")

    # Rename old config
    backup = json_path.with_suffix(".json.bak")
    try:
        json_path.rename(backup)
        print(f"  Migrated .config.json → config.yaml (backup: {backup.name})")
    except OSError:
        print(f"  Migrated .config.json → config.yaml (could not rename original)")

    return nested


# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

def load_config() -> Config:
    """Load config: YAML > migrate JSON > generate template > builtin defaults.

    Ensures PyYAML is available. Returns a fully populated Config.
    """
    from .deps import ensure_pyyaml
    ensure_pyyaml()

    override: dict = {}

    if CONFIG_FILE.exists():
        # Load existing YAML config
        import yaml
        try:
            raw = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                override = raw
        except Exception:
            print(f"  WARNING: Could not parse {CONFIG_FILE.name}, using defaults.")

    elif _LEGACY_CONFIG.exists():
        # Migrate from .config.json
        override = _migrate_json_config(_LEGACY_CONFIG, CONFIG_FILE)

    else:
        # Generate template on first run
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(generate_config_template(), encoding="utf-8")
        print(f"  Generated default config: {CONFIG_FILE}")

    merged = _deep_merge(_BUILTIN_DEFAULTS, override)
    return _config_from_dict(merged)


# ---------------------------------------------------------------------------
# CLI override application
# ---------------------------------------------------------------------------

def apply_cli_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Apply CLI flags over config values. CLI wins where explicitly set.

    Uses the convention that argparse defaults are None (valued) or False (bools),
    and only non-default values override config.
    """
    # --- URLs ---
    if args.urls:
        config.urls = list(args.urls)

    # --- Output ---
    if args.output_dir is not None:
        config.output.dir = str(args.output_dir)
    if args.overwrite:
        config.output.overwrite = True

    # --- Subtitles ---
    if args.lang is not None:
        config.subtitles.lang = args.lang
    if args.prefer_auto:
        config.subtitles.prefer_auto = True

    # --- Auth ---
    if args.cookies_from_browser is not None:
        config.auth.cookies_from_browser = args.cookies_from_browser
    if args.cookies is not None:
        config.auth.cookies = str(args.cookies)

    # --- Network ---
    if args.retries is not None:
        config.network.retries = args.retries

    # --- Whisper ---
    if args.no_whisper:
        config.whisper.enabled = False
    if args.whisper_model is not None:
        config.whisper.model = args.whisper_model
    if args.whisper_device is not None:
        config.whisper.device = args.whisper_device

    # --- LLM ---
    if args.model is not None:
        config.llm.model = args.model
    if args.polish_model is not None:
        config.llm.polish_model = args.polish_model

    # --- Flags ---
    if args.polish:
        config.flags.polish = True
    if args.summarize:
        config.flags.summarize = True
    if args.no_chapters:
        config.flags.no_chapters = True
    if args.include_description:
        config.flags.include_description = True

    return config


# ---------------------------------------------------------------------------
# Cookie args builder
# ---------------------------------------------------------------------------

def build_cookie_args(config: Config) -> List[str]:
    """Build yt-dlp cookie arguments from config."""
    if config.auth.cookies_from_browser:
        return ["--cookies-from-browser", config.auth.cookies_from_browser]
    if config.auth.cookies:
        return ["--cookies", config.auth.cookies]
    # Auto-detect default cookies file
    if DEFAULT_COOKIES_FILE.exists():
        return ["--cookies", str(DEFAULT_COOKIES_FILE)]
    return []
