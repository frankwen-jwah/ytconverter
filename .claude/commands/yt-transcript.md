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
- If user provides a file path → add `--file <path>`
- If no auth flags but video fails with auth error → retry with `--cookies-from-browser chrome`

### Step 2: Run the command

```bash
python3 /workspace/yt_transcript.py [flags] [URLs]
```

### Step 3: Polish (if --polish was used)

If `--polish` was requested, find all `.unpolished.md` files in the output directory and for each one:

1. Read the file content
2. Split by `## ` chapter headers (or process as one block if no chapters)
3. For each section, apply these fixes while preserving the original language:
   - Fix punctuation and capitalization
   - Fix obvious speech-recognition errors (e.g., homophones, word boundaries)
   - For CJK text: fix punctuation placement, remove spurious spaces
   - Do NOT translate — keep the original language
   - Do NOT change meaning — only fix formatting artifacts
4. Reassemble the markdown with the fixed sections
5. Write the polished version as the final `.md` file (same name without `.unpolished`)
6. Delete the `.unpolished.md` file

### Step 4: Report results

Show the user:
- Number of videos processed
- Output file path(s)
- Any errors encountered
- Offer to read/summarize the transcript if they want
