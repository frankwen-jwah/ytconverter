"""LLM-based post-processing — polish and summarize via Claude CLI."""

import json as _json
import logging
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

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
        return  # Already set — skip print and session reset

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


def set_model(model_str: str) -> None:
    """Switch the active model. Resets session since model changed."""
    if not _claude_path:
        raise LLMError("LLM not initialized. Call validate_llm_setup() first.")
    if model_str == _model_alias:
        return  # Already set — skip print and session reset
    _set_model_override(model_str)
    reset_session()  # Different model → new session


# ---------------------------------------------------------------------------
# Claude CLI call
# ---------------------------------------------------------------------------

def _run_claude_cli(model: str, system: str, user_msg: str,
                     fallback: Optional[str] = None,
                     session_id: Optional[str] = None,
                     ) -> subprocess.CompletedProcess:
    """Run a single claude CLI call. Returns CompletedProcess.

    If *session_id* is given the call resumes that session (prompt caching).
    Output is always JSON so we can extract session_id for reuse.
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
        if session_id:
            cmd.extend(["--resume", session_id])
        if fallback:
            cmd.extend(["--fallback-model", fallback])

        return subprocess.run(
            cmd,
            input=user_msg,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=1200,
        )
    finally:
        pathlib.Path(system_file).unlink(missing_ok=True)


def _parse_json_output(stdout: str) -> Tuple[str, Optional[str]]:
    """Extract text result and session_id from JSON output."""
    try:
        data = _json.loads(stdout)
        text = data.get("result", "")
        sid = data.get("session_id")
        return text, sid
    except (_json.JSONDecodeError, TypeError):
        # Fallback: treat as plain text (shouldn't happen with --output-format json)
        return stdout.strip(), None


def _has_real_error(result: subprocess.CompletedProcess) -> bool:
    """Check if a non-zero exit is a real error or just a hook failure."""
    if result.returncode == 0:
        return False
    # If we got output, the LLM call succeeded — hook failures don't matter
    if result.stdout.strip():
        return False
    return True


# Session ID for reuse across sequential _call_claude calls
_session_id: Optional[str] = None


def reset_session() -> None:
    """Reset the cached session so the next call starts fresh."""
    global _session_id
    _session_id = None


def _call_claude(system: str, user_msg: str) -> str:
    """Call claude CLI with automatic model fallback and session reuse."""
    global _claude_path, _model_alias, _fallback_alias, _session_id

    if not _claude_path or not _model_alias:
        raise LLMError("LLM not initialized. Call validate_llm_setup() first.")

    input_chars = len(user_msg)
    sys_chars = len(system)
    session_tag = f" | session: {_session_id[:8]}..." if _session_id else ""
    msg = (f"calling {_model_alias} | system: {sys_chars} chars | "
           f"input: {input_chars} chars{session_tag}")
    print(f"    [llm] {msg}", flush=True)
    _log.info(msg)
    _log.debug("input preview: %s", user_msg[:300].replace("\n", "\\n"))
    t0 = time.time()

    try:
        result = _run_claude_cli(
            _model_alias, system, user_msg, _fallback_alias,
            session_id=_session_id)
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        msg = f"TIMEOUT after {elapsed:.0f}s"
        print(f"    [llm] {msg}", flush=True)
        _log.error(msg)
        raise LLMError("Claude CLI timed out after 1200 seconds")
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

    if _has_real_error(result) and _fallback_alias:
        # Primary model failed — try fallback
        stderr = result.stderr.strip()
        msg = (f"Model {_model_alias} failed ({stderr[:80]}). "
               f"Switching to {_fallback_alias}...")
        print(f"  {msg}", flush=True)
        _log.warning(msg)

        # Advance the fallback chain
        old_fallback = _fallback_alias
        idx = _MODEL_PREFERENCE.index(_fallback_alias) if _fallback_alias in _MODEL_PREFERENCE else -1
        _model_alias = old_fallback
        _fallback_alias = _MODEL_PREFERENCE[idx + 1] if idx + 1 < len(_MODEL_PREFERENCE) else None

        t0 = time.time()
        msg = f"retrying with {_model_alias}..."
        print(f"    [llm] {msg}", flush=True)
        _log.info(msg)
        try:
            result = _run_claude_cli(
                _model_alias, system, user_msg, _fallback_alias,
                session_id=_session_id)
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            msg = f"retry TIMEOUT after {elapsed:.0f}s"
            print(f"    [llm] {msg}", flush=True)
            _log.error(msg)
            raise LLMError("Claude CLI timed out after 1200 seconds")

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

    output, new_sid = _parse_json_output(raw)
    if new_sid:
        if _session_id != new_sid:
            _log.info("session started: %s", new_sid)
        _session_id = new_sid

    if not output:
        _log.error("Claude CLI JSON had empty result field")
        raise LLMError("Claude CLI returned empty output")

    msg = f"output: {len(output)} chars | session: {_session_id or 'none'}"
    print(f"    [llm] {msg}", flush=True)
    _log.info(msg)
    _log.debug("output preview: %s", output[:300].replace("\n", "\\n"))
    return output


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

    # Reset session so polish gets a fresh one (reused across chunks)
    reset_session()

    if sections:
        # Polish each chapter section sequentially (session reused)
        non_empty = [(i, h, b.strip()) for i, (h, b) in enumerate(sections)
                     if b.strip()]
        total = len(non_empty)
        polished = {}
        _CHUNK_CHARS = 1_500 if is_cjk_dominant(body[:500]) else 3_000
        prev_tail = ""  # trailing context from previous section/chunk

        for n, (i, header, section_body) in enumerate(non_empty, 1):
            context_note = ""
            if prev_tail:
                context_note = (
                    "\nText after [CONTEXT]...[/CONTEXT] is prior context "
                    "— do NOT include it in your output.\n\n"
                    f"[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                )

            if len(section_body) <= _CHUNK_CHARS:
                # Small section — polish in one call
                print(f"  Polishing section {n}/{total}...", flush=True)
                polished[i] = _call_claude(
                    _POLISH_SYSTEM,
                    f"Polish this transcript section. "
                    f"Only fix the text — do not add commentary.\n"
                    f"{context_note}\n"
                    f"{section_body}",
                )
                prev_tail = section_body[-200:]
            else:
                # Large section — split into sub-chunks (no overlap;
                # context is provided via [CONTEXT] markers instead)
                chunk_tuples = _split_text_by_punctuation(
                    section_body, _CHUNK_CHARS, overlap=0)
                sub_total = len(chunk_tuples)
                sub_polished = []
                for j, (_olap, chunk) in enumerate(chunk_tuples, 1):
                    print(f"  Polishing section {n}/{total} "
                          f"chunk {j}/{sub_total}...", flush=True)
                    # Build context for chunks after the first
                    ctx = ""
                    if prev_tail:
                        ctx = (
                            "\nText after [CONTEXT]...[/CONTEXT] is prior "
                            "context — do NOT include it in your output."
                            f"\n\n[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                        )
                    result = _call_claude(
                        _POLISH_SYSTEM,
                        f"Polish this transcript section. "
                        f"Only fix the text — do not add commentary.\n"
                        f"{ctx}\n{chunk}",
                    )
                    sub_polished.append(result)
                    prev_tail = chunk[-200:]
                polished[i] = "\n\n".join(sub_polished)

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
        _CHUNK_CHARS = 1_500 if is_cjk_dominant(full_text[:500]) else 3_000

        if len(full_text) <= _CHUNK_CHARS:
            chunk_tuples = [(0, full_text)]
        else:
            chunk_tuples = _split_text_by_punctuation(
                full_text, _CHUNK_CHARS, overlap=0)

        # Polish chunks sequentially (session reused across calls)
        total = len(chunk_tuples)
        polished_chunks = []
        prev_tail = ""
        for i, (_olap, chunk) in enumerate(chunk_tuples, 1):
            print(f"  Polishing chunk {i}/{total}...", flush=True)
            context_note = ""
            if prev_tail:
                context_note = (
                    "\nText after [CONTEXT]...[/CONTEXT] is prior context "
                    "— do NOT include it in your output.\n\n"
                    f"[CONTEXT]{prev_tail}[/CONTEXT]\n\n"
                )
            polished = _call_claude(
                _POLISH_SYSTEM,
                f"Polish this transcript section. "
                f"Only fix the text — do not add commentary.\n"
                f"{context_note}\n{chunk}",
            )
            polished_chunks.append(polished)
            prev_tail = chunk[-200:]
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

    # Reset session so summarize gets a fresh one (reused across chunks)
    reset_session()

    if len(chunk_tuples) <= 1:
        # Short enough for single pass
        print("  Generating summary...", flush=True)
        user_msg = (
            f"Summarize this transcript. Use this template format:\n\n"
            f"{template}\n\n---\n\nTRANSCRIPT:\n\n{markdown}"
        )
        summary = _call_claude(_SUMMARIZE_SYSTEM, user_msg)
    else:
        # Summarize each chunk sequentially (session reused)
        total = len(chunk_tuples)
        chunk_summaries = []
        for i, (olap, chunk) in enumerate(chunk_tuples, 1):
            print(f"  Summarizing section {i}/{total}...", flush=True)
            # For summarize, overlap gives context but each summary is
            # independent — no need to trim (summaries don't duplicate)
            chunk_summaries.append(_call_claude(
                _SUMMARIZE_SYSTEM,
                f"Summarize this section into concise bullet points "
                f"(key arguments, facts, notable quotes). Keep the same "
                f"language as the source:\n\n{chunk}",
            ))

        print("  Synthesizing final summary...", flush=True)
        all_chunks = "\n\n---\n\n".join(chunk_summaries)
        user_msg = (
            f"Based on these section summaries, produce a final summary "
            f"using this template:\n\n{template}\n\n---\n\n"
            f"SECTION SUMMARIES:\n\n{all_chunks}"
        )
        summary = _call_claude(_SUMMARIZE_SYSTEM, user_msg)

    summary_path.write_text(summary, encoding="utf-8")
