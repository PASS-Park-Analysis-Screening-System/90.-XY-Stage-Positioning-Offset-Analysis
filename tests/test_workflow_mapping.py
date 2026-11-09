"""Workflow별 데이터 폴더 지정 — 폴더명이 Spec 이름과 달라도 분석할 수 있어야 한다.

배경: 루트 아래 폴더를 모두 Recipe로 보고, 이름이 Spec 키와 다르면 분석을 통째로
막았다. Lot 1..14만 있는 Vision Pattern 폴더, 반복 수가 다른 시험 폴더, 날짜 폴더를
분석할 수 없었다. 또 'vision pattern' 폴더는 이름 검사는 통과했지만 판정·Cpk가 비었다.
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import recipe_scanner, settings
from core.recipe_scanner import (build_recipe, load_all_recipes, normalize_spec_names,
                                 recipes_from_mapping, scan_recipes)
from core.workflow_map import (common_source_folder, compute_prefill, describe_folder,
                               validate_rows)

SPECS = ['Vision Pattern', 'In-Die Align', 'LLC Translation', 'Global Align']

# ── 픽스처: 실데이터(SmartScan)와 같은 구조의 최소 Lot 폴더 ─────────────────
X_UL_CSV = (
    "Lot ID,Lot4\r\n"
    "Recipe ID,03. XY Stage\\1. Vision Pattern Recognize\r\n"
    "Sample ID,Sample10\r\n"
    "\r\n"
    "\r\n"
    "Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,"
    "HZ1_O (nm),HZ1_O_Valid\r\n"
    "0001_X000_Y000,0,0,1,4305.72,5726,X,COMPLETED,TRUE,2795.41,TRUE\r\n"
    "0002_X002_Y000,2,0,1,24305.72,5726,X,COMPLETED,TRUE,2888.183,TRUE\r\n"
)
SUMMARY_CSV = (
    "Lot ID,Lot4\r\n"
    "Sample ID,Sample10\r\n"
    "\r\n"
    "X_UL\r\n"
    "ITEM,HZ1_O (nm),\r\n"
    "MEAN,2369.052,\r\n"
    "STDEV,1817.046,\r\n"
    "MIN,-3454.59,\r\n"
    "MAX,3210.449,\r\n"
    "RANGE,6665.039,\r\n"
)


def _make_lots(folder, names):
    """folder 아래에 Lot 폴더들을 만든다 (CRLF 원문 그대로 바이트로 쓴다)."""
    for name in names:
        lot = folder / str(name)
        lot.mkdir(parents=True, exist_ok=True)
        (lot / "Sample10_X_UL.csv").write_bytes(X_UL_CSV.encode())
        (lot / "Sample10.csv").write_bytes(SUMMARY_CSV.encode())
    return folder


# ── build_recipe / scan_recipes ─────────────────────────────────────

def test_build_recipe_flat_lots_is_root_round(tmp_path):
    folder = _make_lots(tmp_path / 'lots', range(1, 15))
    recipe = build_recipe(str(folder))
    assert recipe['name'] == 'lots'
    assert recipe['rounds'] == [{'name': '(root)', 'path': str(folder), 'lot_count': 14}]


def test_build_recipe_round_folders(tmp_path):
    folder = tmp_path / '1. Vision Pattern Recognize'
    _make_lots(folder / '1st', ['Lot1', 'Lot2'])
    _make_lots(folder / '2nd', ['Lot1'])
    (folder / 'empty').mkdir()
    recipe = build_recipe(str(folder))
    assert [(r['name'], r['lot_count']) for r in recipe['rounds']] == [('1st', 2), ('2nd', 1)]
    assert recipe['short_name'] == 'Vision Pattern Rec…'
    assert recipe['index'] == 1


def test_build_recipe_without_lots_is_none(tmp_path):
    (tmp_path / 'a' / 'b').mkdir(parents=True)
    assert build_recipe(str(tmp_path / 'a')) is None
    assert build_recipe(str(tmp_path / 'missing')) is None
    assert build_recipe('') is None


def test_scan_recipes_output_is_unchanged(tmp_path):
    _make_lots(tmp_path / '2. In-Die Align' / '1st', ['L1'])
    _make_lots(tmp_path / '1. Vision Pattern Recognize', ['L1', 'L2'])
    _make_lots(tmp_path / 'misc' / '1st', ['L1'])
    _make_lots(tmp_path / 'zeta', ['L1'])
    (tmp_path / 'nothing').mkdir()
    (tmp_path / 'file.txt').write_text('x')

    def p(*parts):
        return str(tmp_path.joinpath(*parts))

    assert scan_recipes(str(tmp_path)) == [
        {'name': '1. Vision Pattern Recognize', 'path': p('1. Vision Pattern Recognize'),
         'index': 1, 'short_name': 'Vision Pattern Rec…',
         'rounds': [{'name': '(root)', 'path': p('1. Vision Pattern Recognize'), 'lot_count': 2}]},
        {'name': '2. In-Die Align', 'path': p('2. In-Die Align'), 'index': 2,
         'short_name': 'In-Die Align',
         'rounds': [{'name': '1st', 'path': p('2. In-Die Align', '1st'), 'lot_count': 1}]},
        {'name': 'misc', 'path': p('misc'), 'index': 3, 'short_name': 'misc',
         'rounds': [{'name': '1st', 'path': p('misc', '1st'), 'lot_count': 1}]},
        {'name': 'zeta', 'path': p('zeta'), 'index': 4, 'short_name': 'zeta',
         'rounds': [{'name': '(root)', 'path': p('zeta'), 'lot_count': 1}]},
    ]


# ── recipes_from_mapping / load_all_recipes ─────────────────────────

def test_mapping_with_only_vision_pattern(tmp_path):
    folder = _make_lots(tmp_path / '20261101', range(1, 15))
    recipes, problems = recipes_from_mapping([('Vision Pattern', str(folder))], SPECS)
    assert problems == []
    assert len(recipes) == 1
    r = recipes[0]
    assert (r['name'], r['short_name'], r['index']) == ('Vision Pattern', 'Vision Pattern', 1)
    assert r['source_path'] == str(folder)
    assert r['source_label'] == '20261101'
    assert r['rounds'][0]['lot_count'] == 14


def test_mapping_follows_spec_order_and_skips_unlisted(tmp_path):
    ga = _make_lots(tmp_path / 'ga', ['L1'])
    vp = _make_lots(tmp_path / 'vp' / '1st', ['L1']).parent
    recipes, problems = recipes_from_mapping(
        [('Global Align', str(ga)), ('Vision Pattern', str(vp))], SPECS)
    assert problems == []
    assert [(r['index'], r['short_name']) for r in recipes] == [
        (1, 'Vision Pattern'), (2, 'Global Align')]


def test_mapping_reports_bad_paths(tmp_path):
    good = _make_lots(tmp_path / 'good', ['L1'])
    (tmp_path / 'nolots').mkdir()
    recipes, problems = recipes_from_mapping([
        ('Vision Pattern', str(good)),
        ('In-Die Align', str(tmp_path / 'missing')),
        ('LLC Translation', str(tmp_path / 'nolots')),
    ], SPECS)
    assert [r['short_name'] for r in recipes] == ['Vision Pattern']
    assert problems == [
        ('In-Die Align', str(tmp_path / 'missing'), '폴더 없음'),
        ('LLC Translation', str(tmp_path / 'nolots'), 'Lot 데이터 없음'),
    ]


def test_load_all_recipes_uses_given_recipes(tmp_path, monkeypatch):
    folder = _make_lots(tmp_path / 'vp', ['L1', 'L2'])
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(folder))], SPECS)

    def must_not_scan(_root):
        raise AssertionError('scan_recipes를 부르면 안 된다')
    monkeypatch.setattr(recipe_scanner, 'scan_recipes', must_not_scan)

    results = load_all_recipes(str(tmp_path / 'unrelated'), recipes=recipes)

    assert len(results) == 1
    assert results[0]['short_name'] == 'Vision Pattern'
    assert results[0]['source_path'] == str(folder)
    assert results[0]['raw_data']


def test_auto_scan_results_have_no_source_path(tmp_path):
    _make_lots(tmp_path / 'Vision Pattern', ['L1'])
    results = load_all_recipes(str(tmp_path))
    assert 'source_path' not in results[0]


def test_normalize_sets_exact_spec_key():
    recipes = [{'name': 'vision pattern', 'short_name': 'vision pattern'},
               {'name': '4. Global Align', 'short_name': 'Global Align'},
               {'name': '2. IN-DIE ALIGN', 'short_name': 'IN-DIE ALIGN'},
               {'name': '20261101', 'short_name': '20261101'}]
    mismatched = normalize_spec_names(recipes, SPECS)
    assert mismatched == ['20261101']
    assert [r['short_name'] for r in recipes[:3]] == [
        'Vision Pattern', 'Global Align', 'In-Die Align']


def test_lowercase_folder_gets_verdict_lookup_key(tmp_path):
    """'vision pattern' 폴더도 spec_deviation['Vision Pattern']을 찾아야 한다."""
    _make_lots(tmp_path / 'vision pattern', ['L1'])
    recipes = scan_recipes(str(tmp_path))
    assert normalize_spec_names(recipes, SPECS) == []
    results = load_all_recipes(str(tmp_path), recipes=recipes)
    assert results[0]['short_name'] == 'Vision Pattern'


# ── 대화상자 로직 (화면 없이) ───────────────────────────────────────

def test_describe_folder_texts(tmp_path):
    flat = _make_lots(tmp_path / 'flat', range(1, 15))
    rounds = tmp_path / 'rounds'
    _make_lots(rounds / '1st', ['a', 'b'])
    _make_lots(rounds / '2nd', ['a'])
    (tmp_path / 'nolots').mkdir()
    assert describe_folder(str(flat)) == (True, '✅ Lot 14개 · (root)')
    assert describe_folder(str(rounds)) == (True, '✅ 1st Lot 2개, 2nd Lot 1개')
    assert describe_folder(str(tmp_path / 'nolots')) == (False, '❌ Lot 데이터 없음')
    assert describe_folder(str(tmp_path / 'missing')) == (False, '❌ 폴더 없음')
    assert describe_folder('  ')[0] is False


def test_prefill_matches_root_subfolders_then_remembered(tmp_path):
    root = tmp_path / 'root'
    _make_lots(root / '1. vision pattern', ['L1'])
    _make_lots(root / '20261101', ['L1'])
    remembered_ida = _make_lots(tmp_path / 'old_ida', ['L1'])
    remembered = {
        'Vision Pattern': {'path': str(tmp_path / 'old_vp'), 'enabled': True},
        'In-Die Align': {'path': str(remembered_ida), 'enabled': True},
        'LLC Translation': {'path': str(tmp_path / 'gone'), 'enabled': True},
    }

    prefill = compute_prefill(SPECS, str(root), remembered)

    assert prefill == {
        'Vision Pattern': {'path': str(root / '1. vision pattern'), 'enabled': True},
        'In-Die Align': {'path': str(remembered_ida), 'enabled': True},
        'LLC Translation': {'path': str(tmp_path / 'gone'), 'enabled': False},
        'Global Align': {'path': '', 'enabled': False},
    }


def test_prefill_keeps_remembered_unchecked_rows_unchecked(tmp_path):
    ida = _make_lots(tmp_path / 'ida', ['L1'])
    prefill = compute_prefill(SPECS, '', {'In-Die Align': {'path': str(ida), 'enabled': False}})
    assert prefill['In-Die Align'] == {'path': str(ida), 'enabled': False}


def test_prefill_proposes_root_that_holds_lots(tmp_path):
    """화면 사례: 고른 폴더 아래가 Lot 1..14뿐이다."""
    root = _make_lots(tmp_path / 'Vision Pattern 20261101', range(1, 15))
    prefill = compute_prefill(SPECS, str(root), {})
    assert prefill['Vision Pattern'] == {'path': str(root), 'enabled': True}
    assert all(not prefill[k]['path'] for k in SPECS[1:])


def test_validate_rows(tmp_path):
    good = _make_lots(tmp_path / 'good', ['L1'])
    ok, reasons = validate_rows([('Vision Pattern', False, str(good))])
    assert not ok and reasons == ['사용할 Workflow를 하나 이상 선택해 주세요.']

    ok, reasons = validate_rows([('Vision Pattern', True, str(good)),
                                 ('Global Align', True, str(tmp_path / 'missing'))])
    assert not ok and reasons == ['Global Align: 폴더 없음']

    ok, reasons = validate_rows([('Vision Pattern', True, str(good)),
                                 ('Global Align', False, str(tmp_path / 'missing'))])
    assert ok and reasons == []


def test_common_source_folder(tmp_path):
    a, b = str(tmp_path / 'x' / 'a'), str(tmp_path / 'x' / 'b')
    assert common_source_folder([a, b]) == str(tmp_path / 'x')
    assert common_source_folder([a]) == a
    assert common_source_folder(['C:\\a', 'D:\\b']) == 'C:\\a'
    assert common_source_folder([]) == ''


def test_dialog_ok_follows_rows(tmp_path):
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    app = QApplication.instance() or QApplication([])
    from ui.dialogs.workflow_folder_dialog import WorkflowFolderDialog
    root = _make_lots(tmp_path / 'lots', range(1, 4))

    dlg = WorkflowFolderDialog(None, SPECS, str(root), {}, '이유 문구')
    ok_btn = dlg.buttons.button(QDialogButtonBox.Ok)
    assert ok_btn.isEnabled()
    assert dlg.selected_mapping() == [('Vision Pattern', str(root))]

    check, edit, status, _timer = dlg._rows['Global Align']
    edit.setText(str(tmp_path / 'missing'))
    check.setChecked(True)
    assert not ok_btn.isEnabled()
    assert 'Global Align' in dlg.block_label.text()

    check.setChecked(False)
    assert ok_btn.isEnabled()
    assert dlg.folder_map()['Global Align'] == {'path': str(tmp_path / 'missing'), 'enabled': False}
    dlg.deleteLater()
    app.processEvents()


# ── 설정 저장 ──────────────────────────────────────────────────────

MAP = {'Vision Pattern': {'path': 'D:/data/vp', 'enabled': True},
       'Global Align': {'path': '', 'enabled': False}}


@pytest.fixture
def source_mode(tmp_path, monkeypatch):
    bundled = tmp_path / 'settings.json'
    bundled.write_text(json.dumps({'last_folder': '', 'recent_folders': []}), encoding='utf-8')
    local = tmp_path / 'settings.local.json'
    monkeypatch.setattr(settings, 'IS_FROZEN', False)
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(bundled))
    monkeypatch.setattr(settings, 'LOCAL_SETTINGS_FILE', str(local))
    return bundled, local


def test_local_settings_roundtrip_workflow_map(source_mode):
    _, local = source_mode
    loaded = settings.load_settings()
    loaded['workflow_folder_map'] = MAP
    settings.save_settings(loaded)
    assert json.loads(local.read_text(encoding='utf-8'))['workflow_folder_map'] == MAP
    assert settings.load_settings()['workflow_folder_map'] == MAP


@pytest.mark.parametrize('bad', [
    'D:/x', [], {'Vision Pattern': 'D:/x'},
    {'Vision Pattern': {'path': 3, 'enabled': True}},
    {'Vision Pattern': {'path': 'D:/x', 'enabled': 'yes'}},
    {'Vision Pattern': {'path': 'D:/x'}},
])
def test_invalid_workflow_map_is_dropped(source_mode, bad):
    _, local = source_mode
    local.write_text(json.dumps({'workflow_folder_map': bad, 'wafer_size': 200}),
                     encoding='utf-8')
    loaded = settings.load_settings()
    assert 'workflow_folder_map' not in loaded
    assert loaded['wafer_size'] == 200


def test_frozen_app_ignores_bundled_workflow_map(tmp_path, monkeypatch):
    bundled = tmp_path / 'settings.json'
    bundled.write_text(json.dumps({'workflow_folder_map': {
        'Vision Pattern': {'path': 'Z:/customer', 'enabled': True}}}), encoding='utf-8')
    monkeypatch.setattr(settings, 'IS_FROZEN', True)
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(bundled))
    monkeypatch.setattr(settings, '_read_registry_settings', lambda: {})
    assert settings.load_settings()['workflow_folder_map'] == {}


class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    REG_SZ = 1
    REG_DWORD = 4

    def __init__(self):
        self.values = {}

    def CreateKey(self, root, path):
        return _FakeKey()

    def OpenKey(self, root, path):
        return _FakeKey()

    def SetValueEx(self, key, name, reserved, value_type, value):
        self.values[name] = (value, value_type)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]


def test_registry_roundtrip_workflow_map(monkeypatch):
    fake = _FakeWinreg()
    monkeypatch.setattr(settings, 'winreg', fake)
    settings._write_registry_settings({'workflow_folder_map': MAP})
    assert fake.values['workflow_folder_map'][1] == fake.REG_SZ
    assert settings._read_registry_settings() == {'workflow_folder_map': MAP}

    fake.values['workflow_folder_map'] = (json.dumps({'Vision Pattern': 'D:/x'}), fake.REG_SZ)
    assert settings._read_registry_settings() == {}


def test_build_guard_flags_bundled_workflow_map():
    spec = importlib.util.spec_from_file_location(
        'build_config', Path(__file__).resolve().parents[1] / 'tools' / 'build_config.py')
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    errors = cfg.settings_privacy_errors({'workflow_folder_map': {
        'Vision Pattern': {'path': 'relative', 'enabled': True}}})
    assert any(e.startswith('workflow_folder_map') for e in errors)
    assert cfg.settings_privacy_errors({'workflow_folder_map': {}}) == []


# ── 스캔 화면 흐름 ─────────────────────────────────────────────────

SPEC_DEV = {'Vision Pattern': {'spec_range': 2.0, 'spec_stddev': 1.0}}


@pytest.fixture
def window(monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import main
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_deviation=SPEC_DEV))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    yield win
    win._loader_thread = None
    win.deleteLater()
    app.processEvents()


@pytest.fixture
def flow(window, monkeypatch):
    """가짜 로더 스레드와 가짜 Workflow 대화상자로 _scan_folder를 돌린다."""
    from PySide6.QtWidgets import QDialog, QMessageBox
    import ui.controllers.scan_controller as controller

    state = {'threads': [], 'dialogs': [], 'critical': [], 'recent': [], 'answer': None}

    class FakeThread:
        def __init__(self, folder, recipes=None):
            self.folder, self.recipes = folder, recipes
            self.finished = self.error = self
            state['threads'].append(self)

        def connect(self, _slot):
            pass

        def start(self):
            pass

        def isRunning(self):
            return False

    class FakeDialog:
        def __init__(self, parent, spec_keys, root, remembered, reason):
            state['dialogs'].append({'root': root, 'reason': reason, 'specs': spec_keys})

        def exec(self):
            return QDialog.Accepted if state['answer'] else QDialog.Rejected

        def selected_mapping(self):
            return state['answer']

        def folder_map(self):
            return {k: {'path': p, 'enabled': True} for k, p in state['answer']}

    monkeypatch.setattr(controller, 'DataLoaderThread', FakeThread)
    monkeypatch.setattr(controller, 'WorkflowFolderDialog', FakeDialog)
    monkeypatch.setattr(controller, 'add_recent_folder',
                        lambda s, f: state['recent'].append(f) or s)
    monkeypatch.setattr(window, '_save_settings', lambda: None)
    monkeypatch.setattr(QMessageBox, 'critical',
                        staticmethod(lambda *a, **k: state['critical'].append(a)))
    for name in ('warning', 'information'):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    return state


def test_mismatch_opens_dialog_and_loads_mapping(window, flow, tmp_path):
    root = tmp_path / 'root'
    lots = _make_lots(root / '20261101', range(1, 15))
    flow['answer'] = [('Vision Pattern', str(lots))]
    window.path_edit.setText(str(root))

    window._scan_folder()

    assert flow['critical'] == []
    assert len(flow['dialogs']) == 1
    assert flow['dialogs'][0]['root'] == str(root)
    assert '20261101' in flow['dialogs'][0]['reason']
    thread = flow['threads'][-1]
    assert thread.folder == str(lots)
    assert [(r['short_name'], r['source_path']) for r in thread.recipes] == [
        ('Vision Pattern', str(lots))]
    assert window.recipes == thread.recipes
    assert window.settings['workflow_folder_map'] == {
        'Vision Pattern': {'path': str(lots), 'enabled': True}}
    assert flow['recent'] == []     # 공통 상위 폴더는 다시 스캔할 루트가 아니다


def test_cancelled_dialog_starts_no_load(window, flow, tmp_path):
    root = tmp_path / 'root'
    _make_lots(root / '20261101', ['L1'])
    window.path_edit.setText(str(root))

    window._scan_folder()

    assert len(flow['dialogs']) == 1
    assert flow['threads'] == []
    assert flow['critical'] == []
    assert window._loader_thread is None
    assert window.recipes == []


def test_root_holding_lots_opens_dialog(window, flow, tmp_path):
    root = tmp_path / 'vp-lots'
    for i in range(1, 15):     # 화면 사례: Lot 폴더 1..14 (각 Lot 아래 Repeat 폴더 없음)
        _make_lots(root, [i])
    window.path_edit.setText(str(root))

    window._scan_folder()

    assert len(flow['dialogs']) == 1
    assert flow['dialogs'][0]['root'] == str(root)
    assert flow['threads'] == []


def test_matching_lowercase_folder_loads_with_exact_key(window, flow, tmp_path):
    _make_lots(tmp_path / '1. vision pattern', ['L1'])
    window.path_edit.setText(str(tmp_path))

    window._scan_folder()

    assert flow['dialogs'] == []
    thread = flow['threads'][-1]
    assert thread.folder == str(tmp_path)
    assert [r['short_name'] for r in thread.recipes] == ['Vision Pattern']
    assert flow['recent'] == [str(tmp_path)]


def test_workflow_button_opens_dialog_and_is_disabled_while_loading(window, flow, tmp_path):
    lots = _make_lots(tmp_path / 'any-name', ['L1'])
    flow['answer'] = [('Vision Pattern', str(lots))]
    window.path_edit.setText('')

    window._open_workflow_dialog()

    assert flow['dialogs'][0]['root'] == ''
    assert flow['threads'][-1].recipes[0]['short_name'] == 'Vision Pattern'
    assert not window.btn_workflow.isEnabled()
    window._set_scan_controls_enabled(True)
    assert window.btn_workflow.isEnabled()


# ── 내보내기 출처 ──────────────────────────────────────────────────

def _pdf_texts(monkeypatch, tmp_path, results):
    from core import pdf_generator
    seen = {'root': None, 'texts': []}
    real_title = pdf_generator._add_title_page
    real_text = pdf_generator.Figure.text

    def spy_title(pdf, root, comparison, checklist=None, notes=None):
        seen['root'] = root
        return real_title(pdf, root, comparison, checklist, notes)

    def spy_text(fig, x, y, s, *args, **kwargs):
        seen['texts'].append(s)
        return real_text(fig, x, y, s, *args, **kwargs)

    monkeypatch.setattr(pdf_generator, '_add_title_page', spy_title)
    monkeypatch.setattr(pdf_generator.Figure, 'text', spy_text)
    pdf_generator.generate_pdf_report(str(tmp_path / 'r.pdf'), 'D:/data', results, [], {}, {})
    return seen


def test_pdf_shows_source_per_workflow(monkeypatch, tmp_path):
    results = [{'short_name': 'Vision Pattern', 'round': '(root)', 'raw_data': [],
                'source_path': 'D:/data/20261101'}]
    seen = _pdf_texts(monkeypatch, tmp_path, results)
    assert seen['root'] == 'per-workflow folders (see Source on each Step page), common: D:/data'
    assert 'Source: D:/data/20261101' in seen['texts']


def test_pdf_auto_scan_source_is_unchanged(monkeypatch, tmp_path):
    results = [{'short_name': 'Vision Pattern', 'round': '1st', 'raw_data': []}]
    seen = _pdf_texts(monkeypatch, tmp_path, results)
    assert seen['root'] == 'D:/data'
    assert not any(str(t).startswith('Source:') for t in seen['texts'])
