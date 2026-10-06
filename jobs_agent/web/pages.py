"""HTML pages and static assets.

The markup, styles, and scripts live in ``static/`` as real files rather than
as Python string literals, so an editor can highlight and lint them. Pages
are read from disk on each request: they're a few kilobytes, and editing the
UI without restarting the server is worth more than the saved syscall.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"

CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

#: Site navigation, as (path, label, page key).
NAV_ITEMS = (
    ("/", "Queue", "queue"),
    ("/documents", "Profile", "documents"),
    ("/companies", "Companies", "companies"),
    ("/settings", "Settings", "settings"),
)


def nav(active: str, who: str = "", show_logout: bool = False) -> str:
    """The top bar. ``who`` is the candidate's name, shown as a trailing slug
    on every authenticated page (not just the one it's set on). ``show_logout``
    adds a "Log out" control — every authenticated page has one; the signed-out
    /login and /signup pages don't call this at all.

    The active link is marked with ``aria-current`` rather than a class: the
    design system styles that attribute directly, and it is what a screen
    reader announces.
    """
    links = []
    for path, label, key in NAV_ITEMS:
        current = ' aria-current="page"' if key == active else ""
        links.append(f'<a href="{path}"{current}>{label}</a>')
    slug = f'<span class="nav-who">{escape(who)}</span>' if who else ""
    logout = ('<button class="btn btn-ghost" id="nav-logout" type="button">Log out</button>'
              if show_logout else "")
    return ('<header class="topbar"><nav class="nav">'
            '<a class="brand" href="/"><img src="/static/logo-mark.svg" alt="" '
            'width="26" height="26">Jobs Agent</a>'
            '<span class="nav-links">' + "".join(links) + "</span>"
            '<span class="nav-end">' + slug + logout + "</span></nav></header>")


def render(name: str, **fields: str) -> str:
    """Read ``static/<name>`` and substitute ``{placeholder}`` tokens.

    Plain replacement rather than ``str.format`` so that braces in the markup
    — or in any script that later moves inline — aren't interpreted.
    """
    html = (STATIC_DIR / name).read_text()
    for key, value in fields.items():
        html = html.replace("{" + key + "}", value)
    return html


def queue_page(candidate_name: str) -> str:
    return render("queue.html", nav=nav("queue", candidate_name, show_logout=True))


def documents_page(candidate_name: str = "") -> str:
    return render("documents.html", nav=nav("documents", candidate_name, show_logout=True))


def companies_page(candidate_name: str = "") -> str:
    return render("companies.html", nav=nav("companies", candidate_name, show_logout=True))


def settings_page(candidate_name: str = "") -> str:
    return render("settings.html", nav=nav("settings", candidate_name, show_logout=True))


def login_page() -> str:
    return render("login.html")


def signup_page() -> str:
    return render("signup.html")


def static_asset(name: str) -> tuple[bytes, str] | None:
    """Bytes and content type for ``/static/<name>``, or None if it's not a
    servable asset.

    Only plain filenames of a known type resolve, so a crafted path can't
    walk out of ``static/``.
    """
    path = STATIC_DIR / name
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    content_type = CONTENT_TYPES.get(path.suffix)
    if content_type is None or not path.is_file():
        return None
    return path.read_bytes(), content_type
