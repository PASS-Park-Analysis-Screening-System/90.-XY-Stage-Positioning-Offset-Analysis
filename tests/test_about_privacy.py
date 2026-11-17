"""정보창(도움말 개발자 정보)에 개인 이메일이 없고 PASS 요청 창구 안내가 있다."""
import pathlib
import re

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def test_about_has_no_email_and_shows_request_desk():
    text = (_SRC / "ui" / "controllers" / "ui_builder_mixin.py").read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", text)
    assert "문의: PASS 요청 창구(MS Forms)" in text


def test_no_email_in_any_source():
    for p in _SRC.rglob("*.py"):
        assert not re.search(r"[\w.+-]+@[\w-]+\.(com|net|co\.kr)\b", p.read_text(encoding="utf-8")), p
