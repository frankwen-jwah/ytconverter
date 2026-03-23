Extract YouTube video transcript(s) to Markdown.

Usage: /yt-transcript <URL(s) or description>

## Instructions

Parse the user's input from $ARGUMENTS for YouTube URLs and intent, then run the extraction pipeline.

### Step 1: Build the command

Base command: `python3 /workspace/yt_transcript.py`

Add flags based on user intent:
- If user mentions "member", "paid", "subscriber", or "membership" content → add `--cookies-from-browser chrome`
- If user specifies a language → add `--lang <CODE>`
- If user says "no chapters" or "flat" → add `--no-chapters`
- If user says "description" → add `--include-description`
- If user says "polish" or "clean up" → add `--polish`
- If user says "summarize", "summary", or "key points" → add `--summarize`
- If user provides a file path → add `--file <path>`
- If no auth flags but video fails with auth error → retry with `--cookies-from-browser chrome`

### Step 2: Run the command

```bash
python3 /workspace/yt_transcript.py [flags] [URLs]
```

Output goes to a timestamped folder: `yt_transcripts/{date}_{slug}_{timestamp}/`

### Step 3: Polish (if --polish was used)

If `--polish` was requested, find all `transcript.unpolished.md` files in the output folders and for each one:

1. Read the file content
2. Split by `## ` chapter headers (or process as one block if no chapters)
3. For each section, apply these fixes while preserving the original language:
   - Fix punctuation and capitalization
   - Fix obvious speech-recognition errors (e.g., homophones, word boundaries)
   - For CJK text: fix punctuation placement, remove spurious spaces
   - Do NOT translate — keep the original language
   - Do NOT change meaning — only fix formatting artifacts
4. Update frontmatter: change `polished: false` to `polished: true`
5. Reassemble the markdown with the fixed sections
6. Write the polished version as `transcript.md` in the same folder
7. Delete the `transcript.unpolished.md` file

### Step 4: Summarize (if --summarize was used)

If `--summarize` was requested, find each output folder's `transcript.md` and generate a summary:

1. Read `transcript.md` — check its size first:
   - **Short transcript** (under 800 lines): read the whole file and summarize in one pass
   - **Long transcript** (800+ lines): use chunked summarization:
     a. Split the transcript by `## ` chapter headers (or into ~600-line chunks if no chapters)
     b. Summarize each chunk into bullet points (key arguments, facts, quotes)
     c. Read all chunk summaries together and produce the final `summary.md`
2. Read the `language` field from the YAML frontmatter
3. Generate `summary.md` **in the same language as the transcript** using this template:

```markdown
---
title: "Summary: {video title}"
source: "transcript.md"
url: "{video url}"
language: "{language}"
summarized_at: "{ISO timestamp}"
---

# Summary: {Video Title}

## Key Message
[One-sentence governing thought — the pyramid's apex]

## SCQA Framework
- **Situation**: [Context/background]
- **Complication**: [What changed or created tension]
- **Question**: [The question this raises]
- **Answer**: [The resolution/main argument]

## Key Points
[Grouped by theme, pyramid-style — conclusions first, then supporting details]

### [Theme 1]
- Point (supported by...)

### [Theme 2]
- Point (supported by...)

## Notable Quotes / Moments
[Include timestamps if chapter headings provide time context]
```

4. Write `summary.md` into the same folder as `transcript.md`

### Step 5: Report results

Show the user:
- Number of videos processed
- Output folder path(s)
- What was generated (transcript / polished / summary)
- Any errors encountered
