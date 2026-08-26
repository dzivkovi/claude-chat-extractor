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
from urllib.parse import urlparse

# Single source of truth: pyproject.toml. importlib.metadata reads the
# installed package's metadata (populated by setuptools at build/install
# time). Fallback covers running extractor.py directly without install.
try:
    from importlib.metadata import PackageNotFoundError, metadata as _pkg_metadata

    _pkg_meta = _pkg_metadata("claude-chat-extractor")
    __version__ = _pkg_meta["Version"]
    __description__ = _pkg_meta["Summary"]
except (ImportError, PackageNotFoundError):
    __version__ = "1.8.0"
    __description__ = (
        "Extract and consolidate shared Claude, Gemini, ChatGPT, "
        "and Google AI Mode conversations"
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

# Tombstone strings that providers leave inline where artifacts have
# been redacted from a share view. Claude's known phrasing is
# "Files hidden in shared chats" — we scan extracted message text for
# any of these and surface a warning so readers (and downstream LLMs)
# know to download attachments out-of-band.
_HIDDEN_FILES_TOMBSTONES = (
    'Files hidden in shared chats',
    'File hidden in shared chats',
)


def _detect_hidden_files(messages):
    """Return True if any extracted message contains an artifact-redacted
    tombstone string. Conservative literal match — no regex — to avoid
    false positives from messages that happen to discuss the topic."""
    if not messages:
        return False
    return any(
        any(t in (m.get('content') or '') for t in _HIDDEN_FILES_TOMBSTONES)
        for m in messages
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
    # or underscores look ugly. Also strip leading/trailing "+" — Daniel
    # uses "+" as a personal importance marker on chat titles; it stays
    # in the metadata title but is dropped from the filename ("less is
    # more"). Only strips at the boundaries — a "+" mid-title (e.g.
    # "C++") is preserved.
    s = s.strip('._- +')
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


def _provider_label_from_url(url):
    """Derive the filename's provider component from a URL hostname.

    Rule: take the leftmost label of the hostname, lowercased. Leading
    'www.' and 'share.' labels are stripped first — 'share' is a
    link-shortener subdomain (Gemini's short links live at
    share.gemini.google), not a provider name. So:
        https://claude.ai/share/...          -> 'claude'
        https://chatgpt.com/share/...        -> 'chatgpt'
        https://gemini.google.com/share/..   -> 'gemini'
        https://share.gemini.google/<id>     -> 'gemini'

    This decouples the filename from the PROVIDERS registry key, so a
    ChatGPT URL run with the (current, fallback) Claude extractor still
    gets filed under 'chatgpt' in the filename — matching the URL the
    user actually pointed at.
    """
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or '').lower()
    except (ValueError, AttributeError):
        return None
    if host.startswith('www.'):
        host = host[4:]
    labels = host.split('.')
    if labels and labels[0] == 'share' and len(labels) > 1:
        labels = labels[1:]
    if not labels:
        return None
    return labels[0] or None


# Display capitalization for provider names with non-trivial casing.
# Anything not in this dict gets `.title()`-cased — fine for 'claude',
# 'gemini', and any single-word provider, wrong for 'chatgpt' which
# would otherwise become 'Chatgpt'.
_PROVIDER_DISPLAY_LABELS = {
    'claude': 'Claude',
    'chatgpt': 'ChatGPT',
    'gemini': 'Gemini',
    # share.google/aimode/... -> hostname label 'google'. Gemini share
    # links keep their own 'gemini' label (share.gemini.google strips
    # only the leading 'share'), so this entry is AI Mode's alone.
    'google': 'Google AI Mode',
}


def _provider_display_label_from_url(url, fallback='AI'):
    """Human-friendly provider name for headers and role markers.

    Same source-of-truth as the filename label (URL hostname), but
    capitalized for places it shows up in prose: the markdown header's
    'Assistant:' field, the per-message '🤖 **{name}**' role marker,
    and the 'Provider:' line printed at the start of a run.
    """
    key = _provider_label_from_url(url)
    if not key:
        return fallback
    return _PROVIDER_DISPLAY_LABELS.get(key, key.title())


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
            // NOTE: a leading "+" is intentionally preserved — Daniel uses it
            // as a personal importance marker on chat titles. Do not strip.
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
            // NOTE: a leading "+" is intentionally preserved — Daniel uses it
            // as a personal importance marker on chat titles. Do not strip.
            return {
                title: title,
                createdRaw: createdRaw,
                publishedRaw: publishedRaw,
            };
        }
    """)


def _extract_chat_metadata_chatgpt(page):
    """Extract chat title from a ChatGPT share page.

    ChatGPT stamps the chat name into <title> as 'ChatGPT - <name>',
    so document.title is a clean source. The leading 'ChatGPT - '
    prefix (separator can be hyphen, en-dash, or em-dash depending on
    locale) is stripped so the title slug doesn't carry it.
    The chat-creation date is decoded from the URL hex prefix in
    _enrich_metadata — see _chatgpt_iso_date_from_url.
    """
    return page.evaluate("""
        () => {
            let title = (document.title || '').trim();
            // Strip "ChatGPT - " (or - or –) prefix if present
            title = title.replace(/^ChatGPT\\s*[-\\u2013\\u2014]\\s*/, '').trim();
            return {
                title: title,
                createdRaw: null,
            };
        }
    """)


def _enrich_metadata(provider, url, raw):
    """Normalize per-provider raw metadata into a unified shape.

    Returns: {'title': str, 'created_date': 'YYYY-MM-DD' or None, ...}
    The created_date is the chat-creation date when we can determine
    it (Gemini page header, ChatGPT URL hex), else None.

    ChatGPT date decoding follows the URL hostname rather than the
    registry key — same philosophy as the v1.4 label fix. So a
    chatgpt.com URL handled by the fallback Claude extractor still
    gets a correct chat-creation date in the filename.
    """
    raw = raw or {}
    title = (raw.get('title') or '').strip()
    created_date = None
    if provider == 'gemini':
        created_date = _gemini_iso_date_from_text(raw.get('createdRaw'))
    if not created_date and _provider_label_from_url(url) == 'chatgpt':
        created_date = _chatgpt_iso_date_from_url(url)
    return {
        'title': title,
        'created_date': created_date,
        'shared_by': raw.get('sharedBy'),
        'created_raw': raw.get('createdRaw'),
        'published_raw': raw.get('publishedRaw'),
    }


def _compute_auto_filename(
    provider, url, chat_metadata, format_type, output_dir,
    title_override=None,
):
    """Build a Windows-safe filename from chat metadata.

    Pattern: consolidated_chat-YYYY-MM-DD-<provider>-<title-slug>.<ext>
    Date precedence:
      1. chat_metadata['created_date'] if present (Gemini, ChatGPT)
      2. today's local date (Claude, or providers where date is missing)
    Title precedence:
      1. title_override (from --slug CLI flag) if non-empty — runs
         through the same Windows-safe slug sanitizer as extracted titles
      2. chat_metadata['title'] if non-empty (extracted from share page)
      3. literal "untitled" — keeps the filename parseable for
         downstream tools and grep
    Adds _1, _2, ... suffix on collision so parallel runs of
    different chats won't overwrite each other.
    """
    date = (chat_metadata or {}).get('created_date') or datetime.now().strftime('%Y-%m-%d')
    if title_override:
        title_slug = _windows_safe_slug(title_override) or 'untitled'
    else:
        title = (chat_metadata or {}).get('title') or ''
        title_slug = _windows_safe_slug(title) or 'untitled'
    ext = 'pdf' if format_type == 'pdf' else 'md'
    # Filename's provider component comes from the URL hostname, not
    # the PROVIDERS registry key — so a ChatGPT URL handled by the
    # fallback Claude extractor still files under 'chatgpt'.
    label = _provider_label_from_url(url) or provider
    base = f'consolidated_chat-{date}-{label}-{title_slug}'
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

    Responses are serialized DOM -> markdown rather than read via innerText:
    innerText flattens headings/lists/bold to plain text, drops code-fence
    languages, and leaks UI noise (source-citation chips, "Copy code"
    buttons, follow-up suggestion pills) into the transcript. The
    serializer walks the message DOM, emits markdown for the structural
    tags Gemini uses (h1-h6, p, ul/ol, blockquote, table, <code-block>
    with its language label, inline code, links, bold/italic), and skips
    Gemini's decoration elements entirely.

    User-side file attachments (<user-query-file-preview>) are surfaced as
    an "[Attachment: name]" / "[Attached image: url]" manifest appended to
    the user message — the share page doesn't expose file contents, but
    the image URLs (lh3.googleusercontent.com) remain fetchable and named
    file chips keep their filename.
    """
    return page.evaluate("""
        () => {
            // Gemini UI elements that must not leak into the transcript.
            const SKIP_TAGS = new Set([
                'sources-carousel-inline', 'source-footnote',
                'source-inline-chip', 'follow-up', 'elicitations',
                'tts-control-v2', 'button', 'mat-icon', 'gem-icon',
                'gem-icon-button', 'style', 'script', 'link-block',
                'chat-loading-animation', 'processing-state',
            ]);

            const fenceLang = (raw) => {
                const s = (raw || '').trim().toLowerCase().replace(/\\s+/g, '');
                return /^[a-z0-9_+#.-]{1,20}$/.test(s) ? s : '';
            };

            // The language name ("Bash", "Python") is the first bare text
            // node in the code-block header, outside <pre>.
            const codeBlockLabel = (cb) => {
                const walker = document.createTreeWalker(cb, NodeFilter.SHOW_ELEMENT);
                let el;
                while ((el = walker.nextNode())) {
                    if (el.closest('pre')) continue;
                    if (SKIP_TAGS.has(el.tagName.toLowerCase())) continue;
                    const own = [...el.childNodes]
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim())
                        .join('');
                    if (own) return fenceLang(own);
                }
                return '';
            };

            const codeBlockToMd = (cb) => {
                const pre = cb.querySelector('pre');
                const code = (pre ? pre.textContent : cb.textContent) || '';
                return '\\n```' + codeBlockLabel(cb) + '\\n'
                    + code.replace(/\\n+$/, '') + '\\n```\\n';
            };

            const tableToMd = (tbl) => {
                const rows = [...tbl.querySelectorAll('tr')].map(tr =>
                    [...tr.querySelectorAll('th,td')].map(c =>
                        (c.innerText || '').trim()
                            .replace(/\\|/g, '\\\\|')
                            .replace(/\\n+/g, ' ')
                    )
                );
                if (!rows.length) return '';
                const lines = [
                    '| ' + rows[0].join(' | ') + ' |',
                    '| ' + rows[0].map(() => '---').join(' | ') + ' |',
                ];
                rows.slice(1).forEach(r => lines.push('| ' + r.join(' | ') + ' |'));
                return '\\n' + lines.join('\\n') + '\\n';
            };

            function serializeChildren(node, ctx) {
                return [...node.childNodes].map(n => serialize(n, ctx)).join('');
            }

            function serialize(node, ctx) {
                if (node.nodeType === 3) {
                    return node.textContent.replace(/\\s+/g, ' ');
                }
                if (node.nodeType !== 1) return '';
                const tag = node.tagName.toLowerCase();
                if (SKIP_TAGS.has(tag)) return '';
                if (tag === 'code-block') return codeBlockToMd(node);
                if (tag === 'table') return tableToMd(node);
                if (tag === 'br') return '\\n';
                if (tag === 'hr') return '\\n---\\n';
                if (tag === 'img') return '';

                const kids = () => serializeChildren(node, ctx);

                if (/^h[1-6]$/.test(tag)) {
                    const level = parseInt(tag[1], 10);
                    return '\\n' + '#'.repeat(level) + ' ' + kids().trim() + '\\n';
                }
                if (tag === 'p') return '\\n' + kids().trim() + '\\n';
                if (tag === 'blockquote') {
                    const inner = kids().trim().split('\\n')
                        .map(l => '> ' + l).join('\\n');
                    return '\\n' + inner + '\\n';
                }
                if (tag === 'ul' || tag === 'ol') {
                    const items = [...node.children]
                        .filter(c => c.tagName.toLowerCase() === 'li');
                    const indent = '  '.repeat(ctx.listDepth);
                    const lines = items.map((li, idx) => {
                        const marker = tag === 'ol' ? `${idx + 1}. ` : '- ';
                        return serializeChildren(li, {listDepth: ctx.listDepth + 1})
                            .trim().split('\\n')
                            .map((l, j) => j === 0
                                ? indent + marker + l
                                : indent + '  ' + l)
                            .join('\\n');
                    });
                    return '\\n' + lines.join('\\n') + '\\n';
                }
                if (tag === 'pre') {
                    return '\\n```\\n'
                        + (node.textContent || '').replace(/\\n+$/, '')
                        + '\\n```\\n';
                }
                if (tag === 'code') {
                    const t = (node.textContent || '').trim();
                    return t ? '`' + t + '`' : '';
                }
                if (tag === 'a') {
                    const href = node.getAttribute('href') || '';
                    const t = kids().trim();
                    if (!t) return '';
                    return href.startsWith('http') ? `[${t}](${href})` : t;
                }
                if (tag === 'b' || tag === 'strong') {
                    const t = kids().trim();
                    return t ? `**${t}**` : '';
                }
                if (tag === 'i' || tag === 'em') {
                    const t = kids().trim();
                    return t ? `*${t}*` : '';
                }
                return kids();
            }

            const toMarkdown = (root) =>
                serializeChildren(root, {listDepth: 0})
                    .replace(/[ \\t]+\\n/g, '\\n')
                    .replace(/\\n{3,}/g, '\\n\\n')
                    .trim();

            const messages = [];
            const turns = document.querySelectorAll('share-turn-viewer');

            turns.forEach((turn, i) => {
                const userEl = turn.querySelector('user-query-content');
                if (userEl) {
                    let text = (userEl.innerText || '')
                        .replace(/^You said\\s+/, '')
                        .trim();
                    const attachments =
                        [...turn.querySelectorAll('user-query-file-preview')]
                        .map(p => {
                            const chip = (p.innerText || '').trim();
                            if (chip) {
                                return '[Attachment: '
                                    + chip.replace(/\\n+/g, ' - ') + ']';
                            }
                            const img = p.querySelector('img');
                            const src = img ? (img.getAttribute('src') || '') : '';
                            return src
                                ? '[Attached image: ' + src + ']'
                                : '[Attachment]';
                        });
                    if (attachments.length) {
                        text = (text ? text + '\\n\\n' : '')
                            + attachments.join('\\n');
                    }
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
                    const text = toMarkdown(modelEl);
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


def _extract_messages_chatgpt(page):
    """Extract conversation messages from a ChatGPT share page.

    ChatGPT marks every message with a stable [data-message-author-role]
    attribute whose value is 'user' or 'assistant'. There is one quirk
    that is the whole reason this extractor exists: assistant messages
    have innerText='' (the streaming markdown renderer puts content in
    a tree where the rendered-text algorithm sees nothing) but
    textContent is correct. So we read innerText first, fall back to
    textContent. Verified live on a 17-message conversation: 5 user +
    12 assistant, 62 KB of content end-to-end.

    A single user prompt often yields multiple consecutive
    'assistant' messages (preamble paragraph(s) + main answer + final
    summary), corresponding to ChatGPT's "Thought for Ns ›" UI element
    that bundles them. We keep them as separate messages so the
    consolidated markdown preserves the actual exchange structure.
    """
    return page.evaluate("""
        () => {
            const messages = [];
            const elements = document.querySelectorAll('[data-message-author-role]');

            elements.forEach((el, i) => {
                const role = el.getAttribute('data-message-author-role');
                if (role !== 'user' && role !== 'assistant') return;

                let text = (el.innerText || '').trim();
                if (!text) text = (el.textContent || '').trim();

                if (text.length < 10) return;

                messages.push({
                    index: i,
                    role: role,
                    content: text
                });
            });

            return messages;
        }
    """)


def _extract_artifacts(page):
    """Extract code artifacts from the page.

    Fence language detection, in order:
      1. a 'language-<name>' class token on the <code> element
         (Claude, ChatGPT, most highlighters);
      2. Gemini's <code-block> header label — a bare text span
         ("Bash", "Python") outside the <pre>;
      3. 'text'.
    The result is validated against a conservative charset so the
    markdown fence never inherits framework class soup like
    'code-container formatted ng-tns-c...'.
    """
    return page.evaluate("""
        () => {
            const codeBlocks = document.querySelectorAll('pre code');
            const artifacts = [];

            const validLang = (s) => {
                s = (s || '').trim().toLowerCase().replace(/\\s+/g, '');
                return /^[a-z0-9_+#.-]{1,20}$/.test(s) ? s : null;
            };

            codeBlocks.forEach((block, i) => {
                const code = block.textContent;
                if (code && code.length > 50) {
                    let language = null;
                    const m = (block.className || '').toString()
                        .match(/language-([A-Za-z0-9_+#.-]{1,20})/);
                    if (m) language = validLang(m[1]);
                    if (!language) {
                        const cb = block.closest('code-block');
                        if (cb) {
                            const walker = document.createTreeWalker(
                                cb, NodeFilter.SHOW_ELEMENT);
                            let el;
                            while ((el = walker.nextNode())) {
                                if (el.closest('pre')) continue;
                                const own = [...el.childNodes]
                                    .filter(n => n.nodeType === 3)
                                    .map(n => n.textContent.trim())
                                    .join('');
                                if (own) {
                                    language = validLang(own);
                                    break;
                                }
                            }
                        }
                    }
                    artifacts.push({
                        index: i,
                        content: code,
                        language: language || 'text'
                    });
                }
            });

            return artifacts;
        }
    """)


def _extract_messages_aimode(page):
    """Extract conversation messages from a Google AI Mode share page.

    A share.google/aimode/<id> link 302-redirects to a normal
    google.com/search?...&udm=50 result page, so there is no bespoke
    share DOM the way Claude/Gemini/ChatGPT have one. The turn
    structure is carried by two markers that alternate in document
    order, one pair per exchange:

      - user turn:      <h2> whose text is "You said: <query>"
                        (an a11y heading; the "You said: " prefix is
                        stripped the same way Gemini's is)
      - assistant turn: <div data-subtree="aimc">  ("AI Mode content")

    Responses are serialized DOM -> markdown rather than read via
    innerText: AI Mode answers carry real <table>, <ul>/<li>, <strong>
    and <a> markup that innerText flattens, and they embed live UI that
    innerText would leak into the transcript - inline citation chips
    (a <button aria-label="Related results"> plus icon-only <a> links),
    and a per-answer share widget (a role="dialog" subtree with a
    "Share public link" heading, a copy <textarea>, and an <aside>
    reading "Cannot copy the link right now").

    The skip rules are deliberately semantic (tag name, role=dialog,
    aria-hidden) rather than class-based: every class on this page is
    an obfuscated Google build artifact ("n6owBd awi2gc") that rotates
    without notice.

    Note <span data-subtree="aimfl"> is NOT skipped - despite looking
    like a wrapper it holds the answer's real first line.
    """
    return page.evaluate("""
        () => {
            const SKIP_TAGS = new Set([
                'button', 'svg', 'path', 'textarea', 'input', 'select',
                'form', 'aside', 'style', 'script', 'noscript', 'img',
                'g-dialog', 'g-dialog-content', 'template',
            ]);

            const skipEl = (el) => {
                const tag = el.tagName.toLowerCase();
                if (SKIP_TAGS.has(tag)) return true;
                // The per-answer share widget and any other overlay.
                if (el.getAttribute('role') === 'dialog') return true;
                if (el.getAttribute('aria-hidden') === 'true') return true;
                // Collapsed UI that a DOM walk sees but a reader never
                // does: the thumbs-up/down feedback panel with its rating
                // chips and privacy blurb, and "Show all" toggles. Tested
                // per-element rather than via checkVisibility() because
                // Chrome reports display:contents elements as invisible,
                // and Google wraps real answer content in those.
                const cs = getComputedStyle(el);
                if (cs.display === 'none' || cs.visibility === 'hidden') {
                    return true;
                }
                return false;
            };

            const tableToMd = (tbl) => {
                const rows = [...tbl.querySelectorAll('tr')].map(tr =>
                    [...tr.querySelectorAll('th,td')].map(c =>
                        (c.innerText || '').trim()
                            .replace(/\\|/g, '\\\\|')
                            .replace(/\\n+/g, ' ')
                    )
                );
                if (!rows.length) return '';
                const lines = [
                    '| ' + rows[0].join(' | ') + ' |',
                    '| ' + rows[0].map(() => '---').join(' | ') + ' |',
                ];
                rows.slice(1).forEach(r => lines.push('| ' + r.join(' | ') + ' |'));
                return '\\n' + lines.join('\\n') + '\\n';
            };

            function serializeChildren(node, ctx) {
                return [...node.childNodes].map(n => serialize(n, ctx)).join('');
            }

            function serialize(node, ctx) {
                if (node.nodeType === 3) {
                    return node.textContent.replace(/\\s+/g, ' ');
                }
                if (node.nodeType !== 1) return '';
                if (skipEl(node)) return '';
                const tag = node.tagName.toLowerCase();
                if (tag === 'table') return tableToMd(node);
                if (tag === 'br') return '\\n';
                if (tag === 'hr') return '\\n---\\n';

                const kids = () => serializeChildren(node, ctx);

                if (/^h[1-6]$/.test(tag)) {
                    const t = kids().trim();
                    if (!t) return '';
                    return '\\n' + '#'.repeat(parseInt(tag[1], 10)) + ' ' + t + '\\n';
                }
                // AI Mode's section titles are ARIA headings, not <h*>:
                // <div role="heading" aria-level="3">. Without this they
                // land in the transcript as bare paragraphs.
                if (node.getAttribute('role') === 'heading') {
                    const t = kids().trim();
                    if (!t) return '';
                    const lvl = Math.min(6, Math.max(1,
                        parseInt(node.getAttribute('aria-level'), 10) || 3));
                    return '\\n' + '#'.repeat(lvl) + ' ' + t + '\\n';
                }
                if (tag === 'p') return '\\n' + kids().trim() + '\\n';
                if (tag === 'blockquote') {
                    const inner = kids().trim().split('\\n')
                        .map(l => '> ' + l).join('\\n');
                    return '\\n' + inner + '\\n';
                }
                if (tag === 'ul' || tag === 'ol') {
                    const items = [...node.children]
                        .filter(c => c.tagName.toLowerCase() === 'li');
                    const indent = '  '.repeat(ctx.listDepth);
                    const lines = items.map((li, idx) => {
                        const marker = tag === 'ol' ? `${idx + 1}. ` : '- ';
                        return serializeChildren(li, {listDepth: ctx.listDepth + 1})
                            .trim().split('\\n')
                            .map((l, j) => j === 0
                                ? indent + marker + l
                                : indent + '  ' + l)
                            .join('\\n');
                    });
                    return '\\n' + lines.join('\\n') + '\\n';
                }
                if (tag === 'pre') {
                    return '\\n```\\n'
                        + (node.textContent || '').replace(/\\n+$/, '')
                        + '\\n```\\n';
                }
                if (tag === 'code') {
                    const t = (node.textContent || '').trim();
                    return t ? '`' + t + '`' : '';
                }
                if (tag === 'a') {
                    const href = node.getAttribute('href') || '';
                    const t = kids().trim();
                    // Icon-only citation links have no text - drop them.
                    if (!t) return '';
                    return href.startsWith('http') ? `[${t}](${href})` : t;
                }
                if (tag === 'b' || tag === 'strong') {
                    const t = kids().trim();
                    return t ? `**${t}**` : '';
                }
                if (tag === 'i' || tag === 'em') {
                    const t = kids().trim();
                    return t ? `*${t}*` : '';
                }
                // AI Mode has no <p>: paragraphs are bare text runs inside
                // nested <div>s, so block-level containers must force a
                // break or the whole answer collapses into one line.
                if (tag === 'div' || tag === 'section' || tag === 'article') {
                    const t = kids();
                    return t.trim() ? '\\n' + t.trim() + '\\n' : '';
                }
                return kids();
            }

            const toMarkdown = (root) =>
                serializeChildren(root, {listDepth: 0})
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t]+\\n/g, '\\n')
                    .replace(/\\n{3,}/g, '\\n\\n')
                    .replace(/(\\s*---)+\\s*$/, '')
                    .trim();

            const messages = [];
            // h2 (user) and [data-subtree=aimc] (assistant) alternate in
            // document order; reading them together preserves the turn
            // sequence without relying on a per-turn wrapper element.
            const nodes = document.querySelectorAll('h2, [data-subtree="aimc"]');

            nodes.forEach((el, i) => {
                if (el.tagName === 'H2') {
                    const raw = (el.innerText || '').trim();
                    const m = raw.match(/^You said:\\s*([\\s\\S]+)$/);
                    if (!m) return;
                    const text = m[1].trim();
                    if (text.length > 10) {
                        messages.push({index: i, role: 'user', content: text});
                    }
                } else {
                    // The answer lives in the "main-col" container; the
                    // sibling "rhs-col" holds the sources carousel (cards
                    // of truncated snippets), which is UI, not transcript.
                    const root =
                        el.querySelector('[data-container-id="main-col"]') || el;
                    const text = toMarkdown(root);
                    if (text.length > 10) {
                        messages.push({index: i, role: 'assistant', content: text});
                    }
                }
            });

            return messages;
        }
    """)


def _extract_chat_metadata_aimode(page):
    """Extract the chat title from a Google AI Mode share page.

    Google stamps the shared conversation's <h1> as
    "AI Mode Conversation: <first query>". The wrapper prefix is
    stripped so the filename slug is the query itself - the provider
    is already carried by the "google" label in the filename. There is
    no chat-creation date anywhere on the page, so created_date stays
    None and the filename falls back to today's date.
    """
    return page.evaluate("""
        () => {
            const h1 = document.querySelector('h1');
            let title = h1 ? (h1.innerText || '').trim() : '';
            if (!title) {
                title = (document.title || '')
                    .replace(/\\s*-\\s*Google Search\\s*$/, '').trim();
            }
            title = title.replace(/^AI Mode Conversation:\\s*/i, '').trim();
            return {title: title, createdRaw: null};
        }
    """)


# url_prefixes is a tuple because one provider can own several share
# domains: Gemini's in-app "Copy link" now hands out short links on
# share.gemini.google that 302-redirect to gemini.google.com/share/<id>.
# Both must route to the Gemini extractor — an unrecognized prefix
# silently falls back to the Claude extractor, which finds none of
# Gemini's DOM and returns 0 messages.
PROVIDERS = {
    "claude": {
        "url_prefixes": ("https://claude.ai/share/",),
        "extract_messages": _extract_messages_claude,
        "extract_metadata": _extract_chat_metadata_claude,
        "label": "Claude",
    },
    "gemini": {
        "url_prefixes": (
            "https://gemini.google.com/share/",
            "https://share.gemini.google/",
        ),
        "extract_messages": _extract_messages_gemini,
        "extract_metadata": _extract_chat_metadata_gemini,
        "label": "Gemini",
    },
    "chatgpt": {
        "url_prefixes": ("https://chatgpt.com/share/",),
        "extract_messages": _extract_messages_chatgpt,
        "extract_metadata": _extract_chat_metadata_chatgpt,
        "label": "ChatGPT",
    },
    # Google AI Mode. The share link 302-redirects to a plain
    # google.com/search?...&udm=50 page, so only the share.google form
    # is prefix-matchable — a pasted post-redirect search URL carries
    # its mode in a query param and must use --provider aimode.
    "aimode": {
        "url_prefixes": ("https://share.google/aimode/",),
        "extract_messages": _extract_messages_aimode,
        "extract_metadata": _extract_chat_metadata_aimode,
        "label": "Google AI Mode",
    },
}


def _url_matches_provider(url, cfg):
    """True if the URL starts with any of the provider's share prefixes."""
    return any(url.startswith(p) for p in cfg["url_prefixes"])


def _build_markdown(url, messages, artifacts, assistant_label='Claude'):
    """Build consolidated markdown from extracted data.

    assistant_label is the human-readable name shown for assistant
    turns (e.g. 'Claude' or 'Gemini'). The export header stays
    Claude-branded since the tool is called claude-chat-extractor.
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    has_hidden_files = _detect_hidden_files(messages)

    lines = [
        "# Claude Chat Export - Consolidated",
        "",
        f"**Exported**: {now}",
        f"**Source**: {url}",
        f"**Assistant**: {assistant_label}",
        f"**Messages**: {len(messages)}",
        f"**Artifacts**: {len(artifacts)}",
    ]
    if has_hidden_files:
        lines.append(
            "**⚠️ Hidden files**: this shared chat contains file "
            "attachments that the share page does not expose. Download "
            "them from your authenticated chat session if you need full "
            "context — the conversation text below references them but "
            "their content is not included here."
        )
    lines.extend([
        "",
        "---",
        ""
    ])

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
        if _detect_hidden_files(messages):
            print("   ⚠️  Hidden file attachments detected — share page "
                  "redacts file content; download separately if needed.")

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
        help=('Share URL (Claude: https://claude.ai/share/..., '
              'Gemini: https://gemini.google.com/share/... or '
              'https://share.gemini.google/..., '
              'ChatGPT: https://chatgpt.com/share/...)')
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

    parser.add_argument(
        '--slug',
        type=str,
        default=None,
        help=('Override the title portion of the auto-generated '
              'filename (e.g. --slug "3. AI Harness Terminology"). '
              'Sanitized for Windows-safe filenames. Useful when the '
              'extracted title is missing or unhelpful, or when you '
              'want a name that matches your filing scheme.')
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
                if _url_matches_provider(args.url, cfg)
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

    # Validate URL against ALL known share-link prefixes. The warning
    # only fires when the URL doesn't match any registered provider —
    # so a claude.ai, gemini.google.com, or chatgpt.com URL passes
    # through silently regardless of which one --provider selected.
    registry_label = PROVIDERS[args.provider]["label"]
    # Display label comes from the URL hostname when possible, so a
    # chatgpt.com URL handled by the fallback Claude extractor is still
    # labeled "ChatGPT" in headers (filename, markdown, console).
    display_label = _provider_display_label_from_url(
        args.url, fallback=registry_label
    )
    recognized = any(
        _url_matches_provider(args.url, cfg)
        for cfg in PROVIDERS.values()
    )
    if not recognized:
        print("⚠️  Warning: URL doesn't match any known "
              "share-link pattern")
        print("   Recognized:")
        for cfg in PROVIDERS.values():
            for prefix in cfg["url_prefixes"]:
                print(f"     {prefix}...")
        print(f"   Got: {args.url}")
        response = input("   Continue anyway? (y/N): ")
        if response.lower() != 'y':
            print("❌ Cancelled")
            return 1

    print("=" * 70)
    print("Claude Chat Extractor")
    print("=" * 70)
    print(f"URL:        {args.url}")
    print(f"Provider:   {display_label}")
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
                assistant_label=display_label,
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
                    title_override=args.slug,
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
                    title_override=args.slug,
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
