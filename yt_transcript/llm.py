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
from typing import Any, Dict, List, Optional, Tuple

from .config import OUTPUT_DIR
from .exceptions import LLMError
from .text import is_cjk_dominant

# ---------------------------------------------------------------------------
# File logger — writes to yt_transcripts/llm.log
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
# Model detection and caching
# ---------------------------------------------------------------------------

_MODEL_PREFERENCE = ["opus", "sonnet", "haiku"]

# Cached state (set once per process by validate_llm_setup)
_claude_path: Optional[str] = None
_model_alias: Optional[str] = None
_fallback_alias: Optional[str] = None
_model_lock = threading.Lock()
_max_workers: int = 3


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
    """Set model directly from --model flag, skipping probing."""
    global _model_alias, _fallback_alias

    if model_str == _model_alias:
        return  # Already set

    alias_indices = {a: i for i, a in enumerate(_MODEL_PREFERENCE)}
    if model_str in alias_indices:
        idx = alias_indices[model_str]
        _model_alias = model_str
        _fallback_alias = _MODEL_PREFERENCE[idx + 1] if idx + 1 < len(_MODEL_PREFERENCE) else None
    else:
        # Full model name or unknown — use as-is, no fallback
        _model_alias = model_str
        _fallback_alias = None

    print(f"  Using model: {_model_alias}"
          + (f" (fallback: {_fallback_alias})" if _fallback_alias else ""),
          flush=True)


def validate_llm_setup(model_override: Optional[str] = None) -> None:
    """Check LLM prerequisites early. Raises LLMError if not ready."""
    global _claude_path, _model_alias, _fallback_alias

    _claude_path = _find_claude_cli()

    if model_override:
        _set_model_override(model_override)
    else:
        # Start with the best model; _call_claude will auto-fallback on failure
        _model_alias = _MODEL_PREFERENCE[0]  # opus
        _fallback_alias = _MODEL_PREFERENCE[1] if len(_MODEL_PREFERENCE) > 1 else None
        print(f"  Using model: {_model_alias}"
              + (f" (fallback: {_fallback_alias})" if _fallback_alias else ""),
              flush=True)


def get_models() -> tuple:
    """Return (primary_model, secondary_model) based on auto-detection.

    Primary is the best available (for summarize), secondary is the next
    best (for polish).  Either may be None if not yet initialized.
    """
    return _model_alias, _fallback_alias


def set_model(model_str: str) -> None:
    """Switch the active model."""
    if not _claude_path:
        raise LLMError("LLM not initialized. Call validate_llm_setup() first.")
    if model_str == _model_alias:
        return
    _set_model_override(model_str)


# ---------------------------------------------------------------------------
# Claude CLI call
# ---------------------------------------------------------------------------

def _run_claude_cli(model: str, system: str, user_msg: str,
                     fallback: Optional[str] = None,
                     ) -> subprocess.CompletedProcess:
    """Run a single claude CLI call. Returns CompletedProcess.

    Each call starts a fresh session — no history accumulation.
    Output is JSON so we can extract the result text.
    """
    # Write system prompt to temp file to avoid Windows command-line encoding issues
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
            timeout=600,
        )
    finally:
        pathlib.Path(system_file).unlink(missing_ok=True)


def _parse_json_output(stdout: str) -> str:
    """Extract text result from JSON output."""
    try:
        data = _json.loads(stdout)
        return data.get("result", "")
    except (_json.JSONDecodeError, TypeError):
        # Fallback: treat as plain text (shouldn't happen with --output-format json)
        return stdout.strip()


def _has_real_error(result: subprocess.CompletedProcess) -> bool:
    """Check if a non-zero exit is a real error."""
    if result.returncode == 0:
        return False
    return True


_ERROR_PATTERNS = ["API Error:", "You're out of extra usage", "rate limit", "overloaded"]


def _is_error_content(text: str) -> Optional[str]:
    """Return matched pattern if text is an error message, else None."""
    head = text[:200]
    for p in _ERROR_PATTERNS:
        if p.lower() in head.lower():
            return p
    return None


def _call_claude(system: str, user_msg: str) -> str:
    """Call claude CLI with automatic model fallback. Thread-safe."""
    global _model_alias, _fallback_alias

    if not _claude_path or not _model_alias:
        raise LLMError("LLM not initialized. Call validate_llm_setup() first.")

    # Snapshot model state under lock (microseconds)
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
        raise LLMError("Claude CLI timed out after 600 seconds")
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
        # Primary model failed — advance fallback chain under lock
        stderr = result.stderr.strip()
        with _model_lock:
            msg = (f"Model {model} failed ({stderr[:80]}). "
                   f"Switching to {fallback}...")
            print(f"  {msg}", flush=True)
            _log.warning(msg)

            idx = _MODEL_PREFERENCE.index(fallback) if fallback in _MODEL_PREFERENCE else -1
            _model_alias = fallback
            _fallback_alias = _MODEL_PREFERENCE[idx + 1] if idx + 1 < len(_MODEL_PREFERENCE) else None
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
            raise LLMError("Claude CLI timed out after 600 seconds")

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
    """Run multiple _call_claude calls in parallel.

    Returns ``(results, errors)`` dicts keyed by work item key.
    Caller decides error policy (keep original, abort, etc.).
    """
    total = len(work_items)

    # Fast path: single item, skip thread pool
    if total == 1:
        key, user_msg = work_items[0]
        try:
            return {key: _call_claude(system, user_msg)}, {}
        except LLMError as e:
            return {}, {key: e}

    results: Dict[Any, str] = {}
    errors: Dict[Any, LLMError] = {}
    workers = min(_max_workers, total)
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
    """Split text into overlapping chunks at punctuation boundaries.

    Returns list of ``(overlap_len, chunk)`` tuples.  For chunks after the
    first, the chunk **includes** *overlap* characters from the end of the
    previous chunk so the model sees complete sentences across boundaries.
    ``overlap_len`` tells the caller how many leading characters to trim from
    the polished output to avoid duplication.
    """
    # First pass: find split points
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
            # Tertiary: split at spaces (common in Whisper CJK output)
            for j in range(end, max(pos, end - 500), -1):
                if text[j] == ' ':
                    best = j + 1
                    break
        if best == -1:
            best = end
        splits.append(best)
        pos = best
    splits.append(len(text))

    # Second pass: build chunks with overlap baked in
    result: List[Tuple[int, str]] = []
    for i in range(len(splits) - 1):
        if i == 0:
            result.append((0, text[splits[0]:splits[1]]))
        else:
            # Start overlap chars before the split point
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

def polish_transcript(unpolished_path: pathlib.Path,
                      polished_path: pathlib.Path) -> None:
    """Polish a transcript file via Claude CLI. Saves to polished_path."""
    markdown = unpolished_path.read_text(encoding="utf-8")

    # Split frontmatter from body
    parts = markdown.split("---", 2)
    if len(parts) >= 3 and not parts[0].strip():
        frontmatter = parts[1]
        body = parts[2]
    else:
        frontmatter = None
        body = markdown

    # Split body into preamble (metadata) and chapter sections
    preamble, sections = _split_body_sections(body)

    if sections:
        non_empty = [(i, h, b.strip()) for i, (h, b) in enumerate(sections)
                     if b.strip()]
        _CHUNK_CHARS = 500 if is_cjk_dominant(body[:500]) else 1_000
        _CONTEXT_CHARS = _CHUNK_CHARS // 10  # 10% of chunk size

        # Phase 1: Pre-compute all work items (pure string ops, no LLM)
        work_items: List[Tuple[Any, str]] = []  # [(key, user_msg), ...]
        sub_counts: Dict[int, int] = {}  # section_idx -> num sub-chunks
        prev_tail = ""

        for _n, (i, _header, section_body) in enumerate(non_empty):
            context_note = ""
            if prev_tail:
                context_note = (
                    "\nText after [CONTEXT]...[/CONTEXT] is prior context "
                    "— do NOT include it in your output.\n\n"
                    f"[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                )

            if len(section_body) <= _CHUNK_CHARS:
                work_items.append((
                    (i,),
                    f"Polish this transcript section. "
                    f"Only fix the text — do not add commentary.\n"
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
                            "context — do NOT include it in your output."
                            f"\n\n[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                        )
                    work_items.append((
                        (i, j),
                        f"Polish this transcript section. "
                        f"Only fix the text — do not add commentary.\n"
                        f"{ctx}\n{chunk}",
                    ))
                    prev_tail = chunk[-_CONTEXT_CHARS:]

        # Phase 2: Parallel dispatch
        results, errors = _call_claude_parallel(
            _POLISH_SYSTEM, work_items, label="section")

        # Phase 3: Reassemble
        polished = {}
        for _n, (i, _header, section_body) in enumerate(non_empty):
            if i in sub_counts:
                # Multi-chunk section — check all sub-chunks
                sc = sub_counts[i]
                if any((i, j) in errors for j in range(sc)):
                    msg = f"Polish failed for section {i}: keeping original"
                    print(f"  WARNING: {msg}", flush=True)
                    _log.warning(msg)
                else:
                    polished[i] = "\n\n".join(
                        results[(i, j)] for j in range(sc))
            else:
                # Single-chunk section
                if (i,) in errors:
                    msg = f"Polish failed for section {i}: keeping original"
                    print(f"  WARNING: {msg}", flush=True)
                    _log.warning(msg)
                else:
                    polished[i] = results[(i,)]

        # Reassemble with headers
        polished_parts = [preamble]
        for i, (header, section_body) in enumerate(sections):
            if i in polished:
                polished_parts.append(f"{header}\n\n{polished[i]}")
            else:
                polished_parts.append(f"{header}\n{section_body}")
        polished_body = "\n\n".join(polished_parts)
    else:
        # No chapter headers — split text by character with punctuation snapping
        body_text = preamble.rstrip()
        paragraphs = [p for p in body_text.split("\n\n") if p.strip()]

        # Separate preamble lines from transcript text
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
        _CHUNK_CHARS = 500 if is_cjk_dominant(full_text[:500]) else 1_000
        _CONTEXT_CHARS = _CHUNK_CHARS // 10  # 10% of chunk size

        if len(full_text) <= _CHUNK_CHARS:
            chunk_tuples = [(0, full_text)]
        else:
            chunk_tuples = _split_text_by_punctuation(
                full_text, _CHUNK_CHARS, overlap=0)

        # Phase 1: Pre-compute all work items
        work_items: List[Tuple[Any, str]] = []
        prev_tail = ""
        for idx, (_olap, chunk) in enumerate(chunk_tuples):
            context_note = ""
            if prev_tail:
                context_note = (
                    "\nText after [CONTEXT]...[/CONTEXT] is prior context "
                    "— do NOT include it in your output.\n\n"
                    f"[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                )
            work_items.append((
                idx,
                f"Polish this transcript section. "
                f"Only fix the text — do not add commentary.\n"
                f"{context_note}\n{chunk}",
            ))
            prev_tail = chunk[-_CONTEXT_CHARS:]

        # Phase 2: Parallel dispatch
        results, errors = _call_claude_parallel(
            _POLISH_SYSTEM, work_items, label="chunk")

        # Phase 3: Reassemble (failed chunks keep original text)
        if errors:
            msg = f"Polish failed for {len(errors)} chunk(s) — keeping original for those"
            print(f"  WARNING: {msg}", flush=True)
            _log.warning(msg)
        polished_chunks = []
        for idx, (_olap, chunk) in enumerate(chunk_tuples):
            if idx in results:
                polished_chunks.append(results[idx])
            else:
                polished_chunks.append(chunk)  # keep original
        polished_text = "\n\n".join(polished_chunks)

        preamble_text = "\n\n".join(preamble_paras)
        polished_body = preamble_text + "\n\n" + polished_text if preamble_text else polished_text

    # Reassemble with updated frontmatter
    if frontmatter is not None:
        updated_fm = frontmatter.replace("polished: false", "polished: true")
        result = f"---{updated_fm}---\n\n{polished_body.lstrip()}"
    else:
        result = polished_body

    polished_path.write_text(result, encoding="utf-8")


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------

def summarize_transcript(transcript_path: pathlib.Path,
                         summary_path: pathlib.Path) -> None:
    """Generate a Pyramid/SCQA summary. Saves to summary_path."""
    markdown = transcript_path.read_text(encoding="utf-8")

    # Extract metadata from frontmatter
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

    # Split body into chunks for focused summarization
    # Strip frontmatter for body splitting (same pattern as polish_transcript)
    parts = markdown.split("---", 2)
    if len(parts) >= 3 and not parts[0].strip():
        body_text = parts[2].strip()
    else:
        body_text = markdown

    _, sections = _split_body_sections(body_text)
    _CHUNK_CHARS = 1_500 if is_cjk_dominant(body_text[:500]) else 3_000

    if sections:
        # Chunk large sections so no single call is oversized
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
        # No sections — split by character with punctuation snapping + overlap
        if len(body_text) <= _CHUNK_CHARS:
            chunk_tuples = [(0, body_text)]
        else:
            chunk_tuples = _split_text_by_punctuation(
                body_text, _CHUNK_CHARS)

    if len(chunk_tuples) <= 1:
        # Short enough for single pass
        print("  Generating summary...", flush=True)
        user_msg = (
            f"Summarize this transcript. Use this template format:\n\n"
            f"{template}\n\n---\n\nTRANSCRIPT:\n\n{markdown}"
        )
        summary = _call_claude(_SUMMARIZE_SYSTEM, user_msg)
    else:
        # Summarize chunks in parallel
        work_items = [
            (i, f"Summarize this section into concise bullet points "
                f"(key arguments, facts, notable quotes). Keep the same "
                f"language as the source:\n\n{chunk}")
            for i, (_olap, chunk) in enumerate(chunk_tuples)
        ]
        results, errors = _call_claude_parallel(
            _SUMMARIZE_SYSTEM, work_items, label="summary")
        if errors:
            msg = f"Summarize failed for {len(errors)} chunk(s) — skipping those"
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
