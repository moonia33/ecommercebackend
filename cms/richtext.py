"""Tiptap (ProseMirror) JSON -> sanitized HTML / content blocks.

Editors store the document as Tiptap JSON. The storefront gets content
blocks: ``html`` segments with a ``products`` block wherever the custom
``productCards`` node sits, so the FE renders real product cards with
live prices and stock.
"""
from __future__ import annotations

import html
import re

import bleach

ALLOWED_TAGS = [
    "p", "h2", "h3", "h4", "strong", "em", "s", "u", "code", "pre", "a", "br", "hr",
    "ul", "ol", "li", "blockquote", "img", "figure", "figcaption",
    "table", "thead", "tbody", "tr", "th", "td",
]
ALLOWED_ATTRS = {
    "a": ["href", "target", "rel"],
    "img": ["src", "alt", "title", "loading"],
    "ol": ["start"],
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
}
_URL_OK = re.compile(r"^(https?://|mailto:|tel:|/(?!/)|#)", re.I)
_IMG_OK = re.compile(r"^(https?://|/(?!/))", re.I)
_MARKS = {"bold": "strong", "italic": "em", "strike": "s", "underline": "u", "code": "code"}
_BLOCKS = {
    "paragraph": "p", "bulletList": "ul", "orderedList": "ol", "listItem": "li",
    "blockquote": "blockquote", "tableRow": "tr",
}
PRODUCT_NODE = "productCards"
MAX_PRODUCTS_PER_BLOCK = 12


def _attr(value) -> str:
    return html.escape(str(value), quote=True)


def _text(node: dict) -> str:
    out = html.escape(str(node.get("text") or ""), quote=False)
    for mark in reversed(node.get("marks") or []):
        kind = (mark or {}).get("type")
        if kind in _MARKS:
            tag = _MARKS[kind]
            out = f"<{tag}>{out}</{tag}>"
        elif kind == "link":
            href = str(((mark.get("attrs") or {}).get("href")) or "").strip()
            if _URL_OK.match(href):
                external = href.lower().startswith("http")
                extra = ' target="_blank" rel="noopener noreferrer"' if external else ""
                out = f'<a href="{_attr(href)}"{extra}>{out}</a>'
    return out


def _children(node: dict) -> str:
    return "".join(_node(c) for c in (node.get("content") or []) if isinstance(c, dict))


def _cell(tag: str, node: dict) -> str:
    attrs = node.get("attrs") or {}
    extra = ""
    for key in ("colspan", "rowspan"):
        try:
            n = int(attrs.get(key) or 1)
        except (TypeError, ValueError):
            n = 1
        if 1 < n <= 20:
            extra += f' {key}="{n}"'
    return f"<{tag}{extra}>{_children(node)}</{tag}>"


def _node(node: dict) -> str:
    kind = node.get("type")
    attrs = node.get("attrs") or {}
    if kind == "text":
        return _text(node)
    if kind == "hardBreak":
        return "<br>"
    if kind == "horizontalRule":
        return "<hr>"
    if kind == "heading":
        try:
            level = min(max(int(attrs.get("level") or 2), 2), 4)
        except (TypeError, ValueError):
            level = 2
        return f"<h{level}>{_children(node)}</h{level}>"
    if kind == "orderedList":
        try:
            start = int(attrs.get("start") or 1)
        except (TypeError, ValueError):
            start = 1
        extra = f' start="{start}"' if start != 1 else ""
        return f"<ol{extra}>{_children(node)}</ol>"
    if kind in _BLOCKS:
        tag = _BLOCKS[kind]
        return f"<{tag}>{_children(node)}</{tag}>"
    if kind == "codeBlock":
        return f"<pre><code>{_children(node)}</code></pre>"
    if kind == "image":
        src = str(attrs.get("src") or "").strip()
        if not _IMG_OK.match(src):
            return ""
        alt = _attr(attrs.get("alt") or "")
        title = attrs.get("title") or ""
        img = f'<img src="{_attr(src)}" alt="{alt}" loading="lazy">'
        if title:
            return f"<figure>{img}<figcaption>{html.escape(str(title))}</figcaption></figure>"
        return img
    if kind == "table":
        return f"<table><tbody>{_children(node)}</tbody></table>"
    if kind == "tableHeader":
        return _cell("th", node)
    if kind == "tableCell":
        return _cell("td", node)
    # Unknown node: keep its text content, drop the wrapper.
    return _children(node)


def clean_html(raw: str) -> str:
    return bleach.clean(
        raw, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS,
        protocols=["http", "https", "mailto", "tel"], strip=True,
    )


def product_ids_of(node: dict) -> list[int]:
    ids = []
    for raw in (node.get("attrs") or {}).get("productIds") or []:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in ids:
            ids.append(pid)
    return ids[:MAX_PRODUCTS_PER_BLOCK]


def doc_to_segments(doc) -> list[dict]:
    """``[{"type": "html", "html": str} | {"type": "products", "product_ids": [...], "title": str}]``"""
    if not isinstance(doc, dict):
        return []
    segments: list[dict] = []
    buf: list[str] = []

    def flush():
        joined = clean_html("".join(buf)).strip()
        buf.clear()
        if joined:
            segments.append({"type": "html", "html": joined})

    for node in doc.get("content") or []:
        if not isinstance(node, dict):
            continue
        if node.get("type") == PRODUCT_NODE:
            ids = product_ids_of(node)
            if ids:
                flush()
                segments.append({
                    "type": "products", "product_ids": ids,
                    "title": str((node.get("attrs") or {}).get("title") or "")[:200],
                })
            continue
        buf.append(_node(node))
    flush()
    return segments


def doc_to_html(doc) -> str:
    return "".join(s["html"] for s in doc_to_segments(doc) if s["type"] == "html")


def doc_product_ids(doc) -> list[int]:
    ids: list[int] = []
    for s in doc_to_segments(doc):
        for pid in s.get("product_ids") or []:
            if pid not in ids:
                ids.append(pid)
    return ids


def doc_plain_text(doc) -> str:
    parts: list[str] = []

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "text":
            parts.append(str(node.get("text") or ""))
        for c in node.get("content") or []:
            walk(c)
        if node.get("type") in ("paragraph", "heading", "listItem"):
            parts.append(" ")

    walk(doc)
    return re.sub(r"\s+", " ", "".join(parts)).strip()
