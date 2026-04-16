"""Dependency management — ensures required packages are installed."""

import logging
import pathlib
import re
import shutil
import subprocess
import sys

# Silence noisy benign warnings from MarkItDown's PDF backends
# (e.g., "Could not get FontBBox from font descriptor ...") — these
# are emitted for PDFs with malformed font descriptors and do not
# affect extraction output.
logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)


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


def _check_java_runtime() -> None:
    """Verify Java 11+ is available on PATH. Raises SystemExit if not."""
    java_path = shutil.which("java")
    if not java_path:
        print("ERROR: Java 11+ is required for opendataloader-pdf but 'java' "
              "was not found on PATH. Install a JDK 11+ and ensure 'java' "
              "is accessible.", file=sys.stderr)
        sys.exit(1)
    try:
        result = subprocess.run(
            ["java", "-version"], capture_output=True, text=True, timeout=10)
        # Java prints version to stderr
        version_output = result.stderr or result.stdout
        match = re.search(r'"(\d+)', version_output)
        if match:
            major = int(match.group(1))
            if major < 11:
                print(f"ERROR: Java {major} found but opendataloader-pdf "
                      f"requires Java 11+. Please upgrade.", file=sys.stderr)
                sys.exit(1)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"WARNING: Could not verify Java version: {exc}",
              file=sys.stderr)


def ensure_opendataloader_pdf() -> None:
    """Ensure opendataloader-pdf (and Java 11+) are available. Auto-installs via pip if missing."""
    if sys.version_info < (3, 10):
        print(f"ERROR: opendataloader-pdf requires Python 3.10+. "
              f"Current version: {sys.version}", file=sys.stderr)
        sys.exit(1)
    _check_java_runtime()
    try:
        import opendataloader_pdf  # noqa: F401
        return
    except ImportError:
        pass
    print("opendataloader-pdf not found. Installing...")
    if not _pip_install("opendataloader-pdf"):
        print("ERROR: Failed to install opendataloader-pdf. "
              "Install manually: pip install opendataloader-pdf",
              file=sys.stderr)
        sys.exit(1)


def ensure_python_docx() -> None:
    """Ensure python-docx is installed. Auto-installs via pip if missing."""
    try:
        import docx  # noqa: F401
        return
    except ImportError:
        pass
    print("python-docx not found. Installing...")
    if not _pip_install("python-docx"):
        print("ERROR: Failed to install python-docx. Install manually: pip install python-docx",
              file=sys.stderr)
        sys.exit(1)


def ensure_python_pptx() -> None:
    """Ensure python-pptx is installed. Auto-installs via pip if missing."""
    try:
        import pptx  # noqa: F401
        return
    except ImportError:
        pass
    print("python-pptx not found. Installing...")
    if not _pip_install("python-pptx"):
        print("ERROR: Failed to install python-pptx. Install manually: pip install python-pptx",
              file=sys.stderr)
        sys.exit(1)


def ensure_mammoth() -> None:
    """Ensure mammoth is installed (for .doc conversion). Auto-installs via pip if missing."""
    try:
        import mammoth  # noqa: F401
        return
    except ImportError:
        pass
    print("mammoth not found. Installing...")
    if not _pip_install("mammoth"):
        print("ERROR: Failed to install mammoth. Install manually: pip install mammoth",
              file=sys.stderr)
        sys.exit(1)


def ensure_feedparser() -> None:
    """Ensure feedparser is installed. Auto-installs via pip if missing."""
    try:
        import feedparser  # noqa: F401
        return
    except ImportError:
        pass
    print("feedparser not found. Installing...")
    if not _pip_install("feedparser"):
        print("ERROR: Failed to install feedparser. Install manually: pip install feedparser",
              file=sys.stderr)
        sys.exit(1)


def ensure_beautifulsoup() -> None:
    """Ensure beautifulsoup4 is installed. Auto-installs via pip if missing."""
    try:
        import bs4  # noqa: F401
        return
    except ImportError:
        pass
    print("beautifulsoup4 not found. Installing...")
    if not _pip_install("beautifulsoup4"):
        print("ERROR: Failed to install beautifulsoup4. Install manually: pip install beautifulsoup4",
              file=sys.stderr)
        sys.exit(1)


def ensure_browser_cookie3() -> None:
    """Ensure browser-cookie3 is installed. Auto-installs via pip if missing."""
    try:
        import browser_cookie3  # noqa: F401
        return
    except ImportError:
        pass
    print("browser-cookie3 not found. Installing...")
    if not _pip_install("browser-cookie3"):
        print("ERROR: Failed to install browser-cookie3. Install manually: pip install browser-cookie3",
              file=sys.stderr)
        sys.exit(1)


def ensure_playwright() -> None:
    """Ensure playwright + Chromium browser are available. Auto-installs if missing."""
    try:
        import playwright  # noqa: F401
        return
    except ImportError:
        pass
    print("playwright not found. Installing...")
    if not _pip_install("playwright"):
        print("ERROR: Failed to install playwright. Install manually: pip install playwright",
              file=sys.stderr)
        sys.exit(1)
    print("Installing Chromium browser for playwright...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"])
    except subprocess.CalledProcessError:
        print("WARNING: Failed to install Chromium. Run manually: playwright install chromium",
              file=sys.stderr)


def ensure_openai() -> None:
    """Ensure openai package is available. Auto-installs via pip if missing."""
    try:
        import openai  # noqa: F401
        return
    except ImportError:
        pass
    print("openai not found. Installing...")
    if not _pip_install("openai"):
        print("ERROR: Failed to install openai. Install manually: pip install openai",
              file=sys.stderr)
        sys.exit(1)


def ensure_dotenv() -> None:
    """Ensure python-dotenv is available. Auto-installs via pip if missing."""
    try:
        import dotenv  # noqa: F401
        return
    except ImportError:
        pass
    print("python-dotenv not found. Installing...")
    if not _pip_install("python-dotenv"):
        print("ERROR: Failed to install python-dotenv. Install manually: pip install python-dotenv",
              file=sys.stderr)
        sys.exit(1)


def ensure_markitdown() -> None:
    """Ensure markitdown[all] is available. Auto-installs via pip if missing."""
    try:
        import markitdown  # noqa: F401
        return
    except ImportError:
        pass
    print("markitdown not found. Installing...")
    if not _pip_install("markitdown[all]"):
        print("ERROR: Failed to install markitdown. Install manually: pip install 'markitdown[all]'",
              file=sys.stderr)
        sys.exit(1)
