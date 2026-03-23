"""Text processing — CJK detection, paragraph assembly, chapter alignment."""

import re
from typing import Dict, List

from .models import Chapter, SubtitleCue

_CJK_RANGE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef"
    r"\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]"
)
_SENTENCE_END = re.compile(r"[.!?。！？]\s*$")


def is_cjk_dominant(text: str) -> bool:
    """Check if >30% of non-whitespace chars are CJK."""
    chars = re.sub(r"\s", "", text)
    if not chars:
        return False
    cjk_count = len(_CJK_RANGE.findall(chars))
    return cjk_count / len(chars) > 0.3


def cues_to_text(cues: List[SubtitleCue]) -> str:
    """Convert subtitle cues into readable paragraph text."""
    if not cues:
        return ""

    # Determine if CJK dominant
    sample = " ".join(c.text for c in cues[:50])
    cjk_mode = is_cjk_dominant(sample)
    joiner = "" if cjk_mode else " "

    paragraphs = []
    current_para = []
    sentence_count = 0
    prev_end = cues[0].start_seconds

    for cue in cues:
        # Gap-based paragraph break: >4 seconds silence
        if current_para and (cue.start_seconds - prev_end) > 4.0:
            paragraphs.append(joiner.join(current_para))
            current_para = []
            sentence_count = 0

        current_para.append(cue.text)

        if _SENTENCE_END.search(cue.text):
            sentence_count += 1

        # Sentence-count paragraph break
        if sentence_count >= 6:
            paragraphs.append(joiner.join(current_para))
            current_para = []
            sentence_count = 0

        prev_end = cue.end_seconds

    if current_para:
        paragraphs.append(joiner.join(current_para))

    return "\n\n".join(paragraphs)


def align_cues_to_chapters(cues: List[SubtitleCue],
                           chapters: List[Chapter]) -> Dict[int, List[SubtitleCue]]:
    """Assign each cue to its chapter. Single-pass O(n) merge."""
    if not chapters:
        return {0: cues}

    result: Dict[int, List[SubtitleCue]] = {i: [] for i in range(len(chapters))}
    ch_idx = 0

    for cue in cues:
        while (ch_idx < len(chapters) - 1 and
               cue.start_seconds >= chapters[ch_idx + 1].start_seconds):
            ch_idx += 1
        result[ch_idx].append(cue)

    return result
