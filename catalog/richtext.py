from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import bleach
from markdownify import markdownify as _html_to_md


@dataclass(frozen=True)
class RichTextNormalizeResult:
    markdown: str
    source_format: Literal["md", "html"]


_ALLOWED_TAGS: list[str] = [
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "blockquote",
    "code",
    "pre",
]

_ALLOWED_ATTRS: dict[str, list[str]] = {}


def normalize_richtext_to_markdown(
    value: str | None,
    *,
    input_format: Literal["auto", "md", "html"] = "auto",
) -> RichTextNormalizeResult:
    """Normalize rich text to Markdown.

    Intended for XML imports where the field might contain HTML.

    - If `input_format` is `auto`, HTML is detected by presence of angle-bracket tags.
    - HTML is cleaned with `bleach` (scripts removed) before converting to Markdown.

    Returns normalized Markdown and detected source format.
    """

    text = (value or "").strip()
    if not text:
        return RichTextNormalizeResult(markdown="", source_format="md")

    fmt: Literal["md", "html"]
    if input_format == "md":
        fmt = "md"
    elif input_format == "html":
        fmt = "html"
    else:
        # Heuristic: treat as HTML if it looks like tags.
        fmt = "html" if (
            "<" in text and ">" in text and "</" in text) else "md"

    if fmt == "md":
        return RichTextNormalizeResult(markdown=text, source_format="md")

    cleaned = bleach.clean(
        text,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
    )

    md = _html_to_md(cleaned, heading_style="ATX")
    md = (md or "").strip()
    return RichTextNormalizeResult(markdown=md, source_format="html")
