"""LLM-based post-processing — polish and summarize via Claude CLI."""

import json as _json
import logging
import pathlib
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .config import OUTPUT_DIR
from .exceptions import LLMError
from .text import is_cjk_dominant

if TYPE_CHECKING:
    from .config import Config

# ---------------------------------------------------------------------------
# File logger — writes to content/llm.log
# ---------------------------------------------------------------------------

_log = logging.getLogger("yt_transcript.llm")
_log.setLevel(logging.DEBUG)
_log.propagate = False

_log_file = OUTPUT_DIR / "llm.log"
_log_file.parent.mkdir(parents=True, exist_ok=True)
_fh = logging.FileHandler(_log_file, encoding="utf-8")
_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_log.addHandler(_fh)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_POLISH_SYSTEM = """\
You are a transcript polishing assistant. Fix formatting artifacts in speech-to-text output.

Rules:
- Fix punctuation and capitalization
- Fix obvious speech-recognition errors (homophones, word boundaries)
- For CJK text: fix punctuation placement (use Chinese punctuation like \u3002\uff0c\u3001\uff01\uff1f), \
remove spurious spaces between characters, fix segmentation errors
- Do NOT translate \u2014 keep the original language exactly
- Do NOT change meaning or add/remove content
- Do NOT add commentary or explanation
- Leave markdown headers (## ...), blockquotes (> ...), and YAML frontmatter exactly as-is
- Output the COMPLETE text from start to finish — do not skip, omit, or summarize any portion
- Every sentence in the input must appear in your output, even if you cannot fully fix it
- Output ONLY the fixed text, preserving all original formatting structure"""

_SUMMARIZE_SYSTEM = """\
You are a summarization assistant using the Pyramid Principle and SCQA framework.

Rules:
- Write the summary in the SAME LANGUAGE as the transcript
- Use the exact template format provided
- Be concise but comprehensive
- Group key points by theme, pyramid-style (conclusions first, then supporting details)
- Include notable quotes or moments with context
- Output ONLY the summary markdown document"""


# ---------------------------------------------------------------------------
# Module state — initialized by init_llm()
# ---------------------------------------------------------------------------

_config: Optional["Config"] = None
_claude_path: Optional[str] = None
_model_alias: Optional[str] = None
_fallback_alias: Optional[str] = None
_initial_primary: Optional[str] = None
_initial_secondary: Optional[str] = None
_model_lock = threading.Lock()


def _find_claude_cli() -> str:
    """Find the claude CLI binary. Raises LLMError if not found."""
    path = shutil.which("claude")
    if path:
        return path
    raise LLMError(
        "claude CLI not found. Install Claude Code: "
        "https://docs.anthropic.com/en/docs/claude-code"
    )


def _set_model_override(model_str: str) -> None:
    """Set model directly, skipping probing."""
    global _model_alias, _fallback_alias

    if model_str == _model_alias:
        return

    pref = _config.llm.model_preference if _config else ["opus", "sonnet", "haiku"]
    alias_indices = {a: i for i, a in enumerate(pref)}
    if model_str in alias_indices:
        idx = alias_indices[model_str]
        _model_alias = model_str
        _fallback_alias = pref[idx + 1] if idx + 1 < len(pref) else None
    else:
        _model_alias = model_str
        _fallback_alias = None

    print(f"  Using model: {_model_alias}"
          + (f" (fallback: {_fallback_alias})" if _fallback_alias else ""),
          flush=True)


def init_llm(config: "Config") -> None:
    """Initialize LLM module with config. Finds CLI and sets up models.

    Raises LLMError if claude CLI is not found.
    """
    global _config, _claude_path, _model_alias, _fallback_alias
    global _initial_primary, _initial_secondary

    _config = config
    _claude_path = _find_claude_cli()

    model_override = config.llm.model
    if model_override:
        _set_model_override(model_override)
    else:
        pref = config.llm.model_preference
        _model_alias = pref[0] if pref else "opus"
        _fallback_alias = pref[1] if len(pref) > 1 else None
        print(f"  Using model: {_model_alias}"
              + (f" (fallback: {_fallback_alias})" if _fallback_alias else ""),
              flush=True)

    _initial_primary = _model_alias
    _initial_secondary = _fallback_alias


def get_models() -> tuple:
    """Return (primary_model, secondary_model) from initial auto-detection.

    Primary is the best available (for summarize), secondary is the next
    best (for polish). These are stable — unaffected by set_model() calls.
    """
    return _initial_primary, _initial_secondary


def set_model(model_str: str) -> None:
    """Switch the active model."""
    if not _claude_path:
        raise LLMError("LLM not initialized. Call init_llm() first.")
    if model_str == _model_alias:
        return
    _set_model_override(model_str)


# ---------------------------------------------------------------------------
# Claude CLI call
# ---------------------------------------------------------------------------

def _run_claude_cli(model: str, system: str, user_msg: str,
                     fallback: Optional[str] = None,
                     ) -> subprocess.CompletedProcess:
    """Run a single claude CLI call. Returns CompletedProcess."""
    timeout = _config.llm.timeout if _config else 600

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(system)
        system_file = f.name

    try:
        cmd = [
            _claude_path, "-p",
            "--output-format", "json",
            "--model", model,
            "--max-turns", "1",
            "--system-prompt-file", system_file,
            "--tools", "",
        ]
        if fallback:
            cmd.extend(["--fallback-model", fallback])

        return subprocess.run(
            cmd,
            input=user_msg,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    finally:
        pathlib.Path(system_file).unlink(missing_ok=True)


def _parse_json_output(stdout: str) -> str:
    """Extract text result from JSON output."""
    try:
        data = _json.loads(stdout)
        return data.get("result", "")
    except (_json.JSONDecodeError, TypeError):
        return stdout.strip()


def _has_real_error(result: subprocess.CompletedProcess) -> bool:
    """Check if a non-zero exit is a real error."""
    return result.returncode != 0


def _is_error_content(text: str) -> Optional[str]:
    """Return matched pattern if text is an error message, else None."""
    patterns = _config.llm.error_patterns if _config else [
        "API Error:", "You're out of extra usage", "rate limit", "overloaded"]
    head = text[:200]
    for p in patterns:
        if p.lower() in head.lower():
            return p
    return None


def _call_claude(system: str, user_msg: str) -> str:
    """Call claude CLI with automatic model fallback. Thread-safe."""
    global _model_alias, _fallback_alias

    if not _claude_path or not _model_alias:
        raise LLMError("LLM not initialized. Call init_llm() first.")

    timeout = _config.llm.timeout if _config else 600
    pref = _config.llm.model_preference if _config else ["opus", "sonnet", "haiku"]

    with _model_lock:
        model = _model_alias
        fallback = _fallback_alias

    input_chars = len(user_msg)
    sys_chars = len(system)
    msg = (f"calling {model} | system: {sys_chars} chars | "
           f"input: {input_chars} chars")
    print(f"    [llm] {msg}", flush=True)
    _log.info(msg)
    _log.debug("input preview: %s", user_msg[:300].replace("\n", "\\n"))
    t0 = time.time()

    try:
        result = _run_claude_cli(model, system, user_msg, fallback)
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        msg = f"TIMEOUT after {elapsed:.0f}s"
        print(f"    [llm] {msg}", flush=True)
        _log.error(msg)
        raise LLMError(f"Claude CLI timed out after {timeout} seconds")
    except FileNotFoundError:
        _log.error("Claude CLI binary not found at %s", _claude_path)
        raise LLMError("Claude CLI not found")

    elapsed = time.time() - t0
    msg = (f"returned in {elapsed:.1f}s | exit={result.returncode} | "
           f"stdout={len(result.stdout)} chars | stderr={len(result.stderr)} chars")
    print(f"    [llm] {msg}", flush=True)
    _log.info(msg)
    if result.stderr.strip():
        _log.debug("stderr: %s", result.stderr.strip()[:500])

    if _has_real_error(result) and fallback:
        stderr = result.stderr.strip()
        with _model_lock:
            msg = (f"Model {model} failed ({stderr[:80]}). "
                   f"Switching to {fallback}...")
            print(f"  {msg}", flush=True)
            _log.warning(msg)

            idx = pref.index(fallback) if fallback in pref else -1
            _model_alias = fallback
            _fallback_alias = pref[idx + 1] if idx + 1 < len(pref) else None
            model = _model_alias
            fallback = _fallback_alias

        t0 = time.time()
        msg = f"retrying with {model}..."
        print(f"    [llm] {msg}", flush=True)
        _log.info(msg)
        try:
            result = _run_claude_cli(model, system, user_msg, fallback)
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            msg = f"retry TIMEOUT after {elapsed:.0f}s"
            print(f"    [llm] {msg}", flush=True)
            _log.error(msg)
            raise LLMError(f"Claude CLI timed out after {timeout} seconds")

        elapsed = time.time() - t0
        msg = f"retry returned in {elapsed:.1f}s | exit={result.returncode}"
        print(f"    [llm] {msg}", flush=True)
        _log.info(msg)

    if _has_real_error(result):
        stderr = result.stderr.strip()
        msg = f"CLI failed (exit {result.returncode}): {stderr[:200]}"
        print(f"    [llm] ERROR: {msg}", flush=True)
        _log.error(msg)
        raise LLMError(f"Claude CLI failed (exit {result.returncode}): {stderr}")

    raw = result.stdout.strip()
    if not raw:
        _log.error("Claude CLI returned empty stdout")
        raise LLMError("Claude CLI returned empty output")

    output = _parse_json_output(raw)

    if not output:
        _log.error("Claude CLI JSON had empty result field")
        raise LLMError("Claude CLI returned empty output")

    error_match = _is_error_content(output)
    if error_match:
        msg = f"Claude CLI returned error in output ({error_match}): {output[:200]}"
        _log.error(msg)
        raise LLMError(msg)

    msg = f"output: {len(output)} chars"
    print(f"    [llm] {msg}", flush=True)
    _log.info(msg)
    _log.debug("output preview: %s", output[:300].replace("\n", "\\n"))
    return output


def _call_claude_parallel(
    system: str,
    work_items: List[Tuple[Any, str]],
    label: str = "chunk",
) -> Tuple[Dict[Any, str], Dict[Any, LLMError]]:
    """Run multiple _call_claude calls in parallel."""
    total = len(work_items)
    max_workers = _config.llm.max_workers if _config else 8

    if total == 1:
        key, user_msg = work_items[0]
        try:
            return {key: _call_claude(system, user_msg)}, {}
        except LLMError as e:
            return {}, {key: e}

    results: Dict[Any, str] = {}
    errors: Dict[Any, LLMError] = {}
    workers = min(max_workers, total)
    print(f"  Processing {total} {label}s ({workers} workers)...", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_key = {
            executor.submit(_call_claude, system, user_msg): key
            for key, user_msg in work_items
        }
        done = 0
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            done += 1
            try:
                results[key] = future.result()
                print(f"  Completed {label} {done}/{total}", flush=True)
            except LLMError as e:
                errors[key] = e
                msg = f"{label} {key} failed: {e}"
                print(f"  WARNING: {msg}", flush=True)
                _log.warning(msg)

    return results, errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_text_by_punctuation(
    text: str, chunk_size: int, overlap: int = 200,
) -> List[Tuple[int, str]]:
    """Split text into overlapping chunks at punctuation boundaries."""
    splits: List[int] = [0]
    pos = 0
    while pos < len(text):
        end = pos + chunk_size
        if end >= len(text):
            break
        best = -1
        for j in range(end, max(pos, end - 500), -1):
            if text[j] in '。！？.!?':
                best = j + 1
                break
        if best == -1:
            for j in range(end, max(pos, end - 500), -1):
                if text[j] in '，、；：,;:':
                    best = j + 1
                    break
        if best == -1:
            for j in range(end, max(pos, end - 500), -1):
                if text[j] == ' ':
                    best = j + 1
                    break
        if best == -1:
            best = end
        splits.append(best)
        pos = best
    splits.append(len(text))

    result: List[Tuple[int, str]] = []
    for i in range(len(splits) - 1):
        if i == 0:
            result.append((0, text[splits[0]:splits[1]]))
        else:
            overlap_start = max(splits[0], splits[i] - overlap)
            olap = splits[i] - overlap_start
            result.append((olap, text[overlap_start:splits[i + 1]]))
    return result


def _split_body_sections(body: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Split markdown body into preamble (before first ##) and sections."""
    lines = body.split("\n")
    preamble_lines: List[str] = []
    sections: List[Tuple[str, str]] = []
    current_header = ""
    current_body: List[str] = []
    in_preamble = True

    for line in lines:
        if line.startswith("## "):
            if in_preamble:
                in_preamble = False
            else:
                sections.append((current_header, "\n".join(current_body)))
            current_header = line
            current_body = []
        elif in_preamble:
            preamble_lines.append(line)
        else:
            current_body.append(line)

    if not in_preamble:
        sections.append((current_header, "\n".join(current_body)))

    return "\n".join(preamble_lines), sections


# ---------------------------------------------------------------------------
# Polish
# ---------------------------------------------------------------------------

def _get_polish_chunk_config() -> Tuple[int, int, float]:
    """Return (chunk_size_cjk, chunk_size, context_ratio) from config."""
    if _config:
        pc = _config.llm.polish
        return pc.chunk_size_cjk, pc.chunk_size, pc.context_ratio
    return 500, 1000, 0.1


def polish_transcript(unpolished_path: pathlib.Path,
                      polished_path: pathlib.Path) -> None:
    """Polish a transcript file via Claude CLI. Saves to polished_path."""
    markdown = unpolished_path.read_text(encoding="utf-8")
    chunk_cjk, chunk_default, context_ratio = _get_polish_chunk_config()

    # Split frontmatter from body
    parts = markdown.split("---", 2)
    if len(parts) >= 3 and not parts[0].strip():
        frontmatter = parts[1]
        body = parts[2]
    else:
        frontmatter = None
        body = markdown

    preamble, sections = _split_body_sections(body)

    if sections:
        non_empty = [(i, h, b.strip()) for i, (h, b) in enumerate(sections)
                     if b.strip()]
        _CHUNK_CHARS = chunk_cjk if is_cjk_dominant(body[:500]) else chunk_default
        _CONTEXT_CHARS = int(_CHUNK_CHARS * context_ratio)

        work_items: List[Tuple[Any, str]] = []
        sub_counts: Dict[int, int] = {}
        prev_tail = ""

        for _n, (i, _header, section_body) in enumerate(non_empty):
            context_note = ""
            if prev_tail:
                context_note = (
                    "\nText after [CONTEXT]...[/CONTEXT] is prior context "
                    "\u2014 do NOT include it in your output.\n\n"
                    f"[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                )

            if len(section_body) <= _CHUNK_CHARS:
                work_items.append((
                    (i,),
                    f"Polish this transcript section. "
                    f"Only fix the text \u2014 do not add commentary.\n"
                    f"{context_note}\n"
                    f"{section_body}",
                ))
                prev_tail = section_body[-_CONTEXT_CHARS:]
            else:
                chunk_tuples = _split_text_by_punctuation(
                    section_body, _CHUNK_CHARS, overlap=0)
                sub_counts[i] = len(chunk_tuples)
                for j, (_olap, chunk) in enumerate(chunk_tuples):
                    ctx = ""
                    if prev_tail:
                        ctx = (
                            "\nText after [CONTEXT]...[/CONTEXT] is prior "
                            "context \u2014 do NOT include it in your output."
                            f"\n\n[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                        )
                    work_items.append((
                        (i, j),
                        f"Polish this transcript section. "
                        f"Only fix the text \u2014 do not add commentary.\n"
                        f"{ctx}\n{chunk}",
                    ))
                    prev_tail = chunk[-_CONTEXT_CHARS:]

        results, errors = _call_claude_parallel(
            _POLISH_SYSTEM, work_items, label="section")

        polished = {}
        for _n, (i, _header, section_body) in enumerate(non_empty):
            if i in sub_counts:
                sc = sub_counts[i]
                if any((i, j) in errors for j in range(sc)):
                    msg = f"Polish failed for section {i}: keeping original"
                    print(f"  WARNING: {msg}", flush=True)
                    _log.warning(msg)
                else:
                    polished[i] = "\n\n".join(
                        results[(i, j)] for j in range(sc))
            else:
                if (i,) in errors:
                    msg = f"Polish failed for section {i}: keeping original"
                    print(f"  WARNING: {msg}", flush=True)
                    _log.warning(msg)
                else:
                    polished[i] = results[(i,)]

        polished_parts = [preamble]
        for i, (header, section_body) in enumerate(sections):
            if i in polished:
                polished_parts.append(f"{header}\n\n{polished[i]}")
            else:
                polished_parts.append(f"{header}\n{section_body}")
        polished_body = "\n\n".join(polished_parts)
    else:
        body_text = preamble.rstrip()
        paragraphs = [p for p in body_text.split("\n\n") if p.strip()]

        preamble_paras: List[str] = []
        transcript_paras: List[str] = []
        for p in paragraphs:
            stripped = p.strip()
            if (not transcript_paras and
                (stripped.startswith("#") or stripped.startswith(">") or
                 stripped.startswith("*") or stripped.startswith("<"))):
                preamble_paras.append(p)
            else:
                transcript_paras.append(p)

        full_text = "\n\n".join(transcript_paras)
        _CHUNK_CHARS = chunk_cjk if is_cjk_dominant(full_text[:500]) else chunk_default
        _CONTEXT_CHARS = int(_CHUNK_CHARS * context_ratio)

        if len(full_text) <= _CHUNK_CHARS:
            chunk_tuples = [(0, full_text)]
        else:
            chunk_tuples = _split_text_by_punctuation(
                full_text, _CHUNK_CHARS, overlap=0)

        work_items: List[Tuple[Any, str]] = []
        prev_tail = ""
        for idx, (_olap, chunk) in enumerate(chunk_tuples):
            context_note = ""
            if prev_tail:
                context_note = (
                    "\nText after [CONTEXT]...[/CONTEXT] is prior context "
                    "\u2014 do NOT include it in your output.\n\n"
                    f"[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                )
            work_items.append((
                idx,
                f"Polish this transcript section. "
                f"Only fix the text \u2014 do not add commentary.\n"
                f"{context_note}\n{chunk}",
            ))
            prev_tail = chunk[-_CONTEXT_CHARS:]

        results, errors = _call_claude_parallel(
            _POLISH_SYSTEM, work_items, label="chunk")

        if errors:
            msg = f"Polish failed for {len(errors)} chunk(s) \u2014 keeping original for those"
            print(f"  WARNING: {msg}", flush=True)
            _log.warning(msg)
        polished_chunks = []
        for idx, (_olap, chunk) in enumerate(chunk_tuples):
            if idx in results:
                polished_chunks.append(results[idx])
            else:
                polished_chunks.append(chunk)
        polished_text = "\n\n".join(polished_chunks)

        preamble_text = "\n\n".join(preamble_paras)
        polished_body = preamble_text + "\n\n" + polished_text if preamble_text else polished_text

    if frontmatter is not None:
        updated_fm = frontmatter.replace("polished: false", "polished: true")
        result = f"---{updated_fm}---\n\n{polished_body.lstrip()}"
    else:
        result = polished_body

    polished_path.write_text(result, encoding="utf-8")


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------

def _get_summarize_chunk_config() -> Tuple[int, int]:
    """Return (chunk_size_cjk, chunk_size) from config."""
    if _config:
        sc = _config.llm.summarize
        return sc.chunk_size_cjk, sc.chunk_size
    return 1500, 3000


def summarize_transcript(transcript_path: pathlib.Path,
                         summary_path: pathlib.Path) -> None:
    """Generate a Pyramid/SCQA summary. Saves to summary_path."""
    markdown = transcript_path.read_text(encoding="utf-8")
    chunk_cjk, chunk_default = _get_summarize_chunk_config()

    title = url = language = ""
    fm_match = re.search(r"^---\n(.*?)\n---", markdown, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).split("\n"):
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"')
            elif line.startswith("url:"):
                url = line.split(":", 1)[1].strip().strip('"')
            elif line.startswith("language:"):
                language = line.split(":", 1)[1].strip().strip('"')

    timestamp = datetime.now(timezone.utc).isoformat()
    template = f"""\
---
title: "Summary: {title}"
source: "transcript.md"
url: "{url}"
language: "{language}"
summarized_at: "{timestamp}"
---

# Summary: {title}

## Key Message
[One-sentence governing thought \u2014 the pyramid\u2019s apex]

## SCQA Framework
- **Situation**: [Context/background]
- **Complication**: [What changed or created tension]
- **Question**: [The question this raises]
- **Answer**: [The resolution/main argument]

## Key Points
[Grouped by theme, pyramid-style \u2014 conclusions first, then supporting details]

### [Theme 1]
- Point (supported by...)

### [Theme 2]
- Point (supported by...)

## Notable Quotes / Moments
[Include timestamps if chapter headings provide time context]"""

    parts = markdown.split("---", 2)
    if len(parts) >= 3 and not parts[0].strip():
        body_text = parts[2].strip()
    else:
        body_text = markdown

    _, sections = _split_body_sections(body_text)
    _CHUNK_CHARS = chunk_cjk if is_cjk_dominant(body_text[:500]) else chunk_default

    if sections:
        chunk_tuples = []
        for h, t in sections:
            t = t.strip()
            if not t:
                continue
            full = f"{h}\n{t}"
            if len(full) <= _CHUNK_CHARS:
                chunk_tuples.append((0, full))
            else:
                for olap, sub in _split_text_by_punctuation(t, _CHUNK_CHARS):
                    chunk_tuples.append((olap, f"{h}\n{sub}" if olap == 0 else sub))
    else:
        if len(body_text) <= _CHUNK_CHARS:
            chunk_tuples = [(0, body_text)]
        else:
            chunk_tuples = _split_text_by_punctuation(
                body_text, _CHUNK_CHARS)

    if len(chunk_tuples) <= 1:
        print("  Generating summary...", flush=True)
        user_msg = (
            f"Summarize this transcript. Use this template format:\n\n"
            f"{template}\n\n---\n\nTRANSCRIPT:\n\n{markdown}"
        )
        summary = _call_claude(_SUMMARIZE_SYSTEM, user_msg)
    else:
        work_items = [
            (i, f"Summarize this section into concise bullet points "
                f"(key arguments, facts, notable quotes). Keep the same "
                f"language as the source:\n\n{chunk}")
            for i, (_olap, chunk) in enumerate(chunk_tuples)
        ]
        results, errors = _call_claude_parallel(
            _SUMMARIZE_SYSTEM, work_items, label="summary")
        if errors:
            msg = f"Summarize failed for {len(errors)} chunk(s) \u2014 skipping those"
            print(f"  WARNING: {msg}", flush=True)
            _log.warning(msg)
        chunk_summaries = [
            results[i] for i in range(len(work_items)) if i in results
        ]
        if not chunk_summaries:
            raise LLMError("All summarize chunks failed")

        print("  Synthesizing final summary...", flush=True)
        all_chunks = "\n\n---\n\n".join(chunk_summaries)
        user_msg = (
            f"Based on these section summaries, produce a final summary "
            f"using this template:\n\n{template}\n\n---\n\n"
            f"SECTION SUMMARIES:\n\n{all_chunks}"
        )
        summary = _call_claude(_SUMMARIZE_SYSTEM, user_msg)

    summary_path.write_text(summary, encoding="utf-8")
