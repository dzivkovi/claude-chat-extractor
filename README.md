# claude-chat-extractor

Extend your Claude Desktop context. Continue conversations beyond 200k tokens.

## The Problem

Claude Desktop conversations hit a context limit. When you reach 200k tokens, you can't continue in the same chat.

## The Solution

Extract your conversation to markdown (75% smaller), upload to a new chat, and continue seamlessly.

## Quick Start

```bash
# 1. Install
pip install git+https://github.com/dzivkovi/claude-chat-extractor.git
playwright install chromium

# 2. Extract your conversation (browser will open, complete CAPTCHA if needed)
claude-chat-extractor https://claude.ai/share/CHAT_ID

# 3. Open new Claude Desktop chat, attach consolidated_chat.md, and say:
"Continuing from previous session. Context attached. Continue from where we left off."
```

Your 200k token conversation becomes ~50k tokens (75% reduction) while preserving all content and code artifacts.

**Note:** This tool extracts from Claude shared chat URLs and is designed for continuing conversations in Claude Desktop.

## Installation

```bash
# From GitHub
pip install git+https://github.com/dzivkovi/claude-chat-extractor.git

# Or clone and install locally
git clone https://github.com/dzivkovi/claude-chat-extractor.git
cd claude-chat-extractor
pip install .

# Install Playwright browser
playwright install chromium
```

## Usage

```bash
# Basic usage
claude-chat-extractor https://claude.ai/share/CHAT_ID

# Custom output file
claude-chat-extractor CHAT_URL -o my_conversation.md

# PDF format (for human reading)
claude-chat-extractor CHAT_URL -f pdf

# As Python module
python -m claude_chat_extractor https://claude.ai/share/CHAT_ID
```

## How to Continue a Conversation

1. **Share your chat** - Click share button in Claude Desktop
2. **Extract** - Run `claude-chat-extractor https://claude.ai/share/CHAT_ID`
3. **Continue** - Open new Claude Desktop chat, attach `consolidated_chat.md`, and paste:

   ```text
   Continuing from previous session. Context attached.

   Continue from where we left off.
   ```

## Output

Creates `consolidated_chat.md` containing:

- Complete conversation text
- All code artifacts embedded
- Table of contents for navigation

Format optimized for LLM consumption.

## Arguments

```text
positional:
  url                   Claude share URL

optional:
  -o, --output PATH     Output file path (default: consolidated_chat.md)
  -f, --format FORMAT   markdown (default) or pdf
  -w, --work-dir PATH   Working directory (default: consolidated_chat/)
  --keep-artifacts      Preserve individual code files
  --keep-html          Preserve intermediate HTML
```

## Requirements

- Python 3.8+
- Playwright
- Chromium (via Playwright)

## License

MIT
