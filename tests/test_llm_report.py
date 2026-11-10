"""AI 분석용 Markdown 리포트 (core.llm_report) — 형식 고정과 값 일치."""
import math
import os
import re

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.checklist import compute_axis_metrics
from core.die_analysis import filter_stabilization_die
from core.llm_report import REPORT_SCHEMA, build_llm_report
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
                      '회차 변경 이력 (사용자 기입)', '부록: Die×Repeat 편차 표', 'LLM 분석 요청']
    clues = _section(md, '원인 단서')
    for sub in ('#### 계통(Systematic) vs 랜덤', '#### 공간(Die)', '#### 시간(Repeat 순서)',
                '#### 이상치·측정 실패·제외'):
        assert sub in clues
    assert 'Scaling Sx' in clues and '피팅 불가' not in clues    # 2D 좌표라 Affine이 풀린다
    assert 'Die 간 비율 (%)' in clues
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
                                   'selected_run': '3', 'run_count': 3}]


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
    assert f'{diff:+.3f} ({pct:.1f}%)' in rows[0]
    over = summary[summary.index('### Spec 초과 항목'):summary.index('### Workflow·축별')]
    assert f"- Global Align X Range: {m['range']:.3f} µm (Spec 2.000 µm, {diff:+.3f} µm, {pct:.1f}%)" in over
    assert 'Vision Pattern' not in over


def test_appendix_dimensions_and_markers(tmp_path):
    results = _mapped(tmp_path, [('Vision Pattern', ['1', '2', '3'])])
    appendix = _section(build_llm_report(results, SPEC_LIM, SPEC_DEV, **FIXED),
                        '부록: Die×Repeat 편차 표')
    blocks = appendix.split('\n### ')[1:]
    assert [b.splitlines()[0] for b in blocks] == [
        f'Vision Pattern — 회차 {k}' for k in ('1', '2', '3')]
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
    assert md.count('\n### ') >= 4 * 14
    assert size < 5 * 1024 * 1024


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
            lambda *a, **k: names.append(a[4]) or (str(out), '')))
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
