"""Rendering the body of a literature or journal work.

Text works carry no downloadable media file: their body lives in the
deviation's `textContent`, either as a tiptap document (the current editor,
a JSON tree of nodes) or as an HTML fragment (the older "writer" format, and
what the API's `deviation/content` endpoint returns). Either can be rendered
to plain text (`txt`) or to a standalone HTML document (`html`).

A resolved body is a `(kind, payload)` pair, where kind is one of the KIND_*
constants and payload the raw markup/text; `render` turns that pair into the
chosen output format. This module is pure: fetching the payloads (over the
website or the API) is left to the callers that own those clients.
"""

import html as _html
import json
import re

# The website's `type` for a text work; images and the rest carry media.
LITERATURE_TYPES = {"literature", "journal", "status"}

# The kinds of raw body a resolver can hand back.
KIND_TIPTAP = "tiptap"   # tiptap document, as a JSON string
KIND_HTML = "html"       # an HTML fragment (legacy "writer" / API content)
KIND_TEXT = "text"       # plain text (the listing excerpt)

FORMATS = ("txt", "html")

# tiptap nodes that stand on their own line; everything else is inline.
_BLOCK_TYPES = {
    "paragraph", "heading", "blockquote", "listItem", "bulletList",
    "orderedList", "codeBlock", "horizontalRule", "doc",
}
# tiptap inline marks mapped to their HTML tag.
_INLINE_MARKS = {"bold": "strong", "italic": "em", "underline": "u",
                 "strike": "s", "code": "code"}


def is_text_work(dev: dict) -> bool:
    """True when a work is text (literature/journal) rather than media.

    A media file, when present, always wins. Otherwise a website entry is
    recognised by its `type`, and an API entry (which carries no type) by the
    excerpt/text_content the API attaches only to text works.
    """
    if dev.get("content"):
        return False
    if dev.get("type") in LITERATURE_TYPES:
        return True
    return bool(dev.get("excerpt") or dev.get("text_content"))


def classify_web_html(html_obj: object) -> tuple[str, str] | None:
    """Turn a website `textContent.html` object into a (kind, payload) pair.

    tiptap carries a JSON document, anything else (legacy "writer") an HTML
    fragment. Returns None when there is no markup to render.
    """
    if not isinstance(html_obj, dict):
        return None
    markup = html_obj.get("markup")
    if not markup or not str(markup).strip():
        return None
    kind = KIND_TIPTAP if html_obj.get("type") == "tiptap" else KIND_HTML
    return kind, markup


# --- plain text -----------------------------------------------------------

def _node_text(node: dict) -> str:
    """Flatten one tiptap node (and its children) to text."""
    ntype = node.get("type")
    if ntype == "text":
        return node.get("text") or ""
    if ntype == "hardBreak":
        return "\n"
    inner = "".join(_node_text(c) for c in node.get("content") or [])
    return inner + "\n" if ntype in _BLOCK_TYPES else inner


def _tiptap_to_text(markup: str) -> str:
    try:
        doc = json.loads(markup)
    except (ValueError, TypeError):
        return ""
    root = (doc.get("document") if isinstance(doc, dict) else None) or doc
    return "".join(_node_text(n) for n in (root or {}).get("content") or [])


def _strip_html(markup: str) -> str:
    """Turn an HTML fragment into plain text, keeping line breaks."""
    text = re.sub(r"(?is)<\s*br\s*/?>", "\n", markup)
    text = re.sub(r"(?is)</\s*(p|div|h[1-6]|li|blockquote|tr)\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return _html.unescape(text)


def _clean(text: str) -> str:
    """Normalise line endings, trim trailing spaces and collapse blank runs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- HTML ------------------------------------------------------------------

def _tiptap_inline(node: dict) -> str:
    """Render one inline tiptap node (text with marks, hardBreak) to HTML."""
    if node.get("type") == "hardBreak":
        return "<br>\n"
    if node.get("type") == "text":
        out = _html.escape(node.get("text") or "")
        for mark in node.get("marks") or []:
            mtype = mark.get("type")
            if mtype == "link":
                href = _html.escape((mark.get("attrs") or {}).get("href") or "")
                out = f'<a href="{href}">{out}</a>'
            elif mtype in _INLINE_MARKS:
                tag = _INLINE_MARKS[mtype]
                out = f"<{tag}>{out}</{tag}>"
        return out
    return "".join(_tiptap_inline(c) for c in node.get("content") or [])


def _tiptap_block(node: dict) -> str:
    """Render one block-level tiptap node to an HTML element."""
    ntype = node.get("type")
    children = node.get("content") or []
    if ntype in ("blockquote", "bulletList", "orderedList", "listItem"):
        body = "".join(_tiptap_block(c) for c in children)
        tag = {"blockquote": "blockquote", "bulletList": "ul",
               "orderedList": "ol", "listItem": "li"}[ntype]
        return f"<{tag}>{body}</{tag}>"
    if ntype == "horizontalRule":
        return "<hr>"
    inline = "".join(_tiptap_inline(c) for c in children)
    if ntype == "heading":
        level = min(max(int((node.get("attrs") or {}).get("level") or 1), 1), 6)
        return f"<h{level}>{inline}</h{level}>"
    if ntype == "codeBlock":
        return f"<pre><code>{inline}</code></pre>"
    # paragraph and any unknown block fall back to a paragraph.
    return f"<p>{inline}</p>"


def _tiptap_to_html(markup: str) -> str:
    try:
        doc = json.loads(markup)
    except (ValueError, TypeError):
        return ""
    root = (doc.get("document") if isinstance(doc, dict) else None) or doc
    return "\n".join(_tiptap_block(n) for n in (root or {}).get("content") or [])


def _text_to_html(text: str) -> str:
    """Wrap plain text in paragraphs, escaping it and keeping line breaks."""
    escaped = _html.escape(_clean(text))
    blocks = [b for b in escaped.split("\n\n") if b]
    return "\n".join(f"<p>{b.replace(chr(10), '<br>')}</p>" for b in blocks)


def html_document(title: str, body: str) -> str:
    """Wrap an HTML body fragment in a minimal, standalone document."""
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8">\n<title>{_html.escape(title)}</title>\n'
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


def render(kind: str, payload: str, fmt: str) -> str:
    """Render a resolved (kind, payload) body to the chosen format.

    For "html" the returned string is the body fragment; the caller wraps it in
    a document with `html_document`. For "txt" it is the cleaned plain text.
    """
    if fmt == "html":
        if kind == KIND_TIPTAP:
            return _tiptap_to_html(payload)
        if kind == KIND_HTML:
            return payload
        return _text_to_html(payload)
    # plain text
    if kind == KIND_TIPTAP:
        return _clean(_tiptap_to_text(payload))
    if kind == KIND_HTML:
        return _clean(_strip_html(payload))
    return _clean(payload)
