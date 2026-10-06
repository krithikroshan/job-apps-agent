"""Turning job-description HTML into readable plain text.

Careers sites and ATS APIs hand us descriptions as HTML, sometimes
double-escaped (Greenhouse returns ``&lt;p&gt;`` inside JSON). Scoring and
the review UI want plain text that still keeps paragraph and list breaks,
so a bullet list reads as lines rather than one run-on sentence.
"""

from __future__ import annotations

import html
import re

# Tags that end a line (breaks, list items, table rows) and tags that end a
# paragraph (paragraphs, headings, lists, block containers).
_LINE_TAGS = re.compile(r"<\s*br\s*/?\s*>|</\s*(?:li|tr)\s*>", re.IGNORECASE)
_PARA_TAGS = re.compile(r"</\s*(?:p|h[1-6]|div|ul|ol|section|article)\s*>", re.IGNORECASE)
# Script and style bodies are code, not prose; drop them with their content.
_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\f\v\r\xa0]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def _strip_tags(s: str) -> str:
    s = _SCRIPT_STYLE.sub(" ", s)
    s = _PARA_TAGS.sub("\n\n", s)
    s = _LINE_TAGS.sub("\n", s)
    return _TAGS.sub(" ", s)


def _tidy_whitespace(s: str) -> str:
    lines = (_SPACES.sub(" ", line).strip() for line in s.split("\n"))
    joined = "\n".join(lines)
    # Keep at most one blank line between paragraphs.
    return _BLANK_LINES.sub("\n\n", joined).strip()


def html_to_text(s: str | None) -> str:
    """Plain text from an HTML fragment, keeping line and paragraph breaks.

    Unescapes before stripping so escaped markup (``&lt;p&gt;``, as Greenhouse
    sends it) is treated as tags, then unescapes once more for the entities
    that were inside that markup (``&amp;amp;`` -> ``&``).
    """
    if not s:
        return ""
    text = html.unescape(_strip_tags(html.unescape(s)))
    return _tidy_whitespace(text)
