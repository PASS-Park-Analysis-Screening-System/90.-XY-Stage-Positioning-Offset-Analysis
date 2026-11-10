"""회차(트러블슈팅 반복) 폴더 — 모든 회차를 분석하고 비교한다.

배경: Vision Pattern 폴더 아래 1..14 회차 폴더(각 Lot 10개)가 있었는데, 로드는
'1st' 라운드를 찾다가 없으면 rounds[0]만 읽었다. 13회차가 조용히 빠졌고, 라운드는
문자열 정렬(1, 10, 11, …, 2)이었다.
"""
import math
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.checklist import (build_checklist, combine_verdicts, compute_axis_metrics,
                            result_text, summarize_runs)
from core.die_analysis import filter_stabilization_die
from core.recipe_scanner import (build_recipe, default_round_name, is_run_style,
                                 load_all_recipes, load_recipe_data, natural_key,
                                 recipes_from_mapping, run_label, run_view, scan_recipes)
from core.workflow_map import describe_folder

SPEC_DEV = {'Vision Pattern': {'spec_range': 2.0, 'spec_stddev': 0.5}}
SPEC_LIM = {'Vision Pattern': {'X': {'lsl': -5000, 'usl': 5000},
                               'Y': {'lsl': -5000, 'usl': 5000}}}
HEADER = ('Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
          'HZ1_O (nm),HZ1_O_Valid\n')


def _write_lot(lot, spread, seed):
    """22 Die × X/Y 한 Lot. spread(nm)가 클수록 편차가 크다."""
    lot.mkdir(parents=True, exist_ok=True)
    for axis in ('X', 'Y'):
        rows = []
        for d in range(1, 23):
            value = 2000.0 + spread * math.sin(d * 1.7 + seed * 0.9 + (axis == 'Y'))
            rows.append(f'{d:04d}_X{d:03d}_Y000,{d},0,1,{d * 1000},0,{axis},COMPLETED,TRUE,'
                        f'{value:.3f},TRUE\n')
        (lot / f'Sample10_{axis}_UL.csv').write_text(
            'Lot ID,Lot3\n\n' + HEADER + ''.join(rows), encoding='utf-8')


def _make_runs(folder, run_names, lots=10, spread_of=lambda k: 200.0 * (k + 1)):
    for k, run in enumerate(run_names):
        for rep in range(lots):
            _write_lot(folder / str(run) / f'Lot{rep + 1:02d}', spread_of(k), rep)
    return folder


def _strip_run_keys(result):
    return {k: v for k, v in result.items()
            if k not in ('run_results', 'default_run', 'runs', 'source_path')}


# ── 정렬·기본 회차 ────────────────────────────────────────────────

def test_natural_sort_orders_numbers_and_dates():
    assert sorted(['1', '10', '11', '2', '9'], key=natural_key) == ['1', '2', '9', '10', '11']
    assert sorted(['2026-11-10', '2026-9-30', '2026-11-02'], key=natural_key) == [
        '2026-9-30', '2026-11-02', '2026-11-10']
    assert sorted(['run10', 'Run2', 'run1'], key=natural_key) == ['run1', 'Run2', 'run10']
    assert sorted(['B', 'a'], key=natural_key) == ['a', 'B']


def test_build_recipe_lists_runs_in_natural_order(tmp_path):
    _make_runs(tmp_path / 'vp', [str(i) for i in range(1, 15)], lots=1)
    recipe = build_recipe(str(tmp_path / 'vp'))
    assert [r['name'] for r in recipe['rounds']] == [str(i) for i in range(1, 15)]


@pytest.mark.parametrize('names,expected', [
    (['1st', '2nd'], '1st'),                       # 기존 구조 — 1.1.3과 같아야 한다
    (['2nd', '1st', '3rd'], '1st'),
    (['(root)'], '(root)'),
    (['7'], '7'),
    ([str(i) for i in range(1, 15)], '14'),
    (['2026-11-02', '2026-11-10', '2026-9-30'], '2026-11-10'),
    (['A', 'B'], 'A'),                             # 숫자 없는 이름은 1.1.3처럼 첫 라운드
    (['2nd', '3rd'], '2nd'),
    ([], None),
])
def test_default_round_rule(names, expected):
    assert default_round_name(names) == expected


def test_run_style_excludes_ordinals():
    assert is_run_style(['1', '2'])
    assert is_run_style(['2026-11-01', '2026-11-02'])
    assert not is_run_style(['1st', '2nd'])
    assert not is_run_style(['A', '1'])


# ── 로드 ─────────────────────────────────────────────────────────

def test_legacy_first_second_layout_keeps_1st_result(tmp_path):
    """1.1.3 결과 고정: 1st/2nd 구조의 기본 결과는 1st 라운드 단독 로드와 같다."""
    _make_runs(tmp_path / 'Vision Pattern', ['1st', '2nd'], lots=10)
    recipe = scan_recipes(str(tmp_path))[0]

    result = load_all_recipes(str(tmp_path))[0]

    assert result['round'] == '1st' and result['default_run'] == '1st'
    assert _strip_run_keys(result) == load_recipe_data(recipe, round_name='1st')
    assert [r['round'] for r in result['run_results']] == ['1st', '2nd']


def test_single_round_result_is_unchanged(tmp_path):
    _make_runs(tmp_path, ['Vision Pattern'], lots=10)
    recipe = scan_recipes(str(tmp_path))[0]
    result = load_all_recipes(str(tmp_path))[0]
    assert 'run_results' not in result
    assert result == load_recipe_data(recipe)


def test_all_runs_are_loaded_and_match_single_run_loads(tmp_path):
    vp = _make_runs(tmp_path / 'vp', ['1', '2', '10'], lots=10)
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    progress = []

    result = load_all_recipes('', recipes=recipes,
                              progress_cb=lambda i, n, text: progress.append(text))[0]

    names = [r['round'] for r in result['run_results']]
    assert names == ['1', '2', '10']
    assert result['default_run'] == '10' and result['round'] == '10'
    for run in result['run_results']:
        single = load_recipe_data(recipes[0], round_name=run['round'])
        assert run['statistics'] == single['statistics']
        assert len(run['raw_data']) == len(single['raw_data'])
    assert _strip_run_keys(result) == _strip_run_keys(result['run_results'][-1])
    assert result['source_path'] == str(vp)
    assert 'Vision Pattern 회차 3/3' in progress


def test_run_summary_uses_checklist_values(tmp_path):
    vp = _make_runs(tmp_path / 'vp', ['1', '2', '3', '4'], lots=10)
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    result = load_all_recipes('', recipes=recipes)[0]

    runs = summarize_runs(result, SPEC_LIM, SPEC_DEV)

    assert [r['run'] for r in runs] == ['1', '2', '3', '4']
    for run, full in zip(runs, result['run_results']):
        rows = build_checklist([full | {'short_name': 'Vision Pattern'}], SPEC_LIM, SPEC_DEV)
        by_axis = {r['axis']: r for r in rows}
        for axis in ('x', 'y'):
            expected = by_axis[axis.upper()]
            for key in ('range', 'stddev', 'avg', 'cpk', 'result'):
                assert run[axis][key] == expected[key]
        assert run['result'] == combine_verdicts(by_axis['X']['result'], by_axis['Y']['result'])
        assert run['lot_count'] == 10
    # 편차가 회차마다 커지게 만들었다: 앞 회차는 PASS, 뒤 회차는 FAIL
    assert runs[0]['result'] is True and runs[-1]['result'] is False
    assert summarize_runs(result['run_results'][0], SPEC_LIM, SPEC_DEV) == []


def test_run_view_and_label(tmp_path):
    vp = _make_runs(tmp_path / 'vp', ['1', '2'], lots=10)
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    result = load_all_recipes('', recipes=recipes)[0]
    view = run_view(result, 0)
    assert view['round'] == '1' and view['run_results'] is result['run_results']
    assert view['source_path'] == str(vp)
    assert run_label(view) == 'Run: 1 (2회차 중)'
    assert run_label(result['run_results'][0]) == ''


# ── 상태 문구 ────────────────────────────────────────────────────

def test_status_text_summarises_runs(tmp_path):
    _make_runs(tmp_path / 'same', [str(i) for i in range(1, 15)], lots=2)
    assert describe_folder(str(tmp_path / 'same')) == (True, '✅ 회차 14개 (1~14) · Lot 2개씩')
    _make_runs(tmp_path / 'mixed', ['1', '2'], lots=1)
    _write_lot(tmp_path / 'mixed' / '2' / 'LotX', 100.0, 1)
    assert describe_folder(str(tmp_path / 'mixed')) == (True, '✅ 회차 2개 (1~2) · Lot 1~2개')
    _make_runs(tmp_path / 'legacy', ['1st', '2nd'], lots=1)
    assert describe_folder(str(tmp_path / 'legacy')) == (True, '✅ 1st Lot 1개, 2nd Lot 1개')


# ── Excel ────────────────────────────────────────────────────────

def _excel(tmp_path, results):
    from openpyxl import load_workbook
    from core.exporter import export_excel_report
    path = tmp_path / 'r.xlsx'
    export_excel_report(results[0]['raw_data'], {}, [], str(path), recipe_results=results,
                        spec_limits=SPEC_LIM, spec_deviation=SPEC_DEV)
    return load_workbook(path)


def test_excel_run_comparison_sheet_only_for_multi_run(tmp_path):
    vp = _make_runs(tmp_path / 'vp', ['1', '2', '3'], lots=10)
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    multi = load_all_recipes('', recipes=recipes)
    wb = _excel(tmp_path, multi)
    assert 'Run Comparison' in wb.sheetnames
    rows = [r for r in wb['Run Comparison'].iter_rows(values_only=True) if any(r)]
    assert rows[0][0].startswith('Vision Pattern — 회차 3개 (선택 회차 3')
    assert [r[0] for r in rows[2:]] == ['1', '2', '3']
    basis = [r for r in wb['Analysis Basis'].iter_rows(values_only=True) if any(r)]
    assert ('Vision Pattern', 'Run: 3 (3회차 중)') in [r[:2] for r in basis]

    _make_runs(tmp_path / 'auto', ['Vision Pattern'], lots=10)
    single = load_all_recipes(str(tmp_path / 'auto'))
    wb = _excel(tmp_path, single)
    assert 'Run Comparison' not in wb.sheetnames
    assert not any(r[1] == '선택 회차' for r in wb['Analysis Basis'].iter_rows(values_only=True))


# ── 화면 ─────────────────────────────────────────────────────────

@pytest.fixture
def window(monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import main
    monkeypatch.setattr(main, 'load_settings', lambda: dict(
        spec_deviation=SPEC_DEV, spec_limits=SPEC_LIM))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    yield win
    win.deleteLater()
    app.processEvents()


def _loaded(window, tmp_path, runs=('1', '2', '3', '4')):
    from core.recipe_scanner import compare_recipes
    vp = _make_runs(tmp_path / 'vp', list(runs), lots=10)
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    window.recipes = recipes
    window._build_nav()
    results = load_all_recipes('', recipes=recipes)
    window._loader_thread = type('Done', (), {'folder': str(vp)})()
    window._on_scan_complete(results, compare_recipes(results), 1.0)
    return results


def test_combo_defaults_to_last_run_and_switch_updates_cards(window, tmp_path):
    _loaded(window, tmp_path)
    combo = window.run_combo
    assert not combo.isHidden()
    assert [combo.itemText(i) for i in range(combo.count())] == ['1', '2', '3', '4 (기본)']
    assert combo.currentIndex() == 3
    runs = window.recipe_results[0]['runs']
    assert window.card_x.badge.text() == '❌ FAIL'
    assert window.step_pass_states[0] is False

    combo.setCurrentIndex(0)

    assert window.recipe_results[0]['round'] == '1'
    assert window.card_x.badge.text() == '✅ PASS'
    assert window.card_x.lbl_rng.text() == f"{runs[0]['x']['range']:.3f}"
    assert window.card_x.lbl_cpk.text() == f"{runs[0]['x']['cpk']:.2f}"
    assert window.card_y.lbl_std.text() == f"{runs[0]['y']['stddev']:.3f}"
    assert window.step_pass_states[0] is True      # Step 버튼 색도 이 회차 기준
    assert window.run_compare_table.selectionModel().selectedRows()[0].row() == 0


def test_card_values_equal_run_summary_for_every_run(window, tmp_path):
    _loaded(window, tmp_path)
    runs = window.recipe_results[0]['runs']
    for k, run in enumerate(runs):
        window.run_combo.setCurrentIndex(k)
        for card, axis in ((window.card_x, 'x'), (window.card_y, 'y')):
            assert card.lbl_rng.text() == f"{run[axis]['range']:.3f}"
            assert card.lbl_cpk.text() == f"{run[axis]['cpk']:.2f}"
            assert card.badge.text() in (('✅ PASS', '⚠ PASS') if run[axis]['result']
                                         else ('❌ FAIL',))


def test_run_table_and_row_click(window, tmp_path):
    _loaded(window, tmp_path)
    table = window.run_compare_table
    assert table.rowCount() == 4 and not table.isHidden()
    assert [table.item(r, 0).text() for r in range(4)] == ['1', '2', '3', '4']
    assert table.item(0, 9).text() == 'PASS' and table.item(3, 9).text() == 'FAIL'
    assert window.chart_widgets['Run Metrics']._canvas is not None

    window._on_run_row_clicked(1)

    assert window.run_combo.currentIndex() == 1
    assert window.recipe_results[0]['round'] == '2'


def test_single_run_hides_combo_and_shows_message(window, tmp_path):
    _loaded(window, tmp_path, runs=('1',))
    assert window.run_combo.isHidden()
    assert window.run_compare_table.isHidden()
    assert '회차가 1개라 비교할 수 없습니다' in window.run_compare_label.text()


# ── 로드 시간 (보고용 측정, 상한은 느슨하게) ─────────────────────────

def test_load_time_14_runs_by_10_lots(tmp_path, capsys):
    vp = _make_runs(tmp_path / 'vp', [str(i) for i in range(1, 15)], lots=10)
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    t0 = time.perf_counter()
    result = load_all_recipes('', recipes=recipes)[0]
    load_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    summarize_runs(result, SPEC_LIM, SPEC_DEV)
    summary_s = time.perf_counter() - t0
    with capsys.disabled():
        print(f"\n[load time] 14 runs x 10 lots: load {load_s:.2f}s, summary {summary_s:.2f}s")
    assert len(result['run_results']) == 14
    assert load_s < 120


def test_pdf_step_title_states_run(tmp_path, monkeypatch):
    from core import pdf_generator
    vp = _make_runs(tmp_path / 'vp', ['1', '2'], lots=10)
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    result = load_all_recipes('', recipes=recipes)[0]
    titles = []
    real = pdf_generator.Figure.suptitle
    monkeypatch.setattr(pdf_generator.Figure, 'suptitle',
                        lambda fig, t, *a, **k: titles.append(t) or real(fig, t, *a, **k))
    pdf_generator.generate_pdf_report(str(tmp_path / 'r.pdf'), str(vp), [run_view(result, 0)],
                                      [], SPEC_LIM, SPEC_DEV)
    assert 'Step: Vision Pattern — Run: 1 (2회차 중)' in titles


# ── 리뷰 반영 ────────────────────────────────────────────────────

@pytest.mark.parametrize('names,expected', [
    (['14_rerun', '14'], ['14', '14_rerun']),
    (['2', '1-2', '1', '1-1'], ['1', '1-1', '1-2', '2']),
    (['2026-11-01_2', '2026-11-01'], ['2026-11-01', '2026-11-01_2']),
])
def test_natural_sort_puts_prefix_first(names, expected):
    assert sorted(names, key=natural_key) == expected


@pytest.mark.parametrize('names,expected', [
    (['10', '9', 'retry'], '10'),         # 회차 사이에 retry가 섞여도 회차 구조
    (['1', '2', '14', 'ref', 'backup'], '14'),
    (['2nd', '10th'], '10th'),            # 서수는 1.1.3처럼 단순 정렬의 첫 이름
    (['b', 'B', 'a'], 'B'),
])
def test_default_round_rule_mixed_names(names, expected):
    assert default_round_name(names) == expected


def test_run_style_by_majority():
    assert is_run_style(['1', '2', 'retry'])
    assert not is_run_style(['1', 'retry'])
    assert not is_run_style(['1st', '2nd', '3'])


def _empty_lot(lot):
    """Lot으로 인식되지만 측정 행이 없는 폴더 (측정 중단)."""
    lot.mkdir(parents=True, exist_ok=True)
    for axis in ('X', 'Y'):
        (lot / f'Sample10_{axis}_UL.csv').write_text('Lot ID,Lot3\n\n' + HEADER, encoding='utf-8')


def test_empty_last_run_is_not_the_default(tmp_path):
    vp = _make_runs(tmp_path / 'vp', ['1', '2'], lots=10)
    _empty_lot(vp / '3' / 'Lot01')
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))

    result = load_all_recipes('', recipes=recipes)[0]

    assert [r['round'] for r in result['run_results']] == ['1', '2', '3']
    assert result['run_results'][2]['error'] == 'No data loaded'
    assert result['run_results'][2]['short_name'] == 'Vision Pattern'
    assert result['default_run'] == '2' and result['round'] == '2'
    assert result['default_skipped'] == ['3']
    assert result['short_name'] == 'Vision Pattern'
    runs = summarize_runs(result, SPEC_LIM, SPEC_DEV)
    assert runs[0]['result'] is True and runs[0]['x']['cpk'] is not None
    assert runs[2]['result'] is None and runs[2]['warnings'] == ['No data loaded']


def test_all_runs_empty_falls_back_to_last_with_spec_name(tmp_path):
    vp = tmp_path / 'vp'
    _empty_lot(vp / '1' / 'Lot01')
    _empty_lot(vp / '2' / 'Lot01')
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    result = load_all_recipes('', recipes=recipes)[0]
    assert result['default_run'] == '2'
    assert result['short_name'] == 'Vision Pattern'
    assert 'default_skipped' not in result


def test_pdf_keeps_113_title_for_1st_2nd(tmp_path, monkeypatch):
    from core import pdf_generator
    _make_runs(tmp_path / 'Vision Pattern', ['1st', '2nd'], lots=10)
    result = load_all_recipes(str(tmp_path))[0]
    titles = []
    real = pdf_generator.Figure.suptitle
    monkeypatch.setattr(pdf_generator.Figure, 'suptitle',
                        lambda fig, t, *a, **k: titles.append(t) or real(fig, t, *a, **k))
    pdf_generator.generate_pdf_report(str(tmp_path / 'r.pdf'), str(tmp_path), [result],
                                      [], SPEC_LIM, SPEC_DEV)
    assert 'Step: Vision Pattern (1st Round)' in titles


def test_failed_other_run_is_logged_and_blocks_clean_message(window, tmp_path):
    vp = _make_runs(tmp_path / 'vp', ['1', '2'], lots=10)
    _empty_lot(vp / '3' / 'Lot01')
    from core.recipe_scanner import compare_recipes
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp))], list(SPEC_DEV))
    window.recipes = recipes
    window._build_nav()
    results = load_all_recipes('', recipes=recipes)
    window._loader_thread = type('Done', (), {'folder': str(vp)})()
    window._on_scan_complete(results, compare_recipes(results), 1.0)

    logged = str(window.logger.export_rows())
    assert '❌ Vision Pattern 회차 3: No data loaded' in logged
    assert '데이터 품질 이상 없음' not in logged
    assert '최신 회차 3에 데이터가 없어 기본 회차를 2(으)로 했습니다' in logged


def test_run_switch_redraws_recipe_comparison_charts(window, tmp_path, monkeypatch):
    from core.recipe_scanner import compare_recipes
    import charts
    vp = _make_runs(tmp_path / 'vp', ['1', '2'], lots=10)
    ga = _make_runs(tmp_path / 'ga', ['Lots'], lots=10) / 'Lots'
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(vp)), ('Global Align', str(ga))],
                                      ['Vision Pattern', 'Global Align'])
    window.recipes = recipes
    window._build_nav()
    results = load_all_recipes('', recipes=recipes)
    window._loader_thread = type('Done', (), {'folder': str(tmp_path)})()
    window._on_scan_complete(results, compare_recipes(results), 1.0)
    seen = []
    real = charts.plot_recipe_comparison_boxplot
    monkeypatch.setattr(charts, 'plot_recipe_comparison_boxplot',
                        lambda rr: seen.append([r['round'] for r in rr]) or real(rr))

    window.run_combo.setCurrentIndex(0)

    assert seen == [['1', '(root)']]


def test_pdf_export_uses_snapshot_taken_on_main_thread(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    import core.pdf_generator as pdf_generator
    from ui.controllers import export_controller
    _loaded(window, tmp_path, runs=('1', '2'))
    started = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(tmp_path / 'r.pdf'), '')))
    monkeypatch.setattr(export_controller.threading, 'Thread',
                        lambda target, daemon: type('T', (), {
                            'start': lambda self: started.append(target)})())
    from PySide6.QtWidgets import QMessageBox
    # 완료 확인 타이머가 대화상자·파일 열기를 띄우지 않게 막는다 (모달이면 테스트가 멈춘다)
    monkeypatch.setattr(QMessageBox, 'critical', staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(export_controller.os, 'startfile', lambda p: None, raising=False)
    exported = []

    def fake_generate(path, root, results, *a):
        exported.append([r['round'] for r in results])
        open(path, 'w').close()
    monkeypatch.setattr(pdf_generator, 'generate_pdf_report', fake_generate)
    window._export_pdf()

    window.run_combo.setCurrentIndex(0)     # 내보내는 중에 회차를 바꾼다
    started[0]()

    assert exported == [['2']]
