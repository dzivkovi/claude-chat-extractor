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

from playwright.sync_api import sync_playwright
import argparse
import json
import re
import shutil
from pathlib import Path
from datetime import datetime


def fetch_chat(url: str, work_dir: Path, format_type: str, keep_html: bool = False):
    """
    Fetch Claude chat content using Playwright.

    Args:
        url: Claude share URL
        work_dir: Directory for intermediate files
        format_type: Output format ('markdown' or 'pdf')
        keep_html: Whether to keep HTML file after processing

    Returns:
        dict: Metadata about the extraction
    """
    work_dir.mkdir(exist_ok=True)

    print(f"🌐 Fetching chat from: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()

        # Navigate with error handling
        try:
            page.goto(url, timeout=60000)
        except Exception as e:
            print(f"⚠️  Navigation warning: {e}")

        # Manual CAPTCHA handling
        print("\n⏳ Waiting for page to load...")
        print("   If you see a CAPTCHA or verification, please complete it.")
        input("   Press Enter once the chat content is fully loaded... ")

        print("📄 Extracting content...")

        # Scroll to load lazy content
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        page.wait_for_timeout(2000)

        # Save HTML (intermediate)
        html_content = page.content()
        html_path = work_dir / "chat_complete.html"
        html_path.write_text(html_content, encoding='utf-8')

        # Generate PDF if requested
        if format_type == 'pdf':
            pdf_path = work_dir / "chat.pdf"
            page.pdf(path=str(pdf_path), format='A4', print_background=True)
            print(f"✅ PDF saved: {pdf_path}")

        # Extract conversation messages
        messages_data = page.evaluate("""
            () => {
                const messages = [];
                const messageContainers = document.querySelectorAll('[data-test-render-count]');

                messageContainers.forEach((el, i) => {
                    const text = el.innerText || el.textContent;
                    if (text && text.length > 10) {
                        let role = 'assistant';  // Default to Claude
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

        # Save conversation JSON
        metadata = {
            'url': url,
            'extracted_at': datetime.now().isoformat(),
            'message_count': len(messages_data)
        }

        json_path = work_dir / "conversation.json"
        json_path.write_text(json.dumps({
            'metadata': metadata,
            'messages': messages_data
        }, indent=2), encoding='utf-8')

        # Save conversation markdown
        md_lines = [
            "# Claude Chat Export",
            "",
            f"**Source**: {url}",
            f"**Extracted**: {len(messages_data)} messages",
            f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            ""
        ]

        for msg in messages_data:
            role_header = "👤 **User**" if msg['role'] == 'user' else "🤖 **Claude**"
            md_lines.extend([f"### {role_header}", "", msg['content'], "", "---", ""])

        md_path = work_dir / "conversation.md"
        md_path.write_text('\n'.join(md_lines), encoding='utf-8')

        # Extract code artifacts
        print("📦 Extracting artifacts...")
        artifacts = page.evaluate("""
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

        # Save artifacts
        for artifact in artifacts:
            ext = artifact.get('language', 'txt')
            artifact_path = work_dir / f"artifact_code_{artifact['index']}.{ext}"
            artifact_path.write_text(artifact['content'], encoding='utf-8')

        print(f"   Found {len(artifacts)} code artifacts")

        browser.close()

        # Cleanup HTML if not keeping
        if not keep_html:
            html_path.unlink(missing_ok=True)
            print("   Cleaned up HTML file")

        return {
            'metadata': metadata,
            'artifact_count': len(artifacts),
            'work_dir': work_dir
        }


def consolidate_markdown(work_dir: Path, output_file: Path, keep_artifacts: bool = False):
    """
    Consolidate conversation and artifacts into single markdown file.

    Args:
        work_dir: Directory containing chat export files
        output_file: Path for consolidated output
        keep_artifacts: Whether to keep individual artifact files
    """
    print(f"\n📝 Consolidating to: {output_file}")

    # Read conversation
    conversation_md = work_dir / "conversation.md"
    if not conversation_md.exists():
        raise FileNotFoundError(f"conversation.md not found in {work_dir}")

    conversation_text = conversation_md.read_text(encoding='utf-8')

    # Read JSON metadata
    json_path = work_dir / "conversation.json"
    metadata = {}
    if json_path.exists():
        data = json.loads(json_path.read_text(encoding='utf-8'))
        metadata = data.get('metadata', {})

    # Collect artifacts
    artifact_files = sorted(work_dir.glob("artifact_code_*.*"))
    artifacts = {}

    for artifact_file in artifact_files:
        match = re.search(r'artifact_code_(\d+)', artifact_file.name)
        if match:
            artifact_num = match.group(1)
            ext = artifact_file.suffix.lstrip('.')
            artifacts[artifact_num] = {
                'content': artifact_file.read_text(encoding='utf-8'),
                'language': ext
            }

    # Build consolidated markdown
    lines = [
        "# Claude Chat Export - Consolidated",
        "",
        f"**Exported**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Source**: {metadata.get('url', 'Unknown')}",
        f"**Messages**: {metadata.get('message_count', 'Unknown')}",
        f"**Artifacts**: {len(artifacts)}",
        "",
        "---",
        ""
    ]

    # Table of contents for artifacts
    if artifacts:
        lines.extend([
            "## 📦 Code Artifacts",
            ""
        ])
        for num in sorted(artifacts.keys(), key=int):
            lines.append(f"- [Artifact {num}](#artifact-{num})")
        lines.extend(["", "---", ""])

    # Conversation
    lines.extend([
        "## 💬 Conversation",
        "",
        conversation_text,
        ""
    ])

    # Artifacts section
    if artifacts:
        lines.extend(["", "---", "", "## 📝 Code Artifacts - Full Content", ""])

        for num in sorted(artifacts.keys(), key=int):
            artifact = artifacts[num]
            lang = artifact['language']
            lines.extend([
                f"### Artifact {num}",
                "",
                f"```{lang}",
                artifact['content'],
                "```",
                ""
            ])

    # Footer
    lines.extend([
        "---",
        "",
        "*This document was automatically generated from a Claude chat export.*",
        "*Ready to use as context in your next Claude conversation.*",
        ""
    ])

    # Write output
    output_file.write_text('\n'.join(lines), encoding='utf-8')

    print(f"✅ Consolidated markdown created:")
    print(f"   - Size: {output_file.stat().st_size / 1024:.1f} KB")
    print(f"   - Conversation: {len(conversation_text)} chars")
    print(f"   - Artifacts: {len(artifacts)}")

    # Cleanup artifacts if not keeping
    if not keep_artifacts:
        for artifact_file in artifact_files:
            artifact_file.unlink()
        conversation_md.unlink(missing_ok=True)
        print(f"   Cleaned up {len(artifact_files)} artifact files")


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
        help='Output file path (default: consolidated_chat.md for markdown, chat.pdf for PDF)'
    )

    parser.add_argument(
        '--work-dir', '-w',
        type=Path,
        default=Path('consolidated_chat'),
        help='Working directory for intermediate files (default: consolidated_chat/)'
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
        help='Keep individual artifact files after consolidation'
    )

    parser.add_argument(
        '--keep-html',
        action='store_true',
        help='Keep intermediate HTML file'
    )

    args = parser.parse_args()

    # Set default output paths
    if args.output is None:
        if args.format == 'pdf':
            args.output = Path('chat.pdf')
        else:
            args.output = Path('consolidated_chat.md')

    # Validate URL
    if not args.url.startswith('https://claude.ai/share/'):
        print("⚠️  Warning: URL doesn't look like a Claude share link")
        print(f"   Expected: https://claude.ai/share/...")
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
    print(f"Work dir:   {args.work_dir}")
    print("=" * 70)
    print()

    try:
        # Step 1: Fetch chat content
        result = fetch_chat(
            url=args.url,
            work_dir=args.work_dir,
            format_type=args.format,
            keep_html=args.keep_html
        )

        # Step 2: Consolidate (for markdown format only)
        if args.format == 'markdown':
            consolidate_markdown(
                work_dir=result['work_dir'],
                output_file=args.output,
                keep_artifacts=args.keep_artifacts
            )

            print(f"\n🎉 Success! Your consolidated markdown is ready:")
            print(f"   {args.output.absolute()}")
            print(f"\n💡 This file is optimized for use as context in Claude conversations.")
            print(f"   Simply upload it to your next chat to continue the discussion!")

        else:  # PDF
            # Move PDF to final location if different
            pdf_source = result['work_dir'] / 'chat.pdf'
            if pdf_source != args.output:
                shutil.move(str(pdf_source), str(args.output))

            print(f"\n🎉 Success! PDF created:")
            print(f"   {args.output.absolute()}")

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
