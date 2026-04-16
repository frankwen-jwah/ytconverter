"""LLM-based post-processing — polish via Azure OpenAI."""

import logging
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .config import OUTPUT_DIR
from .exceptions import LLMError
from .text import is_cjk_dominant

if TYPE_CHECKING:
    from .config import Config

# ---------------------------------------------------------------------------
# File logger — writes to content/llm.log
# ---------------------------------------------------------------------------

_log = logging.getLogger("content_extractor.llm")
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


# ---------------------------------------------------------------------------
# Module state — initialized by init_llm()
# ---------------------------------------------------------------------------

_config: Optional["Config"] = None
_initialized = False


def init_llm(config: "Config") -> None:
    """Initialize LLM module — delegates to llm_backend."""
    global _config, _initialized

    _config = config

    from . import llm_backend
    llm_backend.init_backend(config)
    _initialized = True


# ---------------------------------------------------------------------------
# LLM call wrapper
# ---------------------------------------------------------------------------

def _call_llm(system: str, user_msg: str,
              model: Optional[str] = None) -> str:
    """Call Azure OpenAI via llm_backend. Thread-safe."""
    if not _initialized:
        raise LLMError("LLM not initialized. Call init_llm() first.")

    from . import llm_backend

    input_chars = len(user_msg)
    sys_chars = len(system)
    msg = f"calling Azure OpenAI | system: {sys_chars} chars | input: {input_chars} chars"
    print(f"    [llm] {msg}", flush=True)
    _log.info(msg)
    _log.debug("input preview: %s", user_msg[:300].replace("\n", "\\n"))
    t0 = time.time()

    try:
        output = llm_backend.chat_completion(system, user_msg, model=model)
    except LLMError:
        raise
    except Exception as e:
        elapsed = time.time() - t0
        msg = f"LLM call failed after {elapsed:.1f}s: {e}"
        print(f"    [llm] ERROR: {msg}", flush=True)
        _log.error(msg)
        raise LLMError(msg) from e

    elapsed = time.time() - t0
    msg = f"returned in {elapsed:.1f}s | output: {len(output)} chars"
    print(f"    [llm] {msg}", flush=True)
    _log.info(msg)
    _log.debug("output preview: %s", output[:300].replace("\n", "\\n"))
    return output


def _call_llm_parallel(
    system: str,
    work_items: List[Tuple[Any, str]],
    label: str = "chunk",
    model: Optional[str] = None,
) -> Tuple[Dict[Any, str], Dict[Any, LLMError]]:
    """Run multiple _call_llm calls in parallel."""
    total = len(work_items)
    max_workers = _config.llm.max_workers if _config else 8

    if total == 1:
        key, user_msg = work_items[0]
        try:
            return {key: _call_llm(system, user_msg, model=model)}, {}
        except LLMError as e:
            return {}, {key: e}

    results: Dict[Any, str] = {}
    errors: Dict[Any, LLMError] = {}
    workers = min(max_workers, total)
    print(f"  Processing {total} {label}s ({workers} workers)...", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_key = {
            executor.submit(_call_llm, system, user_msg, model=model): key
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
            if text[j] in '\u3002\uff01\uff1f.!?':
                best = j + 1
                break
        if best == -1:
            for j in range(end, max(pos, end - 500), -1):
                if text[j] in '\uff0c\u3001\uff1b\uff1a,;:':
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
                      polished_path: pathlib.Path,
                      model: Optional[str] = None) -> None:
    """Polish a transcript file via Azure OpenAI. Saves to polished_path."""
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

        results, errors = _call_llm_parallel(
            _POLISH_SYSTEM, work_items, label="section", model=model)

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

        results, errors = _call_llm_parallel(
            _POLISH_SYSTEM, work_items, label="chunk", model=model)

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
