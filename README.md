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

**Note:** This tool extracts from Claude shared chat URLs and is designed for continuing conversations in Claude Desktop.

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

This installs the `claude-chat-extractor` command globally in your Python environment. You can run it from any directory.

To **update** to the latest version:

```bash
pip install --upgrade git+https://github.com/dzivkovi/claude-chat-extractor.git
```

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

## How to Continue a Conversation

1. **Share your chat** - Click share button in Claude Desktop
2. **Extract** - Run `claude-chat-extractor https://claude.ai/share/CHAT_ID`
3. **Continue** - The tool prints the resume prompt when done:

   Open a new Claude Desktop chat, attach `consolidated_chat.md`, and paste:

   ```text
   Continuing from previous session. Context attached. Continue from where we left off.
   ```

## Output

Creates `consolidated_chat.md` containing:

- Metadata header (source URL, message count, artifact count)
- Complete conversation text with role markers
- All code artifacts embedded in fenced code blocks
- Table of contents for artifact navigation

Format optimized for LLM consumption. No intermediate folders created by default.

## Arguments

```text
positional:
  url                   Claude share URL

optional:
  -o, --output PATH     Output file path (default: consolidated_chat.md)
  -f, --format FORMAT   markdown (default) or pdf
  -w, --work-dir PATH   Working directory for intermediate files
                         (only created when needed)
  --keep-artifacts      Save individual artifact files to work dir
  --keep-html           Save intermediate HTML to work dir
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
