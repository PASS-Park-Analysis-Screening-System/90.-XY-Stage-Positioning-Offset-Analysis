"""3D 트러블슈팅 보고서(HTML 한 파일)와 사례 라이브러리.

진단 dict(pass.xy.diagnosis/1)를 틀의 `/*__PASS_DIAG__*/null` 자리에 넣는다. 데이터 속 문자열이
<script>를 닫거나 스크립트를 깨면 안 되고, 모든 칸은 null일 수 있다. 번들 사례 페이지에는
고객사 이름·공유 폴더 경로가 남으면 안 된다.
"""
import base64
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import troubleshoot_report as tr  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / 'tests' / 'fixtures' / 'troubleshoot'
RESOURCES = PROJECT / 'src' / 'resources'
CASE_DIR = RESOURCES / 'cases' / '2026-11_xy-scanner-rotation'
EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')

NULL_DIAG = {key: None for key in (
    'generated', 'app_version', 'source', 'spec', 'verdict', 'shape', 'scanner_offsets', 'rotation',
    'positioning_log', 'drift', 'transient', 'die_field', 'whatif', 'parts', 'actions', 'warnings',
    'rotation_test')}
NULL_DIAG['schema'] = 'pass.xy.diagnosis/1'
HOSTILE = '</script><script>window.__pwned=1</script><!--<script> \u2028 \u2029 $1 \\ "q"'


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


@pytest.fixture(params=['diag_ref_old_m25.json', 'diag_ref_new_m5.json'])
def ref_diag(request):
    return _fixture(request.param)


class _Scripts(HTMLParser):
    """HTML 파서가 실제로 본 <script> 블록들 (데이터가 태그를 닫으면 개수·내용이 달라진다)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks, self._cur = [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self._cur = []

    def handle_endtag(self, tag):
        if tag == 'script' and self._cur is not None:
            self.blocks.append(''.join(self._cur))
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None:
            self._cur.append(data)


def _scripts(html):
    p = _Scripts()
    p.feed(html)
    return p.blocks


def _injected(html):
    """보고서 HTML에서 DIAG로 들어간 JSON 텍스트."""
    block = next(b for b in _scripts(html) if 'const DIAG' in b)
    return block.split('const DIAG = ', 1)[1].rsplit(';', 1)[0]


# ── 데이터 넣기 ────────────────────────────────────────────────────────────

def test_template_has_marker_once_and_no_external_resources():
    text = Path(tr.template_path()).read_text(encoding='utf-8')
    assert text.count(tr.DIAG_MARKER) == 1
    assert 'window.onerror' in text
    # 네트워크·CDN·외부 글꼴·외부 스크립트 없음 (SVG 이름공간 문자열만 허용)
    urls = set(re.findall(r'https?://[^\s"\'`)]+', text))
    assert urls <= {'http://www.w3.org/2000/svg'}
    assert not re.search(r'<script[^>]+src=|<link[^>]+href=|@import|@font-face', text, re.I)


def test_reference_fixtures_round_trip(ref_diag):
    html = tr.build_report_html(ref_diag)
    assert tr.DIAG_MARKER not in html
    assert json.loads(_injected(html)) == ref_diag


def test_hostile_strings_cannot_close_the_script():
    diag = _fixture('diag_ref_new_m5.json')
    diag['source']['sample'] = HOSTILE
    diag['parts'][0]['reason'] = HOSTILE
    diag['actions'].append(HOSTILE)
    html = tr.build_report_html(diag)
    template_scripts = _scripts(Path(tr.template_path()).read_text(encoding='utf-8'))

    blocks = _scripts(html)
    assert len(blocks) == len(template_scripts)          # 데이터가 블록을 더 만들거나 끊지 않음
    data = _injected(html)
    assert '</' not in data and '<!--' not in data
    assert '\u2028' not in data and '\u2029' not in data
    assert '__pwned' not in ''.join(b for b in blocks if 'const DIAG' not in b)
    assert json.loads(data) == diag                     # 이스케이프는 값 자체를 바꾸지 않는다


def test_nan_and_numpy_like_values_become_json():
    class Scalar:      # numpy 수처럼 item()이 있는 값
        def item(self):
            return 0.25

    diag = copy.deepcopy(NULL_DIAG)
    diag['verdict'] = {'X': {'range': float('nan'), 'stddev': Scalar(), 'pass': None},
                       'Y': {'range': float('inf'), 'stddev': None, 'pass': True}}
    data = json.loads(_injected(tr.build_report_html(diag)))
    assert data['verdict']['X'] == {'range': None, 'stddev': 0.25, 'pass': None}
    assert data['verdict']['Y']['range'] is None


@pytest.mark.parametrize('diag', [NULL_DIAG, {'schema': 'pass.xy.diagnosis/1'}, {}],
                         ids=['all-null', 'schema-only', 'empty'])
def test_null_diag_builds(diag):
    html = tr.build_report_html(diag)
    assert json.loads(_injected(html)) == diag
    # 비어 있을 때의 문구가 틀에 있다 (그리기는 아래 브라우저 시험에서 확인)
    assert '데이터 없음' in html
    assert '미측정 — 출하 검사/스캐너 교체·선형화 후 필수' in html


def test_template_without_marker_is_rejected(tmp_path, monkeypatch):
    bad = tmp_path / 'report_template.html'
    bad.write_text('<script>const DIAG = null;</script>', encoding='utf-8')
    monkeypatch.setattr(tr, 'template_path', lambda: str(bad))
    with pytest.raises(ValueError):
        tr.build_report_html(NULL_DIAG)


# ── 파일 쓰기 ──────────────────────────────────────────────────────────────

def test_write_report_writes_html_and_json_side_by_side(tmp_path):
    diag = _fixture('diag_ref_new_m5.json')
    path = Path(tr.write_report(diag, str(tmp_path)))

    assert path.parent == tmp_path and path.suffix == '.html'
    assert re.fullmatch(r'XY_troubleshoot_\d{8}_\d{6}_Sample19\.html', path.name)
    assert json.loads(path.with_suffix('.json').read_text(encoding='utf-8')) == diag
    assert json.loads(_injected(path.read_text(encoding='utf-8'))) == diag


def test_write_report_never_overwrites_and_sanitizes_sample(tmp_path):
    diag = copy.deepcopy(NULL_DIAG)
    diag['source'] = {'sample': 'a/b:c*?<lot> 1'}
    first = Path(tr.write_report(diag, str(tmp_path)))
    second = Path(tr.write_report(diag, str(tmp_path)))

    assert first != second and first.exists() and second.exists()
    for p in (first, second):
        assert re.search(r'_a_b_c_lot_1(_\d+)?\.html$', p.name), p.name
    assert len(list(tmp_path.glob('*.json'))) == 2


def test_write_report_default_folder_is_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    path = Path(tr.write_report(NULL_DIAG))
    assert path.parent == tmp_path / 'PASS' / 'XYStage' / 'reports'
    assert path.name.endswith('_unknown.html')


# ── 리소스 위치 (소스 / 빌드본) ─────────────────────────────────────────────

def test_case_library_path_from_source():
    path = Path(tr.case_library_path())
    assert path == RESOURCES / 'cases' / 'index.html'
    assert path.is_file()
    assert Path(tr.browsable_case_library()) == path       # 소스 실행은 복사하지 않는다
    assert Path(tr.template_path()) == RESOURCES / 'troubleshoot' / 'report_template.html'


def test_frozen_paths_follow_meipass_and_bundle_targets(tmp_path, monkeypatch):
    meipass = tmp_path / 'meipass'
    shutil.copytree(RESOURCES / 'cases', meipass / 'resources' / 'cases')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(meipass), raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))

    assert Path(tr.case_library_path()) == meipass / 'resources' / 'cases' / 'index.html'
    assert Path(tr.template_path()) == meipass / 'resources' / 'troubleshoot' / 'report_template.html'
    # onefile의 _MEIPASS는 앱을 끝내면 지워지므로 사용자 폴더로 복사해서 연다
    opened = Path(tr.browsable_case_library())
    assert opened == tmp_path / 'local' / 'PASS' / 'XYStage' / 'cases' / 'index.html'
    assert (opened.parent / '2026-11_xy-scanner-rotation' / 'xy-explainer-3d.html').is_file()

    # 런타임 조회 경로 = build_config의 DATAS 대상
    sys.path.insert(0, str(PROJECT / 'tools'))
    try:
        import build_config as cfg
    finally:
        sys.path.remove(str(PROJECT / 'tools'))
    datas = {src.replace(os.sep, '/'): dest.replace(os.sep, '/') for src, dest in cfg.DATAS}
    assert datas['src/resources/troubleshoot'] == 'resources/troubleshoot'
    assert datas['src/resources/cases'] == 'resources/cases'
    required = {p.replace(os.sep, '/') for p in cfg.REQUIRED_IN_BUNDLE}
    assert {'resources/troubleshoot/report_template.html', 'resources/cases/index.html'} <= required
    assert cfg.missing_sources() == []


def test_case_links_resolve():
    pages = [RESOURCES / 'cases' / 'index.html'] + sorted(CASE_DIR.glob('*.html'))
    assert len(pages) == 5
    for page in pages:
        for href in re.findall(r'href="([^"#:]+\.html)"', page.read_text(encoding='utf-8')):
            assert (page.parent / href).is_file(), f'{page.name} → {href}'


# ── 개인정보 (번들 사본) ────────────────────────────────────────────────────

# 고객사 이름·장비 일련번호 같은 금지어는 이 저장소에도 평문으로 남기지 않으려고 base64로 둔다
_FORBIDDEN_B64 = ('amhpY2M=', 'ZnVqaWFu', 'amluaHVh', '5pmL5Y2O', 'YzE2MDEyNA==', 'MTAuNjAuMjAuMTE=')
_FORBIDDEN_RE = (
    re.compile(r'\\\\\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'),        # \\10.x.x.x\share
    re.compile(r'//\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'),          # //10.x.x.x/share
    re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]+Users[\\/]', re.I),  # 개인 PC 경로
)


def _bundled_text_files():
    files = [p for p in (RESOURCES / 'cases').rglob('*') if p.is_file()]
    files += [p for p in (RESOURCES / 'troubleshoot').rglob('*') if p.is_file()]
    assert len(files) >= 7
    return files


def test_bundled_pages_have_no_customer_or_share_path():
    words = [base64.b64decode(w).decode('utf-8').lower() for w in _FORBIDDEN_B64]
    hits = []
    for path in _bundled_text_files():
        text = path.read_text(encoding='utf-8')
        low = text.lower()
        hits += [f'{path.name}: 금지어 {i}' for i, w in enumerate(words) if w in low]
        hits += [f'{path.name}: {m.group(0)}' for rx in _FORBIDDEN_RE for m in rx.finditer(text)]
    assert hits == []


def test_bundled_case_keeps_model_and_corrected_wording():
    final = (CASE_DIR / 'xy-root-cause-final.html').read_text(encoding='utf-8')
    assert 'Park NX-Wafer 300 mm' in final and '고객사 장비' in final
    for page in CASE_DIR.glob('*.html'):
        assert not re.search(r'96\s?%', page.read_text(encoding='utf-8')), page.name   # 정정 전 문구


# ── 브라우저에서 실제로 그려지는가 (Edge가 있을 때) ─────────────────────────

@pytest.mark.skipif(not EDGE.exists(), reason='Microsoft Edge 없음')
@pytest.mark.parametrize('name', ['diag_ref_old_m25.json', 'diag_ref_new_m5.json', 'null', 'hostile'])
def test_report_renders_without_script_error(tmp_path, name):
    if name == 'null':
        diag = NULL_DIAG
    elif name == 'hostile':
        diag = _fixture('diag_ref_new_m5.json')
        diag['source']['sample'] = HOSTILE
        diag['parts'][1]['reason'] = HOSTILE
    else:
        diag = _fixture(name)
    page = tmp_path / 'report.html'
    page.write_text(tr.build_report_html(diag), encoding='utf-8')
    with tempfile.TemporaryDirectory() as profile:
        out = subprocess.run([str(EDGE), '--headless=new', '--disable-gpu', '--dump-dom',
                              '--virtual-time-budget=3000', f'--user-data-dir={profile}', page.as_uri()],
                             capture_output=True, timeout=90)
    dom = out.stdout.decode('utf-8', 'replace')
    assert 'id="tree"' in dom, out.stderr[-500:]
    assert re.search(r'<div id="errBanner" role="alert" hidden="">', dom), '스크립트 오류 배너가 떴습니다'
    assert len(_scripts(dom)) == 3                             # 데이터가 새 스크립트 요소를 만들지 않음
    assert 'class="lv' in dom                                  # 판단 트리까지 그려짐
    if name == 'hostile':
        assert '&lt;/script&gt;&lt;script&gt;window.__pwned=1' in dom   # 글자로만 보인다
    if name == 'null':
        assert dom.count('데이터 없음') > 20
        assert '미측정 — 출하 검사/스캐너 교체·선형화 후 필수' in dom
    else:
        assert '권장' in dom and 'class="opt on"' in dom


# ── 화면: 진단 없이 누르면 메시지만, 진단이 있으면 보고서를 쓰고 브라우저로 ──────────

@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui_calls(app, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox
    calls = {'info': [], 'warning': [], 'open': []}
    monkeypatch.setattr(QMessageBox, 'information', staticmethod(lambda *a, **k: calls['info'].append(a)))
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: calls['warning'].append(a)))
    monkeypatch.setattr(QDesktopServices, 'openUrl', staticmethod(lambda url: calls['open'].append(url) or True))
    return calls


def test_launcher_without_diagnosis_shows_message(app, ui_calls):
    from PySide6.QtWidgets import QWidget
    from ui.dialogs import troubleshoot_launcher as tl
    window = QWidget()

    assert tl.open_troubleshoot_report(window) is None
    window._xy_diagnosis = None
    assert tl.open_troubleshoot_report(window) is None

    assert [c[2] for c in ui_calls['info']] == [tl.NO_DIAGNOSIS_MESSAGE] * 2
    assert '먼저 Diagnosis 탭에서 진단을 실행하세요' in tl.NO_DIAGNOSIS_MESSAGE
    assert ui_calls['open'] == [] and ui_calls['warning'] == []


def test_launcher_writes_report_and_opens_browser(app, ui_calls, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QWidget
    from ui.dialogs import troubleshoot_launcher as tl
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    diag = _fixture('diag_ref_old_m25.json')

    class Diagnosis:          # 작업 A가 dict 대신 객체를 둘 수도 있다
        def to_dict(self):
            return diag

    window = QWidget()
    for value in (diag, Diagnosis()):
        window._xy_diagnosis = value
        path = Path(tl.open_troubleshoot_report(window))
        assert path.is_file() and path.parent == tmp_path / 'PASS' / 'XYStage' / 'reports'
        assert Path(ui_calls['open'][-1].toLocalFile()) == path
    assert ui_calls['info'] == [] and ui_calls['warning'] == []

    library = Path(tl.open_case_library(window))
    assert library == RESOURCES / 'cases' / 'index.html'
    assert Path(ui_calls['open'][-1].toLocalFile()) == library


def test_export_buttons_open_launchers(app, ui_calls, monkeypatch):
    """Remark → Export에 두 버튼이 있고, 진단 없이 누르면 메시지만 뜬다 (멈추지 않음)."""
    import main
    import ui.controllers.scan_controller as controller
    from PySide6.QtWidgets import QPushButton
    monkeypatch.setattr(main, 'load_settings', lambda: {})
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    monkeypatch.setattr(controller, 'add_recent_folder', lambda s, _f: s)
    win = main.DataAnalyzerApp()
    monkeypatch.setattr(win, '_save_settings', lambda: None)
    try:
        buttons = {b.text(): b for b in win.findChildren(QPushButton)}
        assert '사례 라이브러리' in buttons
        buttons['3D 트러블슈팅 보고서'].click()
        assert ui_calls['info'] and ui_calls['info'][-1][2] == '먼저 Diagnosis 탭에서 진단을 실행하세요.'
        assert ui_calls['open'] == []
        buttons['사례 라이브러리'].click()
        assert Path(ui_calls['open'][-1].toLocalFile()) == RESOURCES / 'cases' / 'index.html'
    finally:
        for thread in win._background_threads():
            if hasattr(thread, 'cancel'):
                thread.cancel()
            thread.wait(10000)
        win.deleteLater()
        app.processEvents()
