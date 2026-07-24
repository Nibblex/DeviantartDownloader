"""Detecting text works, classifying their body and rendering it."""

import json

from deviantart_downloader import literature
from deviantart_downloader.literature import KIND_HTML, KIND_TEXT, KIND_TIPTAP


def _tiptap_doc(*paragraphs):
    """A tiptap markup string; each argument is a paragraph's inline nodes."""
    return json.dumps({"document": {"type": "doc", "content": list(paragraphs)}})


def _para(*nodes):
    return {"type": "paragraph", "content": list(nodes)}


def _text(value, *marks):
    node = {"type": "text", "text": value}
    if marks:
        node["marks"] = [{"type": m} for m in marks]
    return node


class TestIsTextWork:
    def test_media_work_is_not_text(self):
        assert literature.is_text_work({"content": {"src": "x"}}) is False

    def test_website_literature_by_type(self):
        assert literature.is_text_work({"type": "literature", "content": None}) is True
        assert literature.is_text_work({"type": "journal"}) is True

    def test_api_literature_by_excerpt(self):
        assert literature.is_text_work({"excerpt": "a poem"}) is True
        assert literature.is_text_work({"text_content": {"excerpt": "x"}}) is True

    def test_plain_entry_without_text_is_not_text(self):
        assert literature.is_text_work({"type": "image"}) is False
        assert literature.is_text_work({}) is False


class TestClassifyWebHtml:
    def test_tiptap_and_writer_are_distinguished(self):
        assert literature.classify_web_html(
            {"type": "tiptap", "markup": "{}"}) == (KIND_TIPTAP, "{}")
        assert literature.classify_web_html(
            {"type": "writer", "markup": "<p>x</p>"}) == (KIND_HTML, "<p>x</p>")

    def test_empty_or_missing_markup_is_none(self):
        assert literature.classify_web_html({"type": "tiptap", "markup": ""}) is None
        assert literature.classify_web_html({"markup": "   "}) is None
        assert literature.classify_web_html(None) is None


class TestRenderText:
    def test_tiptap_to_text(self):
        markup = _tiptap_doc(
            _para(_text("Line one"), {"type": "hardBreak"}, _text("Line two")),
            _para(),                                        # blank spacer line
            _para(_text("Second stanza")),
        )
        assert literature.render(KIND_TIPTAP, markup, "txt") == (
            "Line one\nLine two\n\nSecond stanza")

    def test_html_to_text_strips_tags(self):
        html = "<p>First line</p><p>Second<br>third</p>"
        assert literature.render(KIND_HTML, html, "txt") == "First line\nSecond\nthird"

    def test_plain_text_is_cleaned(self):
        assert literature.render(KIND_TEXT, "  a  \n\n\n\nb  ", "txt") == "a\n\nb"

    def test_malformed_tiptap_json_is_empty(self):
        assert literature.render(KIND_TIPTAP, "{not json", "txt") == ""


class TestRenderHtml:
    def test_tiptap_to_html_keeps_structure_and_marks(self):
        markup = _tiptap_doc(
            {"type": "heading", "attrs": {"level": 2}, "content": [_text("Title")]},
            _para(_text("plain "), _text("bold", "bold"),
                  {"type": "hardBreak"}, _text("next")),
        )
        html = literature.render(KIND_TIPTAP, markup, "html")
        assert html == ("<h2>Title</h2>\n"
                        "<p>plain <strong>bold</strong><br>\nnext</p>")

    def test_tiptap_escapes_text(self):
        markup = _tiptap_doc(_para(_text("a < b & c")))
        assert literature.render(KIND_TIPTAP, markup, "html") == "<p>a &lt; b &amp; c</p>"

    def test_html_kind_is_passed_through(self):
        assert literature.render(KIND_HTML, "<p>raw</p>", "html") == "<p>raw</p>"

    def test_plain_text_becomes_escaped_paragraphs(self):
        out = literature.render(KIND_TEXT, "Tom & Jerry\nline two\n\npara two", "html")
        assert out == "<p>Tom &amp; Jerry<br>line two</p>\n<p>para two</p>"

    def test_renders_links(self):
        markup = _tiptap_doc(_para(
            dict(_text("click"), marks=[{"type": "link",
                                         "attrs": {"href": "https://x/?a=1&b=2"}}])))
        assert literature.render(KIND_TIPTAP, markup, "html") == (
            '<p><a href="https://x/?a=1&amp;b=2">click</a></p>')

    def test_renders_lists_blockquote_code_and_rule(self):
        markup = _tiptap_doc(
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [_para(_text("one"))]},
                {"type": "listItem", "content": [_para(_text("two"))]}]},
            {"type": "blockquote", "content": [_para(_text("quoted"))]},
            {"type": "codeBlock", "content": [_text("x = 1")]},
            {"type": "horizontalRule"},
        )
        html = literature.render(KIND_TIPTAP, markup, "html")
        assert "<ul><li><p>one</p></li><li><p>two</p></li></ul>" in html
        assert "<blockquote><p>quoted</p></blockquote>" in html
        assert "<pre><code>x = 1</code></pre>" in html
        assert "<hr>" in html

    def test_malformed_tiptap_json_renders_empty(self):
        assert literature.render(KIND_TIPTAP, "{not json", "html") == ""


class TestHtmlDocument:
    def test_wraps_body_with_title_and_charset(self):
        doc = literature.html_document("My <Poem>", "<p>body</p>")
        assert doc.startswith("<!DOCTYPE html>")
        assert '<meta charset="utf-8">' in doc
        assert "<title>My &lt;Poem&gt;</title>" in doc
        assert "<body>\n<p>body</p>\n</body>" in doc
