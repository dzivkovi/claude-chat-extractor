# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Chat Extractor is a Python CLI tool that extracts conversations from Claude shared chat URLs using Playwright browser automation. It consolidates the conversation and code artifacts into a single markdown file, optimized for LLM consumption with ~75% token reduction.

**Key Use Case**: Continue Claude Desktop conversations beyond the 200k token limit by extracting chat history to markdown and uploading to a new session.

## Installation & Setup

```bash
# Install from GitHub
pip install git+https://github.com/dzivkovi/claude-chat-extractor.git

# Or install locally (if you've cloned the repo)
pip install .

# Install Playwright browser (required)
playwright install chromium
```

## Running the Tool

```bash
# Basic usage
claude-chat-extractor https://claude.ai/share/CHAT_ID

# Custom output file
claude-chat-extractor CHAT_URL -o my_conversation.md

# PDF format
claude-chat-extractor CHAT_URL -f pdf

# As Python module
python -m claude_chat_extractor https://claude.ai/share/CHAT_ID

# Keep intermediate files for debugging
claude-chat-extractor CHAT_URL --keep-artifacts --keep-html
```

## Code Architecture

### Package Structure

```
src/claude_chat_extractor/
├── __init__.py         # Package exports: fetch_chat, consolidate_markdown
├── __main__.py         # Module entry point (python -m)
└── extractor.py        # All core functionality
```

### Core Components ([extractor.py](src/claude_chat_extractor/extractor.py))

**`fetch_chat(url, work_dir, format_type, keep_html)`** ([extractor.py:23-175](src/claude_chat_extractor/extractor.py#L23-L175))
- Launches headless=False Chromium browser via Playwright
- Navigates to Claude share URL with manual CAPTCHA handling
- Waits for user input to confirm page load
- Extracts conversation messages via JavaScript evaluation
- Extracts code artifacts from `<pre><code>` blocks
- Saves intermediate files: `conversation.json`, `conversation.md`, `artifact_code_*.{ext}`, optionally `chat_complete.html`
- Returns metadata dict with artifact count and work directory

**Key Implementation Details**:
- Message extraction uses `[data-test-render-count]` selector
- Role detection checks for `.user` class or `.font-user-message` selector
- Scrolls to bottom before extraction to load lazy content
- PDF generation uses Playwright's `page.pdf()` method

**`consolidate_markdown(work_dir, output_file, keep_artifacts)`** ([extractor.py:178-287](src/claude_chat_extractor/extractor.py#L178-L287))
- Reads `conversation.md` and all `artifact_code_*.*` files
- Builds single markdown file with:
  - Metadata header (source URL, message count, artifact count)
  - Table of contents with artifact links
  - Full conversation text
  - All code artifacts embedded in fenced code blocks
- Default cleanup: removes individual artifact files unless `--keep-artifacts`

**`main()`** ([extractor.py:289-422](src/claude_chat_extractor/extractor.py#L289-L422))
- CLI entry point with argparse
- Validates URL format (expects `https://claude.ai/share/...`)
- Orchestrates: fetch → consolidate (for markdown) or fetch → move PDF
- Default outputs: `consolidated_chat.md` or `chat.pdf`
- Default work directory: `consolidated_chat/`

## Development Workflow

### Testing Changes

Since this is a browser automation tool, testing requires:
1. A valid Claude shared chat URL
2. Running the tool end-to-end
3. Verifying output markdown quality

```bash
# Run with a test URL
claude-chat-extractor https://claude.ai/share/TEST_CHAT_ID

# Keep intermediate files to inspect
claude-chat-extractor CHAT_URL --keep-artifacts --keep-html
```

### Debugging

- Use `--keep-html` to inspect the raw HTML extraction
- Use `--keep-artifacts` to see individual artifact files before consolidation
- Check `consolidated_chat/conversation.json` for message extraction data
- Playwright runs in headless=False mode, so you can see browser interactions

## Key Dependencies

- **playwright>=1.40.0**: Browser automation for fetching chat content
- Requires manual Chromium installation via `playwright install chromium`

## Output Files

**Default workflow** (markdown format):
- Working directory: `consolidated_chat/`
- Intermediate: `conversation.json`, `conversation.md`, `artifact_code_*.{ext}`
- Final output: `consolidated_chat.md` (single consolidated file)
- Cleanup: Intermediate files deleted unless `--keep-artifacts` specified

**PDF workflow**:
- Working directory: `consolidated_chat/`
- Output: `chat.pdf` (or custom path via `-o`)
- No consolidation step (PDF is final output)

## Important Behavioral Notes

- Browser runs in non-headless mode to allow CAPTCHA handling
- User must press Enter after CAPTCHA/page load confirmation
- URL validation warns if URL doesn't match `https://claude.ai/share/` pattern
- Message extraction filters out elements with <10 characters
- Code artifacts require ≥50 characters to be extracted
- All file I/O uses UTF-8 encoding explicitly
