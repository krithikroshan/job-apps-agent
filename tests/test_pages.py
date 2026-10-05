"""Page rendering and static-asset serving."""

from jobs_agent.web import pages


def test_nav_marks_the_active_page():
    assert 'href="/" aria-current="page"' in pages.nav("queue")
    assert 'href="/documents" aria-current="page"' in pages.nav("documents")
    assert 'href="/settings" aria-current="page"' in pages.nav("settings")


def test_nav_shows_and_escapes_the_candidate_slug():
    assert 'class="nav-who"' not in pages.nav("queue")
    marked = pages.nav("queue", '<b>Jane</b>')
    assert "&lt;b&gt;Jane&lt;/b&gt;" in marked
    assert "<b>Jane</b>" not in marked


def test_nav_shows_logout_only_when_asked():
    assert "nav-logout" not in pages.nav("queue")
    assert "nav-email" not in pages.nav("queue")
    assert 'id="nav-logout"' in pages.nav("queue", show_logout=True)


def test_queue_page_escapes_the_candidate_name():
    html = pages.queue_page('<script>alert("x")</script>')
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_documents_page_also_shows_and_escapes_the_candidate_name():
    html = pages.documents_page('<script>alert("x")</script>')
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_documents_page_includes_the_profile_chat():
    assert 'id="profile-chat"' in pages.documents_page()


def test_pages_have_no_placeholders_left():
    for html in (pages.queue_page("Jane"), pages.documents_page(), pages.settings_page(),
                 pages.login_page(), pages.signup_page()):
        assert "{nav}" not in html
        assert "{candidate_name}" not in html


def test_static_assets_resolve():
    for name in ("ds-styles.css", "base.css", "queue.js", "documents.js",
                 "queue.css", "documents.css", "nav.js", "auth.css", "auth.js",
                 "settings.js", "settings.css", "logo-mark.svg", "logo-reed.png",
                 "logo-adzuna.png", "logo-gemini.svg", "logo-openai.svg",
                 "logo-anthropic.svg", "logo-openrouter.svg"):
        asset = pages.static_asset(name)
        assert asset is not None, name
        assert asset[0]


def test_static_refuses_traversal_and_unknown_types():
    assert pages.static_asset("../handler.py") is None
    assert pages.static_asset("..%2Fhandler.py") is None
    assert pages.static_asset("queue.html.bak") is None
    assert pages.static_asset(".hidden.css") is None


def test_images_are_served_with_image_types():
    assert pages.static_asset("logo-mark.svg")[1] == "image/svg+xml"
    assert pages.static_asset("logo-reed.png")[1] == "image/png"


def test_nav_carries_the_brand_mark():
    assert 'src="/static/logo-mark.svg"' in pages.nav("queue")
