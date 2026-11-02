"""실행 버전 표시와 버전 출처의 회귀 테스트."""

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from core.version import APP_VERSION, APP_VERSION_LABEL

PROJECT = Path(__file__).resolve().parents[1]


def _build_config():
    spec = importlib.util.spec_from_file_location('build_config', PROJECT / 'tools' / 'build_config.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_version_is_1_1_3():
    assert APP_VERSION == '1.1.3'
    assert APP_VERSION_LABEL == 'v1.1.3'


def test_build_config_reads_the_same_version():
    assert _build_config().app_version() == APP_VERSION


def test_no_hardcoded_app_version_in_source():
    pattern = re.compile(r'\bv\d+\.\d+')
    hits = [str(p.relative_to(PROJECT)) for p in (PROJECT / 'src').rglob('*.py')
            if p.name != 'version.py' and pattern.search(p.read_text(encoding='utf-8'))]

    assert hits == []


def test_module_json_version_check(tmp_path):
    cfg = _build_config()
    module_json = tmp_path / 'module.json'

    module_json.write_text(json.dumps({'version': APP_VERSION}), encoding='utf-8')
    assert cfg.module_version_error(str(module_json)) == ''

    module_json.write_text(json.dumps({'version': '0.0.1'}), encoding='utf-8')
    assert '0.0.1' in cfg.module_version_error(str(module_json))


@pytest.fixture
def window(monkeypatch):
    """화면에 띄우지 않고 실제 창을 만든다. 설정 파일과 레지스트리는 건드리지 않는다."""
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import main
    monkeypatch.setattr(main, 'load_settings', lambda: {})
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    yield win
    win.deleteLater()
    app.processEvents()


def test_version_stays_visible_in_title_and_status_bar(window):
    from PySide6.QtWidgets import QLabel

    assert APP_VERSION_LABEL in window.windowTitle()
    assert window.version_status_label.text() == APP_VERSION_LABEL
    assert window.version_status_label.parent() is window.statusBar()

    # 스캔·Step 전환의 상태 메시지가 영구 표시를 덮어쓰지 않는다
    window.statusBar().showMessage('스캔 중...')
    assert window.statusBar().currentMessage() == '스캔 중...'
    assert not window.version_status_label.isHidden()

    labels = [w.text() for w in window.findChildren(QLabel)]
    help_labels = [t for t in labels if 'Production & Quality Control Team' in t]
    assert help_labels and all(t.startswith(APP_VERSION_LABEL + ' ') for t in help_labels)
