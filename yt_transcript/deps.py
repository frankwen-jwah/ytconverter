"""Dependency management — ensures required packages are installed."""

import pathlib
import shutil
import subprocess
import sys


def _pip_install(package: str) -> bool:
    """Try to pip-install a package with fallback strategies. Returns True on success."""
    base = [sys.executable, "-m", "pip", "install", "--quiet"]
    for extra_args in [[], ["--user"], ["--break-system-packages"], ["--user", "--break-system-packages"]]:
        try:
            subprocess.check_call(base + extra_args + [package],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except subprocess.CalledProcessError:
            continue
    return False


def ensure_pyyaml() -> None:
    """Ensure PyYAML is installed. Auto-installs via pip if missing."""
    try:
        import yaml  # noqa: F401
        return
    except ImportError:
        pass
    print("PyYAML not found. Installing...")
    if not _pip_install("PyYAML"):
        print("ERROR: Failed to install PyYAML. Install manually: pip install PyYAML",
              file=sys.stderr)
        sys.exit(1)


def ensure_yt_dlp() -> str:
    """Ensure yt-dlp is installed. Returns path to binary."""
    path = shutil.which("yt-dlp")
    if path:
        return path
    print("yt-dlp not found. Installing via pip...")
    if not _pip_install("yt-dlp"):
        print("ERROR: Failed to install yt-dlp. Install manually: pip install yt-dlp", file=sys.stderr)
        sys.exit(1)
    path = shutil.which("yt-dlp")
    if not path:
        # pip may have installed to a path not in PATH; try common locations
        for candidate in [
            pathlib.Path(sys.prefix) / "bin" / "yt-dlp",
            pathlib.Path.home() / ".local" / "bin" / "yt-dlp",
        ]:
            if candidate.exists():
                return str(candidate)
        print("ERROR: yt-dlp installed but not found in PATH.", file=sys.stderr)
        sys.exit(1)
    return path


def ensure_requests() -> None:
    """Ensure requests is installed. Auto-installs via pip if missing."""
    try:
        import requests  # noqa: F401
        return
    except ImportError:
        pass
    print("requests not found. Installing...")
    if not _pip_install("requests"):
        print("ERROR: Failed to install requests. Install manually: pip install requests",
              file=sys.stderr)
        sys.exit(1)


def ensure_trafilatura() -> None:
    """Ensure trafilatura is installed. Auto-installs via pip if missing."""
    try:
        import trafilatura  # noqa: F401
        return
    except ImportError:
        pass
    print("trafilatura not found. Installing...")
    if not _pip_install("trafilatura"):
        print("ERROR: Failed to install trafilatura. Install manually: pip install trafilatura",
              file=sys.stderr)
        sys.exit(1)


def ensure_pymupdf4llm() -> None:
    """Ensure pymupdf4llm (and pymupdf) are installed. Auto-installs via pip if missing."""
    try:
        import pymupdf4llm  # noqa: F401
        return
    except ImportError:
        pass
    print("pymupdf4llm not found. Installing...")
    if not _pip_install("pymupdf4llm"):
        print("ERROR: Failed to install pymupdf4llm. Install manually: pip install pymupdf4llm",
              file=sys.stderr)
        sys.exit(1)
