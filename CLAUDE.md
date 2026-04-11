# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Chat Extractor is a Python CLI tool that extracts conversations from shared chat URLs using Playwright browser automation. It consolidates the conversation and code artifacts into a single markdown file, optimized for LLM consumption with ~75% token reduction.

Primary target is Claude (`https://claude.ai/share/...`). As of v1.2.0 it also supports Gemini (`https://gemini.google.com/share/...`) via a second provider entry — see the `PROVIDERS` registry in [extractor.py](src/claude_chat_extractor/extractor.py).

**Key Use Case**: Continue Claude Desktop conversations beyond the 200k token limit by extracting chat history to markdown and uploading to a new session.

## Installation & Setup

```bash
# Install globally (available from any directory)
pip install git+https://github.com/dzivkovi/claude-chat-extractor.git
patchright install chromium

# Or for development (editable mode)
pip install -e .
patchright install chromium

# Update to latest
pip install --upgrade git+https://github.com/dzivkovi/claude-chat-extractor.git
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

**`PROVIDERS` registry** — module-level dict mapping provider key (`'claude'`, `'gemini'`) to a config with `url_prefix`, `extract_messages` callable, and display `label`. This is the extension point for new providers: add one entry with the right URL prefix and a `_extract_messages_<provider>` function.

**`fetch_chat(url, format_type, work_dir, keep_html, provider='claude')`**

- Launches headless=False Chrome via Patchright (falls back to Playwright)
- Navigates to the share URL with manual CAPTCHA handling
- Waits for user input to confirm page load
- Dispatches to `PROVIDERS[provider]['extract_messages']` for message extraction
- Extracts code artifacts from `<pre><code>` blocks (shared across providers; unverified for Gemini)
- Saves intermediate files: `conversation.json`, `artifact_code_*.{ext}`, optionally `chat_complete.html`
- Returns metadata dict with message count, artifact count, and optionally `pdf_path`

**Provider-specific extraction shapes** — *structurally different, not just different selectors*:

- **Claude** (`_extract_messages_claude`): iterates a flat list of `[data-test-render-count]` containers and detects role per element via `.font-user-message` / `className.includes('user')`.
- **Gemini** (`_extract_messages_gemini`): iterates `<share-turn-viewer>` custom elements, each containing one `<user-query-content>` (user side) and one `<response-container> message-content` (model side). Strips the leading `"You said"` accessibility-label prefix from `user-query-content.innerText`.

**`consolidate_markdown(url, messages, artifacts, output_file, assistant_label='Claude')`**

- Builds a single markdown file with metadata header, artifact TOC, conversation text with role labels (`👤 User` / `🤖 {assistant_label}`), and embedded artifact code blocks
- Default cleanup: removes individual artifact files unless `--keep-artifacts`

**`main()`**

- CLI entry point with argparse; adds `--provider {claude,gemini}` flag
- Auto-detects provider from URL hostname when `--provider` is omitted (falls back to `claude` if no prefix matches)
- URL validation uses the selected provider's `url_prefix` — warns but does not block
- Orchestrates: fetch → consolidate (for markdown) or fetch → move PDF

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

- **patchright>=1.50.0**: Patched Playwright fork that bypasses Cloudflare bot detection
- Falls back to `playwright` if patchright is not installed
- Requires Chrome installed on the system (uses `channel="chrome"`)
- Browser profile persisted at `~/.claude-chat-extractor/browser_profile/`

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
- Uses persistent browser context to preserve Cloudflare `cf_clearance` cookies across runs
- With Patchright, uses real Chrome instead of bundled Chromium to avoid bot fingerprinting
- Auto-detects Cloudflare challenge pages and waits for resolution
- User must press Enter after CAPTCHA/page load confirmation
- URL validation warns if URL doesn't match `https://claude.ai/share/` pattern
- Message extraction filters out elements with <10 characters
- Code artifacts require ≥50 characters to be extracted
- All file I/O uses UTF-8 encoding explicitly
