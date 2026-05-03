# claude-chat-extractor

Extend your Claude Desktop context. Continue conversations beyond 200k tokens.

## The Problem

Claude Desktop conversations hit a context limit. When you reach 200k tokens, you can't continue in the same chat.

## The Solution

Extract your conversation to markdown (75% smaller), upload to a new chat, and continue seamlessly.

## Quick Start

```bash
# 1. Install (requires Chrome browser on your system)
pip install git+https://github.com/dzivkovi/claude-chat-extractor.git
patchright install chromium

# 2. Extract your conversation (browser will open, complete CAPTCHA if needed)
claude-chat-extractor https://claude.ai/share/CHAT_ID

# 3. The tool prints the resume prompt when done. Open a new Claude Desktop chat,
#    attach consolidated_chat.md, and paste the prompt.
```

Your 200k token conversation becomes ~50k tokens (75% reduction) while preserving all content and code artifacts.

**Note:** This tool extracts from Claude shared chat URLs and is designed for continuing conversations in Claude Desktop. Gemini share URLs (`gemini.google.com/share/...`) are also supported — see [Gemini support](#gemini-support-experimental) below.

## Why Patchright? (Cloudflare Bot Detection)

In early 2025, Anthropic adopted stronger Cloudflare bot detection on `claude.ai`, including Turnstile challenges and CDP (Chrome DevTools Protocol) fingerprinting. This was in response to large-scale scraping activity, where third-party AI providers were using proxies and automated browsers to harvest training data from Claude conversations.

These security improvements broke the original Playwright-based extractor. Standard Playwright is trivially detectable because:

1. **CDP leak**: Playwright sends a `Runtime.enable` command over the Chrome DevTools Protocol. Cloudflare's detection checks for this specific signal.
2. **`navigator.webdriver` flag**: Playwright's bundled Chromium sets `navigator.webdriver=true`, a standard automation indicator.
3. **Bundled Chromium fingerprint**: Playwright ships its own Chromium build with a distinct fingerprint that differs from a real Chrome installation.

**Patchright** is a drop-in Playwright fork that patches these leaks at the C++ level, making the browser indistinguishable from a manually-opened Chrome window. Combined with a **persistent browser context** (cookies survive between runs), the tool now:

- Uses your real installed Chrome (`channel="chrome"`) instead of bundled Chromium
- Patches the `Runtime.enable` CDP leak at the protocol level
- Stores browser profile and cookies at `~/.claude-chat-extractor/browser_profile/`
- Preserves Cloudflare `cf_clearance` cookies, so you solve CAPTCHA once and skip it on future runs

### About the Yellow Warning Bar

When Chrome opens, you may see a yellow bar saying:

> "You are using an unsupported command-line flag: --disable-blink-features=AutomationControlled"

**This is expected and beneficial.** That flag is a stealth measure injected by Patchright to prevent `navigator.webdriver` detection. The warning is purely cosmetic — Chrome shows it for any non-standard flag. Do not attempt to suppress it, as doing so would disable the stealth feature and cause Cloudflare to block access.

## Installation

```bash
# Install globally (available from any directory)
pip install git+https://github.com/dzivkovi/claude-chat-extractor.git

# Install Patchright browser (requires Chrome installed on system)
patchright install chromium
```

This installs the `claude-chat-extractor` command globally in your Python environment. You can run it from any directory — the current working directory does not matter.

### Check which version you have

```bash
claude-chat-extractor --version
```

Prints the installed version and description, for example:

```text
claude-chat-extractor 1.2.0
Extract and consolidate shared Claude and Gemini conversations
```

This is the fastest way to confirm an upgrade landed, or to report what you're running when filing a bug.

### Updating to the latest version

When new commits are pushed to `main`, rerun the install with the `--upgrade` flag — pip will re-fetch the repo from GitHub and replace your existing installation in place:

```bash
pip install --upgrade git+https://github.com/dzivkovi/claude-chat-extractor.git
```

Then verify the new version:

```bash
claude-chat-extractor --version
```

Without `--upgrade`, pip sees the package is "already installed" and does nothing, so the flag is load-bearing — it's what makes the command re-install instead of skip.

### Pinning to a specific release

If you want to lock to an exact version (for reproducibility, or to roll back after a bad release), append `@<tag>` to the install URL:

```bash
# Install exactly v1.2.0 regardless of what's on main
pip install git+https://github.com/dzivkovi/claude-chat-extractor.git@v1.2.0
```

Tag names come from the project's git tags; see `git tag -l` in the repo or the [GitHub releases page](https://github.com/dzivkovi/claude-chat-extractor/tags).

### Development Install

If you're contributing or modifying the code:

```bash
git clone https://github.com/dzivkovi/claude-chat-extractor.git
cd claude-chat-extractor
pip install -e .   # editable mode — changes take effect immediately
patchright install chromium
```

### First Run

On the first run (or after clearing your browser profile), you may need to solve a Cloudflare CAPTCHA manually in the browser window. Once solved, the `cf_clearance` cookie is saved and subsequent runs should pass through automatically.

If you get stuck on "Performing security verification" indefinitely, delete the browser profile to start fresh:

```bash
# Linux/macOS
rm -rf ~/.claude-chat-extractor/browser_profile

# Windows
rmdir /s /q %USERPROFILE%\.claude-chat-extractor\browser_profile
```

## Usage

```bash
# Basic usage — produces consolidated_chat.md (no temp folders)
claude-chat-extractor https://claude.ai/share/CHAT_ID

# Custom output file
claude-chat-extractor CHAT_URL -o my_conversation.md

# PDF format (for human reading)
claude-chat-extractor CHAT_URL -f pdf

# As Python module
python -m claude_chat_extractor https://claude.ai/share/CHAT_ID

# Keep intermediate files for debugging
claude-chat-extractor CHAT_URL --keep-artifacts --keep-html
```

## Gemini support (experimental)

In addition to Claude, the tool can extract shared Gemini conversations from `https://gemini.google.com/share/...` URLs. The provider is auto-detected from the URL hostname — no extra flag required:

```bash
# Auto-detected as Gemini from the hostname
claude-chat-extractor https://gemini.google.com/share/ee483c76e7a5

# Or force a provider explicitly
claude-chat-extractor --provider gemini https://gemini.google.com/share/ee483c76e7a5
```

The output format is identical for both providers — the assistant's role label in the markdown reflects which AI answered (`🤖 **Claude**` vs `🤖 **Gemini**`).

**Why "experimental":** Gemini's share pages are Angular-based with custom elements (`<share-turn-viewer>`, `<user-query-content>`, `<message-content>`) and no public selector contract. Google may rev the DOM in future builds and silently break extraction. If a Gemini run returns 0 messages, file an issue — the fix is a selector update in the `PROVIDERS` registry in [src/claude_chat_extractor/extractor.py](src/claude_chat_extractor/extractor.py). Code-block (`<pre><code>`) extraction for Gemini conversations is also unverified; if your conversation contains code artifacts that don't appear in the output, please report it.

## How to Continue a Conversation

1. **Share your chat** - Click share button in Claude Desktop
2. **Extract** - Run `claude-chat-extractor https://claude.ai/share/CHAT_ID`
3. **Continue** - The tool prints the resume prompt when done:

   Open a new Claude Desktop chat, attach `consolidated_chat.md`, and paste:

   ```text
   Continuing from previous session. Context attached. Continue from where we left off.
   ```

## Output

Creates a single markdown file containing:

- Metadata header (source URL, message count, artifact count)
- Complete conversation text with role markers
- All code artifacts embedded in fenced code blocks
- Table of contents for artifact navigation

Format optimized for LLM consumption. No intermediate folders created by default.

### Auto-named output files (v1.3.0+)

When you don't pass `-o`, the tool extracts the chat title from the share page and renames the output file accordingly:

```text
consolidated_chat-<YYYY-MM-DD>-<provider>-<chat_title_slug>.md
```

Example outputs:

```text
consolidated_chat-2026-04-08-gemini-Deciphering_Project_Plan_Notes.md
consolidated_chat-2026-05-03-claude-Personal_AI_Agents_in_2026_Convergence.md
```

This means you can run multiple extractions in the same folder in parallel without one overwriting another — different chats produce different filenames automatically. If a name collides anyway (same chat re-extracted, or two chats that auto-titled identically), the second write gets a `_1`, `_2`, … suffix.

**Provider component is derived from the URL, not the registry.** Whatever the leftmost label of the hostname is (lowercased, with a leading `www.` stripped) becomes the provider slug in the filename:

```text
https://claude.ai/share/...        →  claude
https://chatgpt.com/share/...      →  chatgpt
https://gemini.google.com/share/.. →  gemini
```

This decouples the filename from internal extractor selection — pointing the tool at a `chatgpt.com` URL produces a `chatgpt`-prefixed file even when the actual extraction is handled by a fallback strategy.

**Date precedence per provider:**

- **Gemini** — uses the "Created with Pro April 8, 2026 at 07:24 PM" line from the share page header. This is the **actual chat-creation date.**
- **ChatGPT** — decodes the Unix timestamp from the first 8 hex chars of the share URL (UUID v8 format). This is the **share-creation moment**, ≈ chat-end time for short chats.
- **Claude** — Anthropic strips chat dates from share pages (verified across multiple URLs), so the date falls back to **today** (when you ran the tool). Filenames will still be unique because the chat title differs, but the date won't reflect when the conversation actually happened.
- The filename is sanitized for **NTFS / Microsoft Windows**: invalid characters (`< > : " / \ | ? *`) become underscores, runs of underscores collapse to one, reserved device names (`CON`, `PRN`, `COM1`, …) get a leading underscore added, length is capped at 80 chars for the title slug.

If you'd rather keep the old `consolidated_chat.md` filename, pass `-o consolidated_chat.md` explicitly — the auto-rename only fires when `-o` is omitted.

## Arguments

```text
positional:
  url                      Share URL (Claude or Gemini)

optional:
  -V, --version            Print installed version and description, then exit
  -o, --output PATH        Output file path. If omitted, the tool auto-names
                           the file from the chat title and date (see "Output").
  -f, --format FORMAT      markdown (default) or pdf
  -w, --work-dir PATH      Working directory for intermediate files
                           (only created when needed)
  --keep-artifacts         Save individual artifact files to work dir
  --keep-html              Save intermediate HTML to work dir
  --provider {claude,gemini}
                           AI provider (auto-detected from URL hostname)
```

## Requirements

- Python 3.8+
- Chrome browser (installed on your system)
- Patchright (stealth Playwright fork, installed automatically as dependency)

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| Stuck on "Performing security verification" forever | Tainted browser profile or Cloudflare rate limit | Delete `~/.claude-chat-extractor/browser_profile/` and retry |
| Yellow warning bar in Chrome | Patchright stealth flag (expected) | Ignore it — this is a feature, not a bug |
| Chrome doesn't launch | Chrome not installed or not found | Install Chrome, or check it's on your PATH |
| `patchright` import error | Package not installed | Run `pip install patchright && patchright install chromium` |

## License

MIT
