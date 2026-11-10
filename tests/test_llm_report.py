"""AI 분석용 Markdown 리포트 (core.llm_report) — 형식 고정과 값 일치."""
import math
import os
import re

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.checklist import compute_axis_metrics
from core.die_analysis import filter_stabilization_die
from core.llm_report import RAW_CSV_COLUMNS, REPORT_SCHEMA, build_llm_report, build_raw_csv
from core.recipe_scanner import load_all_recipes, recipes_from_mapping

SPECS = ['Vision Pattern', 'In-Die Align', 'LLC Translation', 'Global Align']
SPEC_DEV = {k: {'spec_range': 2.0, 'spec_stddev': 0.5} for k in SPECS}
SPEC_LIM = {k: {'X': {'lsl': -5000, 'usl': 5000}, 'Y': {'lsl': -5000, 'usl': 5000}}
            for k in SPECS}
HEADER = ('Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
          'HZ1_O (nm),HZ1_O_Valid\n')
FIXED = dict(app_version='1.1.4', exported_at='2026-11-10 15:00:00', source_root='D:/data')


def _write_lot(lot, spread, seed, outlier=None, failed=None):
    """22 Die(5열 격자, 10 mm 간격) × X/Y. 위치에 비례한 계통 성분을 섞는다."""
    lot.mkdir(parents=True, exist_ok=True)
    for axis in ('X', 'Y'):
        rows = []
        for d in range(1, 23):
            x_mm, y_mm = ((d - 1) % 5) * 10.0, ((d - 1) // 5) * 10.0
            value = (2000.0 + spread * math.sin(d * 1.7 + seed * 0.9 + (axis == 'Y'))
                     + 5.0 * (x_mm if axis == 'X' else y_mm))
            valid, text = 'TRUE', f'{value:.3f}'
            if outlier == (axis, d):
                text = f'{value + 50 * spread + 3000:.3f}'
            if failed == (axis, d):
                valid, text = 'FALSE', 'NaN'
            rows.append(f'{d:04d}_X{d:03d}_Y000,{d},0,1,{x_mm * 1000:.1f},{y_mm * 1000:.1f},'
                        f'{axis},COMPLETED,{valid},{text},{valid}\n')
        (lot / f'Sample10_{axis}_UL.csv').write_text(
            'Lot ID,Lot3\n\n' + HEADER + ''.join(rows), encoding='utf-8')


def _make_runs(folder, runs, lots=10, spread_of=lambda k: 150.0 * (k + 1), marks=True):
    for k, run in enumerate(runs):
        for rep in range(lots):
            base = folder / str(run) if run is not None else folder
            _write_lot(base / f'Lot{rep + 1:02d}', spread_of(k), rep,
                       outlier=('X', 7) if marks and rep == 3 else None,
                       failed=('Y', 9) if marks and rep == 5 else None)
    return folder


def _mapped(tmp_path, specs_runs, lots=10, spread=None, marks=True):
    mapping = []
    for name, runs in specs_runs:
        folder = tmp_path / name.replace(' ', '_')
        kwargs = {'marks': marks}
        if spread is not None:
            kwargs['spread_of'] = spread[name]
        _make_runs(folder, runs, lots=lots, **kwargs)
        mapping.append((name, str(folder)))
    recipes, problems = recipes_from_mapping(mapping, SPECS)
    assert problems == []
    return load_all_recipes('', recipes=recipes)


def _section(md, title):
    start = md.index(f'\n## {title}')
    nxt = md.find('\n## ', start + 1)
    return md[start: nxt if nxt != -1 else len(md)]


def _front(md):
    assert md.startswith('---\n')
    return yaml.safe_load(md.split('---\n')[1])


def test_output_is_deterministic(tmp_path):
    results = _mapped(tmp_path, [('Vision Pattern', ['1', '2'])])
    again = load_all_recipes('', recipes=recipes_from_mapping(
        [('Vision Pattern', str(tmp_path / 'Vision_Pattern'))], SPECS)[0])
    a = build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED)
    assert a == build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED)
    assert a == build_llm_report(again, SPEC_LIM, SPEC_DEV, **FIXED)


def test_all_sections_present_in_order(tmp_path):
    md = build_llm_report(_mapped(tmp_path, [('Vision Pattern', ['1', '2'])]),
                          SPEC_LIM, SPEC_DEV, **FIXED)
    titles = re.findall(r'^## (.+)$', md, re.MULTILINE)
    assert titles == ['분석 조건', '결과 요약', '원인 단서', '회차 비교',
                      '회차 변경 이력 (사용자 기입)', '부록: Die×Repeat 편차 표 (선택 회차)',
                      'LLM 분석 요청']
    clues = _section(md, '원인 단서')
    for sub in ('#### 계통(Systematic) vs 랜덤', '#### 공간(Die)', '#### 시간(Repeat 순서)',
                '#### 이상치·측정 실패·제외'):
        assert sub in clues
    assert 'Sx = ∂dx/∂x' in clues and '피팅 불가' not in clues    # 2D 좌표라 평면 피팅이 풀린다
    assert 'Die 간 (%)' in clues and 'Lot 간 (%)' in clues
    assert '지어내지 마세요' in _section(md, 'LLM 분석 요청')


def test_front_matter_parses(tmp_path):
    md = build_llm_report(_mapped(tmp_path, [('Vision Pattern', ['1', '2', '3'])]),
                          SPEC_LIM, SPEC_DEV, **FIXED)
    front = _front(md)
    assert front['report_schema'] == REPORT_SCHEMA
    assert front['app_version'] == '1.1.4'
    assert front['data_source'] == {'mode': 'per-workflow', 'root': 'D:/data'}
    assert front['workflows'] == [{'name': 'Vision Pattern',
                                   'path': str(tmp_path / 'Vision_Pattern'),
                                   'selected_run': '3', 'run_count': 3,
                                   'run_status': {'1': 'ok', '2': 'ok', '3': 'ok'}}]


def test_auto_source_text(tmp_path):
    _make_runs(tmp_path / 'root' / 'Vision Pattern', [None])
    results = load_all_recipes(str(tmp_path / 'root'))
    front = _front(build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED))
    assert front['data_source']['mode'] == 'auto'
    assert front['workflows'][0]['path'] == str(tmp_path / 'root' / 'Vision Pattern')
    assert front['workflows'][0]['selected_run'] == '(root)'


def test_spec_out_items_first_and_margins(tmp_path):
    results = _mapped(tmp_path, [('Vision Pattern', [None]), ('Global Align', [None])],
                      spread={'Vision Pattern': lambda k: 100.0,      # PASS
                              'Global Align': lambda k: 1500.0},      # FAIL
                      marks=False)
    md = build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED)
    summary = _section(md, '결과 요약')
    rows = [line for line in summary.splitlines() if line.startswith('| Global Align')
            or line.startswith('| Vision Pattern')]
    assert [r.split(' | ')[0] for r in rows] == ['| Global Align'] * 2 + ['| Vision Pattern'] * 2
    assert '| FAIL |' in rows[0] and '| PASS |' in rows[-1]

    ga = results[1]
    m = compute_axis_metrics(filter_stabilization_die(ga['raw_data']), 'X', 'Global Align',
                             SPEC_LIM, SPEC_DEV)
    diff, pct = m['range'] - 2.0, m['range'] / 2.0 * 100
    assert f'{diff:+.3f} (Spec 대비 {pct:.1f}%)' in rows[0]
    over = summary[summary.index('### Spec 초과 항목'):summary.index('### Workflow·축별')]
    assert (f"- Global Align X Range: {m['range']:.3f} µm (Spec 2.000 µm, {diff:+.3f} µm, "
            f"Spec 대비 {pct:.1f}%)") in over
    assert 'Vision Pattern' not in over


def test_appendix_dimensions_and_markers(tmp_path):
    results = _mapped(tmp_path, [('Vision Pattern', ['1', '2', '3'])])
    appendix = _section(build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED),
                        '부록: Die×Repeat 편차 표 (선택 회차)')
    blocks = appendix.split('\n### ')[1:]
    assert [b.splitlines()[0] for b in blocks] == ['Vision Pattern — 회차 3']   # 선택 회차만
    for block in blocks:
        for axis_table in block.split('#### ')[1:]:
            rows = [line for line in axis_table.splitlines() if line.startswith('| Die')]
            assert len(rows) == 1 + 22                      # 머리글 + 22 Die (안정화 Die 포함)
            assert len(rows[0].split(' | ')) == 1 + 10      # Die 열 + 10 Lot
            assert rows[1].startswith('| Die1 | (제외) |')
    x_table = blocks[0].split('#### X')[1].split('#### Y')[0]
    y_table = blocks[0].split('#### Y')[1]
    die7 = next(line for line in x_table.splitlines() if line.startswith('| Die7 |'))
    assert die7.split(' | ')[4].endswith('*')               # Lot04 X Die7 이상치
    die9 = next(line for line in y_table.splitlines() if line.startswith('| Die9 |'))
    assert die9.split(' | ')[6] == 'NaN'                    # Lot06 Y Die9 측정 실패


def test_single_run_omits_run_comparison(tmp_path):
    md = build_llm_report(_mapped(tmp_path, [('Vision Pattern', [None])]),
                          SPEC_LIM, SPEC_DEV, **FIXED)
    assert '## 회차 비교' not in md
    log = _section(md, '회차 변경 이력 (사용자 기입)')
    assert '| Vision Pattern | (root) |  |' in log


def test_run_comparison_has_deltas_and_spec_crossing(tmp_path):
    md = build_llm_report(_mapped(tmp_path, [('Vision Pattern', ['1', '2', '3', '4', '5'])],
                                  marks=False), SPEC_LIM, SPEC_DEV, **FIXED)
    comp = _section(md, '회차 비교')
    assert '| 1 → 2 |' in comp and '| 4 → 5 |' in comp
    assert re.search(r'- \d → \d: PASS → FAIL', comp)
    assert '- FAIL 회차: ' in comp


def test_file_size_for_14_runs_4_workflows(tmp_path, capsys):
    results = _mapped(tmp_path, [(name, [str(i) for i in range(1, 15)]) for name in SPECS])
    md = build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED)
    size = len(md.encode('utf-8'))
    with capsys.disabled():
        print(f'\n[llm report] 4 workflows x 14 runs x 10 lots: {size / 1024:.1f} KB, '
              f'{md.count(chr(10))} lines')
    csv_text = build_raw_csv(results)
    csv_size = len(csv_text.encode('utf-8-sig'))
    with capsys.disabled():
        print(f'[llm report] companion CSV (all runs): {csv_size / 1024:.1f} KB, '
              f'{csv_text.count(chr(10)) - 1} rows')
    assert md.count('회차 × Die 평균 편차 — X') == 4          # 회차 × Die 표는 .md에 남는다
    assert md.count('\n### ') < 4 * 14                        # 부록은 선택 회차만
    assert csv_text.count('\n') - 1 == 4 * 14 * 10 * 22 * 2
    assert size < 400 * 1024


def test_export_writes_utf8_file_and_logs(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog
    app = QApplication.instance() or QApplication([])
    import main
    from ui.controllers import export_controller
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_deviation=SPEC_DEV,
                                                            spec_limits=SPEC_LIM))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    try:
        win.recipe_results = _mapped(tmp_path, [('Vision Pattern', ['1', '2'])])
        win._scanned_folder = str(tmp_path)
        out = tmp_path / 'out.md'
        names = []
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(
            lambda *a, **k: names.append(a[2]) or (str(out), '')))
        started = []
        monkeypatch.setattr(export_controller.threading, 'Thread',
                            lambda target, daemon: type('T', (), {
                                'start': lambda self: started.append(target)})())
        win._export_llm_markdown()
        started[0]()
        assert re.fullmatch(r'XY_LLM_Vision_Pattern_\d{8}_\d{4}\.md', names[0])
        text = out.read_text(encoding='utf-8')
        assert _front(text)['report_schema'] == REPORT_SCHEMA
        assert b'\r\n' not in out.read_bytes()
        deadline = 50
        while 'AI 분석용 Markdown 저장' not in str(win.logger.export_rows()) and deadline:
            app.processEvents()
            import time
            time.sleep(0.05)
            deadline -= 1
        messages = [row['message'] for row in win.logger.export_rows()]
        assert any(m.startswith(f'AI 분석용 Markdown 저장: {out} (') and m.endswith(' KB)')
                   for m in messages)
    finally:
        win.deleteLater()
        app.processEvents()


def test_every_export_passes_default_name_as_dir_argument(monkeypatch):
    """getSaveFileName(parent, caption, dir, filter): 기본 이름이 5번째(선택 필터)에 가면 무시된다."""
    from PySide6.QtWidgets import QApplication, QFileDialog, QWidget
    from ui.controllers.export_controller import ExportMixin
    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: calls.append(a) or ('', '')))

    class Host(QWidget, ExportMixin):
        pass

    host = Host()
    host.raw_data = [{'value': 1.0}]
    host.recipe_results = [{'short_name': 'Vision Pattern', 'raw_data': []}]
    host.settings = {}
    host._scanned_folder = 'D:/data'
    host.path_edit = type('E', (), {'text': lambda self: 'D:/data'})()
    host.logger = type('L', (), {'info': lambda *a: None})()
    for export in (host._export_csv, host._export_excel, host._export_quality_report,
                   host._export_pdf, host._export_llm_markdown):
        export()
    names = [call[2] for call in calls]
    assert names[:4] == ['Analysis.txt', 'Report.xlsx', 'QualityReport.xlsx', 'Report.pdf']
    assert re.fullmatch(r'XY_LLM_Vision_Pattern_\d{8}_\d{4}\.md', names[4])
    assert all(len(call) == 4 for call in calls)
    host.deleteLater()
    app.processEvents()


def test_companion_csv_contents(tmp_path):
    import csv as csv_module
    import io
    results = _mapped(tmp_path, [('Vision Pattern', ['1', '2'])])
    rows = list(csv_module.DictReader(io.StringIO(build_raw_csv(results))))
    assert list(rows[0].keys()) == RAW_CSV_COLUMNS
    assert len(rows) == 2 * 10 * 22 * 2
    assert {r['run'] for r in rows} == {'1', '2'}
    die1 = [r for r in rows if r['die'] == 'Die1']
    assert die1 and all(r['flags'] == 'excluded' and r['deviation_um'] == '' for r in die1)
    outlier = next(r for r in rows if r['run'] == '2' and r['lot'] == 'Lot04'
                   and r['die'] == 'Die7' and r['axis'] == 'X')
    assert outlier['flags'] == 'outlier' and outlier['deviation_um']
    failed = next(r for r in rows if r['run'] == '2' and r['lot'] == 'Lot06'
                  and r['die'] == 'Die9' and r['axis'] == 'Y')
    assert failed['flags'] == 'failed' and failed['deviation_um'] == ''
    # 부록 표와 같은 값 (선택 회차 2, X, Die2, Lot01)
    md = build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED)
    appendix = _section(md, '부록: Die×Repeat 편차 표 (선택 회차)')
    die2 = next(l for l in appendix.split('#### X')[1].splitlines() if l.startswith('| Die2 |'))
    cell = next(r for r in rows if r['run'] == '2' and r['lot'] == 'Lot01'
                and r['die'] == 'Die2' and r['axis'] == 'X')
    assert die2.split(' | ')[1] == f"{float(cell['deviation_um']):+.3f}"
    assert cell['x_mm'] == '10.000' and cell['lot_order'] == '1'


def test_export_writes_md_and_bom_csv_and_survives_csv_failure(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog
    app = QApplication.instance() or QApplication([])
    import main
    import core.llm_report as llm_report
    from ui.controllers import export_controller
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_deviation=SPEC_DEV,
                                                            spec_limits=SPEC_LIM))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    try:
        win.recipe_results = _mapped(tmp_path, [('Vision Pattern', ['1', '2'])])
        win._scanned_folder = str(tmp_path)
        started = []
        monkeypatch.setattr(export_controller.threading, 'Thread',
                            lambda target, daemon: type('T', (), {
                                'start': lambda self: started.append(target)})())

        def export(name):
            out = tmp_path / name
            monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                                staticmethod(lambda *a, **k: (str(out), '')))
            win._export_llm_markdown()
            started.pop()()
            for _ in range(50):
                app.processEvents()
                if any(str(out) in row['message'] for row in win.logger.export_rows()):
                    break
            return out

        out = export('ok.md')
        csv_path = tmp_path / 'ok_raw.csv'
        assert csv_path.read_bytes().startswith(b'\xef\xbb\xbf')
        assert 'raw_csv: "ok_raw.csv"' in out.read_text(encoding='utf-8')
        assert '`ok_raw.csv`에 있다' in out.read_text(encoding='utf-8')
        messages = [row['message'] for row in win.logger.export_rows()]
        assert any(m.startswith(f'셀 값 CSV 저장: {csv_path} (') for m in messages)

        def boom(_results):
            raise OSError('disk full')
        monkeypatch.setattr(llm_report, 'build_raw_csv', boom)
        out = export('bad.md')
        text = out.read_text(encoding='utf-8')
        assert 'raw_csv: ""' in text and '쓰지 못했다 (OSError: disk full)' in text
        assert not (tmp_path / 'bad_raw.csv').exists()
        messages = [row['message'] for row in win.logger.export_rows()]
        assert any('셀 값 CSV 저장 실패 (Markdown은 저장됨): OSError: disk full' in m for m in messages)
    finally:
        win.deleteLater()
        app.processEvents()
