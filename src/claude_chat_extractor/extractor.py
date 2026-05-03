#!/usr/bin/env python3
"""
Claude Chat Extractor - Fetch and consolidate shared Claude chats

This tool extracts conversations and artifacts from Claude shared chat URLs,
with smart defaults for minimal, clean output optimized for LLM consumption.

Usage:
    claude-chat-extractor <CHAT_URL>
    claude-chat-extractor <CHAT_URL> --output my_chat.md
    claude-chat-extractor <CHAT_URL> --format pdf
"""

try:
    from patchright.sync_api import sync_playwright
    USING_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright
    USING_PATCHRIGHT = False
import argparse
import json
import re
import shutil
from pathlib import Path
from datetime import datetime, timezone

# Single source of truth: pyproject.toml. importlib.metadata reads the
# installed package's metadata (populated by setuptools at build/install
# time). Fallback covers running extractor.py directly without install.
try:
    from importlib.metadata import PackageNotFoundError, metadata as _pkg_metadata

    _pkg_meta = _pkg_metadata("claude-chat-extractor")
    __version__ = _pkg_meta["Version"]
    __description__ = _pkg_meta["Summary"]
except (ImportError, PackageNotFoundError):
    __version__ = "1.3.0"
    __description__ = (
        "Extract and consolidate shared Claude and Gemini conversations"
    )


RESUME_PROMPT = (
    "Continuing from previous session. "
    "Context attached. "
    "Continue from where we left off."
)


# Windows-invalid path characters: < > : " / \ | ? * and control chars (0x00-0x1F).
# Trailing dots and spaces are also forbidden on NTFS, handled by .strip() below.
_WIN_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Reserved device names — case-insensitive, with or without extension.
_WIN_RESERVED_NAMES = (
    {'CON', 'PRN', 'AUX', 'NUL'}
    | {f'COM{i}' for i in range(1, 10)}
    | {f'LPT{i}' for i in range(1, 10)}
)

# Months for parsing Gemini's "Created with Pro April 8, 2026 at 07:24 PM"
_MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5,
    'june': 6, 'july': 7, 'august': 8, 'september': 9, 'october': 10,
    'november': 11, 'december': 12,
}


def _windows_safe_slug(text, max_len=80):
    """Slugify a chat title into a filename component safe on Windows.

    Replaces invalid characters with underscores, collapses whitespace
    runs, strips leading/trailing punctuation that NTFS rejects, caps
    length, and avoids reserved device names like CON/PRN/COM1.
    """
    if not text:
        return ''
    s = _WIN_INVALID_CHARS_RE.sub('_', text)
    s = re.sub(r'\s+', '_', s.strip())
    # Collapse runs of underscores so we don't get ugly "Title__Subtitle"
    # when an invalid character sat next to a space.
    s = re.sub(r'_+', '_', s)
    # NTFS forbids trailing dots/spaces, and leading/trailing dashes
    # or underscores look ugly. Strip them all.
    s = s.strip('._- ')
    if len(s) > max_len:
        s = s[:max_len].rstrip('._- ')
    # Reserved-name protection. The check is on the part before any dot
    # since "CON.md" is also reserved.
    base = s.split('.', 1)[0].upper()
    if base in _WIN_RESERVED_NAMES:
        s = '_' + s
    return s


def _gemini_iso_date_from_text(text):
    """Parse 'April 8, 2026 at 07:24 PM' (or similar) into 'YYYY-MM-DD'.

    Returns None if the text doesn't contain a recognizable English-month
    + day + year triple. Locale-sensitive by design — Gemini renders
    these in the user's UI language.
    """
    if not text:
        return None
    m = re.search(r'\b(\w+)\s+(\d{1,2}),?\s+(\d{4})\b', text)
    if not m:
        return None
    month = _MONTH_NAMES.get(m.group(1).lower())
    if not month:
        return None
    try:
        return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"
    except ValueError:
        return None


def _chatgpt_iso_date_from_url(url):
    """Decode the chat-creation date from a ChatGPT share URL.

    ChatGPT share IDs are UUID v8: the first 8 hex chars are the
    Unix timestamp (seconds, big-endian) of when the share link was
    created, which is approximately the chat-end time for short
    chats. Returns None if the URL doesn't match the share pattern.
    """
    if not url:
        return None
    m = re.search(r'/share/([0-9a-f]{8})', url)
    if not m:
        return None
    try:
        ts = int(m.group(1), 16)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
    except (ValueError, OSError, OverflowError):
        return None


def _extract_chat_metadata_claude(page):
    """Extract chat title (and shared-by name, if visible) from Claude.

    Claude share pages render the chat title as the text inside
    <div class="truncate text-text-300">, prefixed with a leading
    "+" UI artifact that we strip. There is no chat-creation date
    available on the share page — Anthropic strips it.
    """
    return page.evaluate("""
        () => {
            const titleEl = document.querySelector('div.truncate.text-text-300');
            let title = titleEl ? (titleEl.innerText || '').trim() : '';
            if (title.startsWith('+')) title = title.slice(1).trim();
            if (!title) {
                const h1 = document.querySelector('h1');
                title = h1 ? (h1.innerText || '').trim() : '';
            }
            const sharedByMatch = (document.body.innerText || '').match(/Shared by\\s+([^\\n]+)/);
            return {
                title: title,
                sharedBy: sharedByMatch ? sharedByMatch[1].trim() : null,
                createdRaw: null,
            };
        }
    """)


def _extract_chat_metadata_gemini(page):
    """Extract chat title and creation date from a Gemini share page.

    Gemini's <.share-title-section> contains the title (in <strong>),
    the share URL, then two visible date lines:
      "Created with [tier] April 8, 2026 at 07:24 PM"
      "Published April 11, 2026 at 07:02 PM"
    We capture the raw "Created" text and parse to ISO Python-side.
    """
    return page.evaluate("""
        () => {
            const section = document.querySelector('.share-title-section');
            let title = '';
            let createdRaw = null;
            let publishedRaw = null;
            if (section) {
                const strongEl = section.querySelector('strong');
                title = strongEl ? strongEl.textContent.trim() : '';
                const lines = section.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
                for (const line of lines) {
                    const cm = line.match(/^Created(?:\\s+with\\s+\\S+)?\\s+(.+)$/);
                    if (cm) createdRaw = cm[1].trim();
                    const pm = line.match(/^Published\\s+(.+)$/);
                    if (pm) publishedRaw = pm[1].trim();
                }
            }
            if (!title) {
                const h1 = document.querySelector('h1');
                title = h1 ? (h1.innerText || '').trim() : '';
            }
            return {
                title: title,
                createdRaw: createdRaw,
                publishedRaw: publishedRaw,
            };
        }
    """)


def _enrich_metadata(provider, url, raw):
    """Normalize per-provider raw metadata into a unified shape.

    Returns: {'title': str, 'created_date': 'YYYY-MM-DD' or None, ...}
    The created_date is the chat-creation date when we can determine
    it (Gemini page header, ChatGPT URL hex), else None.
    """
    raw = raw or {}
    title = (raw.get('title') or '').strip()
    created_date = None
    if provider == 'gemini':
        created_date = _gemini_iso_date_from_text(raw.get('createdRaw'))
    elif provider == 'chatgpt':
        created_date = _chatgpt_iso_date_from_url(url)
    return {
        'title': title,
        'created_date': created_date,
        'shared_by': raw.get('sharedBy'),
        'created_raw': raw.get('createdRaw'),
        'published_raw': raw.get('publishedRaw'),
    }


def _compute_auto_filename(provider, url, chat_metadata, format_type, output_dir):
    """Build a Windows-safe filename from chat metadata.

    Pattern: consolidated_chat-YYYY-MM-DD-<provider>-<title-slug>.<ext>
    Date precedence:
      1. chat_metadata['created_date'] if present (Gemini, ChatGPT)
      2. today's local date (Claude, or providers where date is missing)
    Title precedence:
      1. chat_metadata['title'] if non-empty
      2. literal "untitled" — keeps the filename parseable for
         downstream tools and grep
    Adds _1, _2, ... suffix on collision so parallel runs of
    different chats won't overwrite each other.
    """
    date = (chat_metadata or {}).get('created_date') or datetime.now().strftime('%Y-%m-%d')
    title = (chat_metadata or {}).get('title') or ''
    title_slug = _windows_safe_slug(title) or 'untitled'
    ext = 'pdf' if format_type == 'pdf' else 'md'
    base = f'consolidated_chat-{date}-{provider}-{title_slug}'
    candidate = output_dir / f'{base}.{ext}'
    if not candidate.exists():
        return candidate
    for i in range(1, 1000):
        candidate = output_dir / f'{base}_{i}.{ext}'
        if not candidate.exists():
            return candidate
    return output_dir / f'{base}.{ext}'  # last-resort overwrite


def _launch_browser(p):
    """Launch a persistent browser context with stealth settings."""
    profile_dir = (
        Path.home() / ".claude-chat-extractor" / "browser_profile"
    )
    profile_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs = dict(
        user_data_dir=str(profile_dir),
        headless=False,
        no_viewport=True,
        chromium_sandbox=True,
    )
    if USING_PATCHRIGHT:
        launch_kwargs["channel"] = "chrome"

    context = p.chromium.launch_persistent_context(**launch_kwargs)
    page = (
        context.pages[0] if context.pages else context.new_page()
    )
    return context, page


def _wait_for_cloudflare(page):
    """Auto-detect and wait for Cloudflare challenge to resolve."""
    try:
        for _attempt in range(3):
            cf_challenge = (
                "challenges.cloudflare.com" in page.url
                or page.locator(
                    "text=Verifying you are human"
                ).count() > 0
            )
            if cf_challenge:
                print("   Cloudflare challenge detected, "
                      "waiting for resolution...")
                page.wait_for_url(
                    "**/share/**", timeout=120000
                )
                break
            page.wait_for_timeout(2000)
    except Exception:
        pass  # Fall through to manual prompt


def _extract_messages_claude(page):
    """Extract conversation messages from a Claude share page."""
    return page.evaluate("""
        () => {
            const messages = [];
            const messageContainers = document.querySelectorAll('[data-test-render-count]');

            messageContainers.forEach((el, i) => {
                const text = el.innerText || el.textContent;
                if (text && text.length > 10) {
                    let role = 'assistant';
                    if (el.className.includes('user') || el.querySelector('.font-user-message')) {
                        role = 'user';
                    }

                    messages.push({
                        index: i,
                        role: role,
                        content: text.trim()
                    });
                }
            });

            return messages;
        }
    """)


def _extract_messages_gemini(page):
    """Extract conversation messages from a Gemini share page.

    Gemini share pages wrap each Q/A pair in a <share-turn-viewer> custom
    element containing one <user-query-content> and one <response-container>
    (which holds a <message-content>). The "You said" prefix in user text is
    an a11y label concatenated into innerText — strip it.
    """
    return page.evaluate("""
        () => {
            const messages = [];
            const turns = document.querySelectorAll('share-turn-viewer');

            turns.forEach((turn, i) => {
                const userEl = turn.querySelector('user-query-content');
                if (userEl) {
                    const text = (userEl.innerText || '')
                        .replace(/^You said\\s+/, '')
                        .trim();
                    if (text && text.length > 10) {
                        messages.push({
                            index: i * 2,
                            role: 'user',
                            content: text
                        });
                    }
                }

                const modelEl = turn.querySelector('response-container message-content');
                if (modelEl) {
                    const text = (modelEl.innerText || '').trim();
                    if (text && text.length > 10) {
                        messages.push({
                            index: i * 2 + 1,
                            role: 'assistant',
                            content: text
                        });
                    }
                }
            });

            return messages;
        }
    """)


def _extract_artifacts(page):
    """Extract code artifacts from the page."""
    return page.evaluate("""
        () => {
            const codeBlocks = document.querySelectorAll('pre code');
            const artifacts = [];

            codeBlocks.forEach((block, i) => {
                const code = block.textContent;
                if (code && code.length > 50) {
                    const language = block.className.replace('language-', '') || 'text';
                    artifacts.push({
                        index: i,
                        content: code,
                        language: language
                    });
                }
            });

            return artifacts;
        }
    """)


PROVIDERS = {
    "claude": {
        "url_prefix": "https://claude.ai/share/",
        "extract_messages": _extract_messages_claude,
        "extract_metadata": _extract_chat_metadata_claude,
        "label": "Claude",
    },
    "gemini": {
        "url_prefix": "https://gemini.google.com/share/",
        "extract_messages": _extract_messages_gemini,
        "extract_metadata": _extract_chat_metadata_gemini,
        "label": "Gemini",
    },
}


def _build_markdown(url, messages, artifacts, assistant_label='Claude'):
    """Build consolidated markdown from extracted data.

    assistant_label is the human-readable name shown for assistant
    turns (e.g. 'Claude' or 'Gemini'). The export header stays
    Claude-branded since the tool is called claude-chat-extractor.
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lines = [
        "# Claude Chat Export - Consolidated",
        "",
        f"**Exported**: {now}",
        f"**Source**: {url}",
        f"**Assistant**: {assistant_label}",
        f"**Messages**: {len(messages)}",
        f"**Artifacts**: {len(artifacts)}",
        "",
        "---",
        ""
    ]

    # Table of contents for artifacts
    if artifacts:
        lines.extend(["## 📦 Code Artifacts", ""])
        for art in artifacts:
            lines.append(
                f"- [Artifact {art['index']}]"
                f"(#artifact-{art['index']})"
            )
        lines.extend(["", "---", ""])

    # Conversation
    lines.extend(["## 💬 Conversation", ""])

    for msg in messages:
        if msg['role'] == 'user':
            role = "👤 **User**"
        else:
            role = f"🤖 **{assistant_label}**"
        lines.extend([f"### {role}", "", msg['content'], "", "---", ""])

    # Artifacts section
    if artifacts:
        lines.extend(["", "## 📝 Code Artifacts - Full Content", ""])

        for art in artifacts:
            lang = art.get('language', 'text')
            lines.extend([
                f"### Artifact {art['index']}",
                "",
                f"```{lang}",
                art['content'],
                "```",
                ""
            ])

    # Footer
    lines.extend([
        "---",
        "",
        "*This document was automatically generated "
        "from a Claude chat export.*",
        "*Ready to use as context in your next "
        "Claude conversation.*",
        ""
    ])

    return '\n'.join(lines)


def fetch_chat(url, format_type='markdown', work_dir=None,
               keep_html=False, provider='claude'):
    """
    Fetch shared chat content using browser automation.

    Args:
        url: Share URL (Claude or Gemini)
        format_type: Output format ('markdown' or 'pdf')
        work_dir: Directory for intermediate files (only needed
                  for pdf or --keep-* flags)
        keep_html: Whether to save HTML file
        provider: Key into PROVIDERS ('claude' or 'gemini')

    Returns:
        dict with keys: messages, artifacts, metadata,
              and optionally pdf_path
    """
    if provider not in PROVIDERS:
        raise ValueError(
            f"Unknown provider {provider!r}. "
            f"Expected one of: {list(PROVIDERS)}"
        )
    extract_messages = PROVIDERS[provider]["extract_messages"]
    extract_metadata = PROVIDERS[provider]["extract_metadata"]

    print(f"🌐 Fetching chat from: {url}")

    with sync_playwright() as p:
        context, page = _launch_browser(p)

        try:
            page.goto(url, timeout=60000)
        except Exception as e:
            print(f"⚠️  Navigation warning: {e}")

        _wait_for_cloudflare(page)

        print("\n⏳ Waiting for page to load...")
        print("   If you see a CAPTCHA or verification, "
              "please complete it.")
        input("   Press Enter once the chat content is "
              "fully loaded... ")

        print("📄 Extracting content...")

        # Scroll to load lazy content
        page.evaluate(
            "window.scrollTo(0, document.body.scrollHeight);"
        )
        page.wait_for_timeout(2000)

        # Save HTML if requested
        if keep_html:
            if work_dir:
                work_dir.mkdir(exist_ok=True)
                html_path = work_dir / "chat_complete.html"
                html_path.write_text(
                    page.content(), encoding='utf-8'
                )
                print(f"   Saved HTML: {html_path}")

        # Generate PDF if requested
        pdf_path = None
        if format_type == 'pdf':
            if work_dir:
                work_dir.mkdir(exist_ok=True)
            pdf_path = (work_dir or Path('.')) / "chat.pdf"
            page.pdf(
                path=str(pdf_path),
                format='A4',
                print_background=True
            )
            print(f"✅ PDF saved: {pdf_path}")

        # Extract data
        messages = extract_messages(page)
        print("📦 Extracting artifacts...")
        artifacts = _extract_artifacts(page)
        print(f"   Found {len(artifacts)} code artifacts")

        # Extract chat-level metadata (title, dates, etc.) — best-effort.
        # Failures here must not crash the run; we fall back to defaults.
        try:
            raw_metadata = extract_metadata(page)
        except Exception as e:
            print(f"   ⚠️  Metadata extraction failed: {e}")
            raw_metadata = {}
        chat_metadata = _enrich_metadata(provider, url, raw_metadata)
        if chat_metadata.get('title'):
            print(f"   Chat title: {chat_metadata['title']}")
        if chat_metadata.get('created_date'):
            print(f"   Chat created: {chat_metadata['created_date']}")

        context.close()

        metadata = {
            'url': url,
            'extracted_at': datetime.now().isoformat(),
            'message_count': len(messages),
            'artifact_count': len(artifacts),
        }

        return {
            'messages': messages,
            'artifacts': artifacts,
            'metadata': metadata,
            'chat_metadata': chat_metadata,
            'pdf_path': pdf_path,
        }


def consolidate_markdown(url, messages, artifacts,
                         output_file, assistant_label='Claude'):
    """Build and write consolidated markdown file."""
    print(f"\n📝 Writing: {output_file}")

    content = _build_markdown(
        url, messages, artifacts, assistant_label=assistant_label
    )
    output_file.write_text(content, encoding='utf-8')

    size_kb = output_file.stat().st_size / 1024
    print(f"✅ Created: {size_kb:.1f} KB, "
          f"{len(messages)} messages, "
          f"{len(artifacts)} artifacts")


def main():
    """Main entry point with CLI."""
    parser = argparse.ArgumentParser(
        description=__description__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage - creates consolidated_chat.md
  claude-chat-extractor https://claude.ai/share/CHAT_ID

  # Custom output file
  claude-chat-extractor CHAT_URL --output my_summary.md

  # Generate PDF instead of markdown
  claude-chat-extractor CHAT_URL --format pdf

  # Keep intermediate files for debugging
  claude-chat-extractor CHAT_URL --keep-artifacts --keep-html
        """
    )

    parser.add_argument(
        '--version', '-V',
        action='version',
        version=f'%(prog)s {__version__}\n{__description__}',
    )

    parser.add_argument(
        'url',
        help=('Share URL (Claude: https://claude.ai/share/... '
              'or Gemini: https://gemini.google.com/share/...)')
    )

    parser.add_argument(
        '--output', '-o',
        type=Path,
        help=('Output file path. If omitted, the file is auto-named '
              'consolidated_chat-<date>-<provider>-<title>.md|pdf')
    )

    parser.add_argument(
        '--work-dir', '-w',
        type=Path,
        default=None,
        help=('Working directory for intermediate files '
              '(only created when needed)')
    )

    parser.add_argument(
        '--format', '-f',
        choices=['markdown', 'pdf'],
        default='markdown',
        help='Output format (default: markdown)'
    )

    parser.add_argument(
        '--keep-artifacts',
        action='store_true',
        help='Save individual artifact files to work dir'
    )

    parser.add_argument(
        '--keep-html',
        action='store_true',
        help='Save intermediate HTML file to work dir'
    )

    parser.add_argument(
        '--provider',
        choices=list(PROVIDERS.keys()),
        default=None,
        help=('AI provider to extract from. Auto-detected '
              'from URL hostname if omitted.')
    )

    args = parser.parse_args()

    # Track whether the user explicitly chose an output path. If they
    # did, we honor it verbatim; if not, we'll auto-name the file from
    # the chat metadata so parallel extractions don't overwrite each
    # other in the same folder.
    user_specified_output = args.output is not None

    # Auto-detect provider from URL hostname if not specified.
    # Falls back to 'claude' to preserve pre-v1.2.0 default behavior.
    if args.provider is None:
        args.provider = next(
            (
                name for name, cfg in PROVIDERS.items()
                if args.url.startswith(cfg["url_prefix"])
            ),
            'claude',
        )

    # Set default output paths
    if args.output is None:
        if args.format == 'pdf':
            args.output = Path('chat.pdf')
        else:
            args.output = Path('consolidated_chat.md')

    # Work dir only needed for pdf, keep-artifacts, or keep-html
    needs_work_dir = (
        args.format == 'pdf'
        or args.keep_artifacts
        or args.keep_html
    )
    if args.work_dir is None and needs_work_dir:
        args.work_dir = Path('consolidated_chat')

    # Validate URL against the selected provider's prefix
    expected_prefix = PROVIDERS[args.provider]["url_prefix"]
    provider_label = PROVIDERS[args.provider]["label"]
    if not args.url.startswith(expected_prefix):
        print(f"⚠️  Warning: URL doesn't look like a "
              f"{provider_label} share link")
        print(f"   Expected: {expected_prefix}...")
        print(f"   Got: {args.url}")
        response = input("   Continue anyway? (y/N): ")
        if response.lower() != 'y':
            print("❌ Cancelled")
            return 1

    print("=" * 70)
    print("Claude Chat Extractor")
    print("=" * 70)
    print(f"URL:        {args.url}")
    print(f"Provider:   {provider_label}")
    print(f"Format:     {args.format}")
    print(f"Output:     {args.output}")
    print("=" * 70)
    print()

    try:
        result = fetch_chat(
            url=args.url,
            format_type=args.format,
            work_dir=args.work_dir,
            keep_html=args.keep_html,
            provider=args.provider,
        )

        if args.format == 'markdown':
            consolidate_markdown(
                url=args.url,
                messages=result['messages'],
                artifacts=result['artifacts'],
                output_file=args.output,
                assistant_label=provider_label,
            )

            # Auto-rename when -o was not given. Pattern:
            #   consolidated_chat-YYYY-MM-DD-<provider>-<title-slug>.md
            # Skips on collision with _N suffix so parallel runs of
            # different chats land in unique files.
            if not user_specified_output:
                auto = _compute_auto_filename(
                    provider=args.provider,
                    url=args.url,
                    chat_metadata=result.get('chat_metadata'),
                    format_type=args.format,
                    output_dir=args.output.parent,
                )
                if auto != args.output:
                    args.output.rename(auto)
                    args.output = auto

            # Save individual artifacts if requested
            if args.keep_artifacts and args.work_dir:
                args.work_dir.mkdir(exist_ok=True)
                for art in result['artifacts']:
                    ext = art.get('language', 'txt')
                    path = (args.work_dir /
                            f"artifact_code_{art['index']}.{ext}")
                    path.write_text(
                        art['content'], encoding='utf-8'
                    )
                print(f"   Saved {len(result['artifacts'])} "
                      f"artifact files to {args.work_dir}/")

            # Save JSON if work dir exists
            if args.work_dir and args.work_dir.exists():
                json_path = args.work_dir / "conversation.json"
                json_path.write_text(json.dumps({
                    'metadata': result['metadata'],
                    'chat_metadata': result.get('chat_metadata'),
                    'messages': result['messages'],
                }, indent=2), encoding='utf-8')

            print(f"\n🎉 Success! "
                  f"{args.output.absolute()}")
            print("\n" + "=" * 70)
            print("📋 To continue this conversation:")
            print("   1. Open a new Claude Desktop chat")
            print(f"   2. Attach {args.output}")
            print("   3. Paste this prompt:")
            print()
            print(f'   "{RESUME_PROMPT}"')
            print("=" * 70)

        else:  # PDF
            pdf_source = result['pdf_path']
            if pdf_source and pdf_source != args.output:
                shutil.move(str(pdf_source), str(args.output))

            if not user_specified_output:
                auto = _compute_auto_filename(
                    provider=args.provider,
                    url=args.url,
                    chat_metadata=result.get('chat_metadata'),
                    format_type=args.format,
                    output_dir=args.output.parent,
                )
                if auto != args.output:
                    args.output.rename(auto)
                    args.output = auto

            print(f"\n🎉 Success! PDF created:")
            print(f"   {args.output.absolute()}")

        # Clean up empty work dir
        if (args.work_dir and args.work_dir.exists()
                and not any(args.work_dir.iterdir())):
            args.work_dir.rmdir()

        print()
        return 0

    except KeyboardInterrupt:
        print("\n\n❌ Cancelled by user")
        return 1

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
