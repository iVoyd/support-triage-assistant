import importlib.util
import xml.dom.minidom
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "term_svg", Path(__file__).parent.parent / "scripts" / "term_svg.py"
)
term_svg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(term_svg)


def test_render_is_valid_svg_and_keeps_text():
    text = "$ triage review\r\n\x1b[1m#1 Title <with> & chars\x1b[0m\n  indented\n" + "x" * 200
    svg = term_svg.render(text, "demo")
    doc = xml.dom.minidom.parseString(svg)
    texts = [t.firstChild.data for t in doc.getElementsByTagName("text") if t.firstChild]
    assert "#1\u00a0Title\u00a0<with>\u00a0&\u00a0chars" in texts  # ANSI stripped, escaped
    assert "\u00a0\u00a0indented" in texts  # indentation kept
    assert any(t.endswith("…") and len(t) == term_svg.WIDTH for t in texts)  # long line cut
