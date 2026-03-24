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
import shutil
from pathlib import Path
from datetime import datetime


RESUME_PROMPT = (
    "Continuing from previous session. "
    "Context attached. "
    "Continue from where we left off."
)


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


def _extract_messages(page):
    """Extract conversation messages from the page."""
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


def _build_markdown(url, messages, artifacts):
    """Build consolidated markdown from extracted data."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lines = [
        "# Claude Chat Export - Consolidated",
        "",
        f"**Exported**: {now}",
        f"**Source**: {url}",
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
            role = "🤖 **Claude**"
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
               keep_html=False):
    """
    Fetch Claude chat content using browser automation.

    Args:
        url: Claude share URL
        format_type: Output format ('markdown' or 'pdf')
        work_dir: Directory for intermediate files (only needed
                  for pdf or --keep-* flags)
        keep_html: Whether to save HTML file

    Returns:
        dict with keys: messages, artifacts, metadata,
              and optionally pdf_path
    """
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
        messages = _extract_messages(page)
        print("📦 Extracting artifacts...")
        artifacts = _extract_artifacts(page)
        print(f"   Found {len(artifacts)} code artifacts")

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
            'pdf_path': pdf_path,
        }


def consolidate_markdown(url, messages, artifacts,
                         output_file):
    """Build and write consolidated markdown file."""
    print(f"\n📝 Writing: {output_file}")

    content = _build_markdown(url, messages, artifacts)
    output_file.write_text(content, encoding='utf-8')

    size_kb = output_file.stat().st_size / 1024
    print(f"✅ Created: {size_kb:.1f} KB, "
          f"{len(messages)} messages, "
          f"{len(artifacts)} artifacts")


def main():
    """Main entry point with CLI."""
    parser = argparse.ArgumentParser(
        description='Extract and consolidate Claude shared chats',
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
        'url',
        help='Claude share URL (e.g., https://claude.ai/share/...)'
    )

    parser.add_argument(
        '--output', '-o',
        type=Path,
        help=('Output file path (default: '
              'consolidated_chat.md or chat.pdf)')
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

    args = parser.parse_args()

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

    # Validate URL
    if not args.url.startswith('https://claude.ai/share/'):
        print("⚠️  Warning: URL doesn't look like a "
              "Claude share link")
        print("   Expected: https://claude.ai/share/...")
        print(f"   Got: {args.url}")
        response = input("   Continue anyway? (y/N): ")
        if response.lower() != 'y':
            print("❌ Cancelled")
            return 1

    print("=" * 70)
    print("Claude Chat Extractor")
    print("=" * 70)
    print(f"URL:        {args.url}")
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
        )

        if args.format == 'markdown':
            consolidate_markdown(
                url=args.url,
                messages=result['messages'],
                artifacts=result['artifacts'],
                output_file=args.output,
            )

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
