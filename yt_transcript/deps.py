"""Dependency management — ensures yt-dlp is installed."""

import pathlib
import shutil
import subprocess
import sys


def ensure_yt_dlp() -> str:
    """Ensure yt-dlp is installed. Returns path to binary."""
    path = shutil.which("yt-dlp")
    if path:
        return path
    print("yt-dlp not found. Installing via pip...")
    base = [sys.executable, "-m", "pip", "install", "--quiet"]
    installed = False
    for extra_args in [[], ["--user"], ["--break-system-packages"], ["--user", "--break-system-packages"]]:
        try:
            subprocess.check_call(base + extra_args + ["yt-dlp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            installed = True
            break
        except subprocess.CalledProcessError:
            continue
    if not installed:
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
