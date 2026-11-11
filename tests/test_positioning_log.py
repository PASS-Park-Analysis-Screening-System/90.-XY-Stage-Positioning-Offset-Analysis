"""비전 보정 로그(SamplePositioning·TipPositioning) 대조.

실데이터: Vision Pattern Lot 46LKD105SJE61 (Slot 19)의 로그와 HZ1_O (tests/fixtures/positioning_log,
Sample ID만 SAMPLE19로 바꿈). 수동 분석: HZ1_O X의 θx(∂X/∂y) = +5.28 µrad인데 비전 로그
어느 단계에도 이 성분이 없다 (Coarse Match X θx 3.2, Fine Move X θx −0.2, 상관 ≈ 0)
→ 비전 정렬 이후(카메라→Tip 착지) 구간에서 생긴 오차.
"""
import math
import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.positioning_log import (NO_LOG_TEXT, analyze_result, find_logs, map_rows_to_dies,
                                  parse_sample_positioning, parse_tip_positioning,
                                  store_analysis, tip_summary)
from core.recipe_scanner import load_all_recipes, recipes_from_mapping

FIXTURES = Path(__file__).parent / 'fixtures' / 'positioning_log'
SAMPLE_LOG = FIXTURES / 'Sample19_SamplePositioning.txt'
TIP_LOG = FIXTURES / 'Sample19_TipPositioning.txt'
SPEC_DEV = {'Vision Pattern': {'spec_range': 2.0, 'spec_stddev': 0.4}}
SPEC_LIM = {'Vision Pattern': {'X': {'lsl': -3000.0, 'usl': 3000.0},
                               'Y': {'lsl': -3000.0, 'usl': 3000.0}}}


def make_lot(folder: Path, name='46LKD105SJE61', sample_log=SAMPLE_LOG, tip_log=TIP_LOG,
             x_shift_nm=0.0):
    """실데이터 Lot 폴더 배치: X/Y UL CSV + Debug/SamplePositioning, Debug/TipPositioning."""
    lot = folder / name
    lot.mkdir(parents=True)
    for axis in ('X', 'Y'):
        text = (FIXTURES / f'lot61_{axis}_UL.csv').read_text(encoding='utf-8')
        if x_shift_nm and axis == 'X':
            lines = text.splitlines()
            start = next(i for i, l in enumerate(lines) if l.startswith('Site ID'))
            for i in range(start + 1, len(lines)):
                cells = lines[i].split(',')
                if len(cells) > 7 and cells[0]:
                    cells[7] = f'{float(cells[7]) + x_shift_nm:.3f}'
                    lines[i] = ','.join(cells)
            text = '\n'.join(lines) + '\n'
        (lot / f'SAMPLE19_{axis}_UL.csv').write_text(text, encoding='utf-8')
    if sample_log is not None:
        (lot / 'Debug' / 'SamplePositioning').mkdir(parents=True)
        shutil.copy(sample_log, lot / 'Debug' / 'SamplePositioning' / 'Sample19_SamplePositioning.txt')
    if tip_log is not None:
        (lot / 'Debug' / 'TipPositioning').mkdir(parents=True, exist_ok=True)
        shutil.copy(tip_log, lot / 'Debug' / 'TipPositioning' / 'Sample19_TipPositioning.txt')
    (lot / 'Debug' / 'Analysis').mkdir(parents=True, exist_ok=True)
    return lot


def load(path: Path):
    recipes, problems = recipes_from_mapping([('Vision Pattern', str(path))], ['Vision Pattern'])
    assert problems == []
    return load_all_recipes('', recipes=recipes)[0]


def row(table, stage, axis):
    return next(r for r in table if r['stage'] == stage and r['axis'] == axis)


# ── 파싱 ─────────────────────────────────────────────────────────

def test_parse_real_sample_positioning():
    rows = parse_sample_positioning(str(SAMPLE_LOG))
    assert len(rows) == 22
    first = rows[0]
    assert (first['slot'], first['point'], first['location']) == ('19', 1.0, 'CENTER')
    assert (first['stage_x'], first['stage_y']) == (-2534.31, 64.12)
    assert (first['coarse_match_x'], first['coarse_match_y']) == (-2.39, 2.80)
    assert (first['fine_move_x'], first['fine_move_y']) == (-0.82, 0.74)
    assert (first['optical_shift_x'], first['tip_pos_x'], first['tip_pos_y']) == (4.76, 1.74, -72.09)
    assert first['vision_total_x'] == pytest.approx(0.63 - 0.82)


def test_parse_real_tip_positioning_uses_fine_match_when_tip_is_empty():
    tips = parse_tip_positioning(str(TIP_LOG))
    assert len(tips) == 1
    t = tips[0]
    assert (t['location'], t['tip_x'], t['tip_y'], t['source']) == ('Center', 1.74, 72.09,
                                                                     'Fine Match X/Y')
    assert t['fine_score'] == 99.85 and t['fine_success'] == 'TRUE'


def test_parser_tolerates_encoding_extra_columns_and_blank_lines(tmp_path):
    path = tmp_path / 'x_SamplePositioning.txt'
    text = ('Slot\tPoint\tLocation\tStage X (um)\tStage Y (um)\tNew Column\tCoarse Match X (um)\n'
            '\n19\t1\tCENTER\t1.5\t2.5\tzz\t-0.5\textra\n')
    path.write_bytes(text.encode('cp949'))
    rows = parse_sample_positioning(str(path))
    assert len(rows) == 1
    assert rows[0]['coarse_match_x'] == -0.5 and rows[0]['fine_move_x'] is None
    assert rows[0]['vision_total_x'] is None


# ── 실데이터 수치 ─────────────────────────────────────────────────

@pytest.fixture
def real_result(tmp_path):
    return load(make_lot(tmp_path / 'Vision Pattern'))


def test_real_sample19_stage_numbers(real_result):
    analysis = analyze_result(real_result)
    assert analysis['status'] == 'ok'
    lot = analysis['lots'][0]
    assert lot['mapping']['rows'] == 22 and lot['mapping']['dies'] == 22
    assert lot['mapping']['rotation_deg'] == pytest.approx(0.046, abs=0.002)   # 샘플 정렬 회전
    assert lot['mapping']['max_residual_um'] < 10
    table = analysis['mean']['stages']
    final_x = row(table, 'final', 'X')
    assert final_x['coef']['θx']['value'] == pytest.approx(5.28, abs=0.01)
    assert final_x['coef']['θx']['t'] > 10
    assert final_x['range'] == pytest.approx(1.270, abs=1e-3)
    assert final_x['k'] == 21                              # 안정화 Die 제외
    fine_x = row(table, 'fine_move', 'X')
    assert fine_x['coef']['θx']['value'] == pytest.approx(-0.19, abs=0.02)
    assert abs(fine_x['r']) < 0.2
    coarse_x, coarse_y = row(table, 'coarse_match', 'X'), row(table, 'coarse_match', 'Y')
    assert coarse_x['coef']['θx']['value'] == pytest.approx(3.17, abs=0.02)
    assert (coarse_x['range'], coarse_y['range']) == (pytest.approx(3.59, abs=0.01),
                                                      pytest.approx(4.26, abs=0.01))
    assert abs(coarse_x['r']) < 0.2
    tip = row(table, 'tip_pos', 'X')
    assert tip['range'] == 0 and tip['r'] is None          # 한 번의 Center Tip Positioning
    lines = analysis['verdict']['lines']
    assert lines[0].startswith('Y 위치에 비례하는 X 오차(θx) +5.28 µrad')
    assert lines[0].endswith('비전 보정 로그에는 없고 최종 착지(HZ1_O)에만 있음 '
                             '→ 비전 정렬 이후(카메라→Tip 착지) 구간')
    assert '|t| ≥ 3' in analysis['verdict']['rule']
    x_term = next(t for t in analysis['verdict']['terms'] if t['term'] == 'θx')
    assert x_term['carriers'] == []


def test_tip_positioning_single_location_suggests_the_test(real_result):
    analysis = analyze_result(real_result)
    assert [(t['lot'], t['location'], t['tip_x']) for t in analysis['tips']] == [
        ('46LKD105SJE61', 'Center', 1.74)]
    summary = analysis['tip_summary']
    assert summary['flag'] is False and summary['slope_x'] is None
    assert any('Y +120 / 0 / −120 mm' in line for line in summary['lines'])


# ── 행 ↔ Die ─────────────────────────────────────────────────────

def _sites():
    from core.positioning_log import lot_sites
    result = load_sites_result()
    return lot_sites(result['raw_data'])


_SITES_CACHE = {}


def load_sites_result():
    if 'r' not in _SITES_CACHE:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix='poslog_sites_'))
        try:
            _SITES_CACHE['r'] = load(make_lot(tmp / 'vp'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _SITES_CACHE['r']


def test_mapping_with_alignment_rotation_offset_and_point0_row():
    """Sample22처럼 원점 차 수백 µm·회전 0.05°·Point 0(샘플 정렬 기준점) 행이 있어도 짝짓는다."""
    sites = _sites()
    rows = parse_sample_positioning(str(SAMPLE_LOG))
    theta = math.radians(-0.05)
    moved = []
    for r in rows:
        x, y = r['stage_x'], r['stage_y']
        moved.append(dict(r, stage_x=math.cos(theta) * x - math.sin(theta) * y - 188.0,
                          stage_y=math.sin(theta) * x + math.cos(theta) * y - 730.0))
    point0 = dict(rows[0], point=0.0, stage_x=130790.8, stage_y=114286.4)
    mapping = map_rows_to_dies([point0] + moved, sites)
    assert mapping['status'] == 'ok' and mapping['skipped_point0'] == 1
    assert len(mapping['mapping']) == 22
    assert mapping['mapping'][4]['stage_x'] == pytest.approx(moved[4]['stage_x'])


def test_mapping_mismatch_is_reported_not_guessed():
    sites = _sites()
    rows = parse_sample_positioning(str(SAMPLE_LOG))
    swapped = list(rows)
    swapped[2], swapped[14] = swapped[14], swapped[2]      # 측정 순서가 어긋남
    mapping = map_rows_to_dies(swapped, sites)
    assert mapping['status'] == 'mismatch' and mapping['mapping'] == {}
    assert any('Die' in p for p in mapping['problems'])

    short = map_rows_to_dies(rows[:-1], sites)
    assert short['status'] == 'mismatch'
    assert short['problems'] == ['로그 행 21개 ≠ Die 22개 (Point 0 행 0개 제외)']


def test_mismatched_lot_is_excluded_with_reason(tmp_path):
    lines = SAMPLE_LOG.read_text(encoding='utf-8').splitlines()
    bad = tmp_path / 'bad_SamplePositioning.txt'
    bad.write_text('\n'.join(lines[:-1]) + '\n', encoding='utf-8')     # 마지막 Die 행 없음
    result = load(make_lot(tmp_path / 'vp', sample_log=bad))
    analysis = analyze_result(result)
    assert analysis['status'] == 'mismatch' and analysis['mean'] is None
    assert analysis['lots'][0]['status'] == 'mismatch'
    assert '로그 행 21개 ≠ Die 22개' in analysis['message']


# ── 로그 없음 ────────────────────────────────────────────────────

def test_missing_logs(tmp_path):
    result = load(make_lot(tmp_path / 'vp', sample_log=None, tip_log=None))
    assert find_logs(result['round_path']) == {'sample': None, 'tip': None, 'problems': []}
    analysis = analyze_result(result)
    assert analysis['status'] == 'no_logs' and analysis['message'] == NO_LOG_TEXT
    assert analysis['lots'][0]['status'] == 'no_log'
    assert analysis['tip_summary']['lines'] == ['Tip Positioning 로그 없음']
    # 경로를 모르는 결과 (메모리에서 만든 결과)
    no_path = dict(result, round_path='')
    assert analyze_result(no_path)['status'] == 'no_logs'
    assert analyze_result({'raw_data': []})['status'] == 'no_data'


# ── Lot 여럿 ─────────────────────────────────────────────────────

def test_multiple_lots_per_lot_and_die_mean(tmp_path):
    root = tmp_path / 'Vision Pattern'
    make_lot(root, '46LKD105SJE61')
    make_lot(root, '46LKD105SJE62', x_shift_nm=300.0)      # Lot 평균만 다르다
    make_lot(root, '46LKD105SJE63', sample_log=None)       # 로그 없는 Lot
    analysis = analyze_result(load(root))
    assert [lot['status'] for lot in analysis['lots']] == ['ok', 'ok', 'no_log']
    assert analysis['status'] == 'partial' and analysis['mean']['lots'] == 2
    assert analysis['message'] == 'Lot 2개 대조 (제외: 로그 없음 1개)'
    # Lot마다 편차 기준(회차 평균)이 같아 Die별 Lot 평균의 θx는 Lot별 θx와 같다
    per_lot = [row(lot['stages'], 'final', 'X')['coef']['θx']['value']
               for lot in analysis['lots'][:2]]
    mean = row(analysis['mean']['stages'], 'final', 'X')['coef']['θx']['value']
    assert per_lot[0] == pytest.approx(5.28, abs=0.01)
    assert mean == pytest.approx(sum(per_lot) / 2, abs=1e-6)
    assert len(analysis['tips']) == 3       # Tip 로그는 세 Lot 모두 있다


def test_store_on_matching_run_but_not_read_errors(tmp_path):
    result = load(make_lot(tmp_path / 'vp'))
    run = dict(result)
    result['run_results'] = [run, dict(result, round='other')]
    analysis = analyze_result(result)
    assert store_analysis(result, analysis) is True
    assert result['positioning_log'] is analysis and run['positioning_log'] is analysis
    assert 'positioning_log' not in result['run_results'][1]
    fresh = dict(result)
    fresh.pop('positioning_log')
    broken = dict(analysis, lots=[dict(analysis['lots'][0], status='error')])
    assert store_analysis(fresh, broken) is False and 'positioning_log' not in fresh


# ── Tip Positioning 위치가 여럿 ───────────────────────────────────

def test_tip_x_versus_stage_y_with_three_locations(tmp_path):
    """Location이 TOP/CENTER/BOTTOM이면 그 위치를 쓰는 Die의 Stage Y 평균으로 기울기를 낸다."""
    lines = SAMPLE_LOG.read_text(encoding='utf-8').splitlines()
    header = lines[0].split('\t')
    loc, sy = header.index('Location'), header.index('Stage Y (um)')
    out = [lines[0]]
    for line in lines[1:]:
        cells = line.split('\t')
        y = float(cells[sy])
        cells[loc] = 'TOP' if y > 60000 else 'BOTTOM' if y < -60000 else 'CENTER'
        out.append('\t'.join(cells))
    sample = tmp_path / 's_SamplePositioning.txt'
    sample.write_text('\n'.join(out) + '\n', encoding='utf-8')
    stage_y = {}
    for line in out[1:]:
        cells = line.split('\t')
        stage_y.setdefault(cells[loc].lower(), []).append(float(cells[sy]) / 1000)
    means = {k: sum(v) / len(v) for k, v in stage_y.items()}
    tip_header = TIP_LOG.read_text(encoding='utf-8').splitlines()[0]
    tip_rows = [tip_header]
    for name in ('Top', 'Center', 'Bottom'):
        fine_x = 1.0 + 0.005 * means[name.lower()]          # 5 µrad
        tip_rows.append(f'19\t{name}\t6594.69\tTRUE\t100.00\t1225.32\t389.53\t1.82\t72.14\t'
                        f'6600.88\tTRUE\t99.85\t1225.07\t389.69\t{fine_x:.4f}\t72.09\t')
    tip = tmp_path / 's_TipPositioning.txt'
    tip.write_text('\n'.join(tip_rows) + '\n', encoding='utf-8')

    analysis = analyze_result(load(make_lot(tmp_path / 'vp', sample_log=sample, tip_log=tip)))

    summary = analysis['tip_summary']
    assert summary['locations'] == ['Bottom', 'Center', 'Top']
    assert summary['slope_x'] == pytest.approx(5.0, abs=0.01)
    assert summary['flag'] is True                      # Tip X 변동이 0.5 µm를 넘는다
    assert any('Tip X 대 스테이지 Y 기울기' in l and '최종 착지 θx +5.28' in l
               for l in summary['lines'])


def test_tip_location_name_with_number():
    tips = [{'tip_x': 1.0 + 0.002 * y, 'tip_y': 72.0, 'location': f'Y{y:+d}', 'stage_y': y}
            for y in (120, 0, -120)]
    summary = tip_summary(tips)
    assert summary['slope_x'] == pytest.approx(2.0)
    assert summary['slope_y'] == pytest.approx(0.0)


def test_location_y_from_name():
    from core.positioning_log import _location_y
    assert _location_y('Y+120', []) == 120.0
    assert _location_y('Center', []) == 0.0
    assert _location_y('Top', []) is None


# ── AI 분석용 리포트 ──────────────────────────────────────────────

def test_llm_report_section(real_result):
    from core.llm_report import build_llm_report
    md = build_llm_report([real_result], SPEC_LIM, SPEC_DEV)
    section = md[md.index('## 비전 보정 로그 대조 (Positioning Log)'):md.index('## 회차 변경 이력')]
    assert '- 상태: Lot 1개 대조' in section
    assert '46LKD105SJE61: 로그 22행 ↔ Die 22개 짝지음 (정렬 회전 +0.046°' in section
    assert '| 최종 착지 (HZ1_O 편차) | X | Sx -0.13 ppm | -0.4 | θx +5.28 µrad | +16.9 |' in section
    assert '| 정밀 보정 (Fine Move) | X | Sx +4.30 ppm | +2.2 | θx -0.19 µrad | -0.1 |' in section
    assert ('- 판정 (파생): Y 위치에 비례하는 X 오차(θx) +5.28 µrad (t +16.9)가 비전 보정 로그에는 '
            '없고 최종 착지(HZ1_O)에만 있음 → 비전 정렬 이후(카메라→Tip 착지) 구간') in section
    assert '#### Tip Positioning' in section
    assert '| 46LKD105SJE61 | Center | +1.74 | +72.09 | Fine Match X/Y | 99.85 | +0.0 |' in section
    assert '위치 결정 단계 (비전 보정 로그 대조' in md[md.index('## LLM 분석 요청'):]


def test_llm_report_without_logs_says_so():
    from core.llm_report import build_llm_report
    rows = [{'lot_name': 'L1', 'lot_index': 1, 'site_id': f'{d:04d}_X000_Y000', 'method': a,
             'value': 100.0 + d, 'valid': True, 'x_um': 1000.0 * d, 'y_um': 7.0 * d}
            for d in range(1, 23) for a in 'XY']
    md = build_llm_report([{'recipe': 'R', 'short_name': 'R', 'round': '1st', 'raw_data': rows}])
    section = md[md.index('## 비전 보정 로그 대조'):md.index('## 회차 변경 이력')]
    assert f'- 상태: {NO_LOG_TEXT}' in section


# ── 로더 스레드·화면 ─────────────────────────────────────────────

def test_loader_thread_does_not_read_logs(tmp_path, monkeypatch):
    """데이터 로드는 로그를 읽지 않는다 (네트워크 왕복을 늘리지 않게). 탭이 보일 때 읽는다."""
    from core import positioning_log
    from ui.workers.data_loader_thread import DataLoaderThread
    monkeypatch.setattr(positioning_log, 'find_logs',
                        lambda *a, **k: pytest.fail('로드 중에 로그를 읽었다'))
    lot = make_lot(tmp_path / 'vp')
    recipes, _ = recipes_from_mapping([('Vision Pattern', str(lot))], ['Vision Pattern'])
    thread = DataLoaderThread('', recipes=recipes)
    got = []
    thread.finished.connect(lambda results, _c, _e: got.append(results))
    thread.run()        # 화면 없이 그대로 돈다
    assert got and 'positioning_log' not in got[0][0]


@pytest.fixture
def window(monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import main
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_limits=SPEC_LIM,
                                                           spec_deviation=SPEC_DEV))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    yield win
    win.deleteLater()
    app.processEvents()


def _show_tab(window):
    advanced = window._inner_tabs['Advanced']
    window.chart_category_tabs.setCurrentWidget(advanced)
    for i in range(advanced.count()):
        if advanced.tabText(i) == 'Positioning Log':
            advanced.setCurrentIndex(i)


def test_positioning_tab_shows_table_verdict_and_chart(window, real_result):
    store_analysis(real_result, analyze_result(real_result))
    window._update_positioning_log(real_result)
    assert window.poslog_table.rowCount() == 16
    assert window.poslog_table.item(14, 0).text() == '최종 착지 (HZ1_O 편차)'
    assert window.poslog_table.item(14, 4).text() == 'θx +5.28 µrad'
    assert '비전 정렬 이후(카메라→Tip 착지) 구간' in window.poslog_label.text()
    assert window.poslog_tip_table.rowCount() == 1
    assert window.chart_widgets['Positioning Stages']._canvas is None     # 탭이 보일 때 그린다
    _show_tab(window)
    assert window.chart_widgets['Positioning Stages']._canvas is not None


def test_positioning_tab_reads_other_runs_only_when_visible(window, real_result):
    from PySide6.QtWidgets import QApplication
    window._update_positioning_log(real_result)
    assert 'positioning_log' not in real_result
    assert '탭을 열 때 읽습니다' in window.poslog_label.text()
    _show_tab(window)
    deadline = time.monotonic() + 15
    while 'positioning_log' not in real_result:
        assert time.monotonic() < deadline
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()
    assert real_result['positioning_log']['status'] == 'ok'
    assert window.poslog_table.rowCount() == 16


def test_positioning_tab_without_logs(window):
    window._update_positioning_log({'raw_data': [{'x': 1}], 'round_path': ''})
    assert window.poslog_label.text() == NO_LOG_TEXT
    window._update_positioning_log({'raw_data': []})
    assert window.poslog_label.text() == '분석할 데이터가 없음'


# ── 리뷰 반영 (c4ee4f1) ─────────────────────────────────────────

def _positions():
    from core.die_analysis import extract_die_positions, filter_stabilization_die
    data = filter_stabilization_die(load_sites_result()['raw_data'])
    return {f'Die{i + 1}': p for i, p in extract_die_positions(data).items()}


def _plane_values(positions, theta, sx, noise, seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    return {d: theta * 1e-3 * y + sx * 1e-3 * x + float(rng.normal(0, noise))
            for d, (x, y) in sorted(positions.items())}


def test_opposite_sign_term_is_not_hidden_by_shared_sx():
    """HIGH 재현: 최종 θx +5.27, Coarse Match θx −3.12이고 값 그대로의 r +0.70은 공통 Sx에서 온다.
    판정은 부분 잔차 r(θx 항)을 쓰고 부호 관계를 적는다 (예전: r +0.70으로 '같은 성분' 판정)."""
    from core.positioning_log import judge, stage_table
    positions = _positions()
    values = {'final': {'X': _plane_values(positions, 5.27, 10.0, 0.02, 1)},
              'coarse_match': {'X': _plane_values(positions, -3.12, 10.0, 0.02, 2)}}
    table = stage_table(values, positions)
    coarse = row(table, 'coarse_match', 'X')
    assert coarse['r'] == pytest.approx(0.70, abs=0.05)              # 공통 Sx 때문
    assert coarse['r_terms']['θx'] == pytest.approx(-1.0, abs=0.02)  # θx 성분끼리는 반대
    verdict = judge(table)
    theta = next(t for t in verdict['terms'] if t['term'] == 'θx')
    assert theta['details'][0]['same_sign'] is False and theta['details'][0]['r'] < 0
    line = next(l for l in verdict['lines'] if l.startswith('Y 위치에 비례하는 X 오차(θx)'))
    assert '(반대 부호, r -0.99)' in line and '같은 부호' not in line
    assert '반대 부호로 연관' in line


def test_sign_inconsistent_partial_correlation_does_not_carry():
    from core.positioning_log import judge
    final = {'stage': 'final', 'axis': 'X', 'k': 21, 'r': None, 'r_terms': {}, 'range': 1.0,
             'coef': {'Sx': {'value': 0.1, 'se': 1, 't': 0.1},
                      'θx': {'value': 5.0, 'se': 0.1, 't': 50.0}}}
    stage = {'stage': 'coarse_match', 'axis': 'X', 'k': 21, 'r': 0.8, 'range': 1.0,
             'r_terms': {'Sx': 0.1, 'θx': 0.8},          # 계수는 반대 부호인데 r은 + → 다른 데서 온 상관
             'coef': {'Sx': {'value': 0.1, 'se': 1, 't': 0.1},
                      'θx': {'value': -4.0, 'se': 0.5, 't': -8.0}}}
    verdict = judge([stage, final])
    assert verdict['terms'][0]['carriers'] == []
    assert '최종 착지(HZ1_O)에만 있음' in verdict['lines'][0]
    few = dict(stage, k=5, r_terms={'Sx': 0.1, 'θx': -0.9})     # 로그 단계도 Die 8개 이상 필요
    assert judge([few, final])['terms'][0]['carriers'] == []
    ok = dict(stage, r_terms={'Sx': 0.1, 'θx': -0.9})
    assert judge([ok, final])['terms'][0]['carriers'] == ['coarse_match']


def test_real_sample19_verdict_unchanged_with_partial_r(real_result):
    analysis = analyze_result(real_result)
    fine = row(analysis['mean']['stages'], 'fine_move', 'X')
    assert abs(fine['r_terms']['θx']) < 0.5
    x_term = next(t for t in analysis['verdict']['terms'] if t['term'] == 'θx')
    assert x_term['carriers'] == [] and x_term['span'] == '비전 정렬 이후(카메라→Tip 착지)'


def test_collinear_or_narrow_layout_gives_no_coefficient():
    from core.positioning_log import judge, plane_fit, stage_table
    line = {f'Die{i}': (10.0 * i, 10.0 * i + 0.01 * i * i) for i in range(2, 23)}   # 거의 한 직선
    values = {d: 0.004 * y for d, (x, y) in line.items()}
    fit = plane_fit(values, line)
    assert fit['dx'] is None and fit['dy'] is None
    narrow = {f'Die{i}': (1.0 * (i % 3), 10.0 * i) for i in range(2, 23)}           # X 퍼짐 < 5 mm
    fit = plane_fit({d: 0.004 * y for d, (x, y) in narrow.items()}, narrow)
    assert fit['dx'] is None and fit['dy']['value'] == pytest.approx(4.0)
    table = stage_table({'final': {'X': {d: 0.005 * y for d, (x, y) in line.items()}}}, line)
    assert row(table, 'final', 'X')['coef'] == {'Sx': None, 'θx': None}
    verdict = judge(table)
    assert verdict['terms'] == [] and '정할 수 없는 계수' in verdict['lines'][0]


def test_mapping_rejects_rotated_symmetric_match():
    sites = _sites()
    rows = parse_sample_positioning(str(SAMPLE_LOG))
    turned = [dict(r, stage_x=-r['stage_y'], stage_y=r['stage_x']) for r in rows]   # 90° 돈 배치
    mapping = map_rows_to_dies(turned, sites)
    assert mapping['status'] == 'mismatch'
    assert '맞춘 회전' in mapping['problems'][0] and '1°를 넘어' in mapping['problems'][0]


def test_log_choice_prefers_csv_prefix_and_rejects_ambiguous(tmp_path):
    lot = make_lot(tmp_path / 'a')
    folder = lot / 'Debug' / 'SamplePositioning'
    shutil.copy(SAMPLE_LOG, folder / 'Other7_SamplePositioning.txt')
    logs = find_logs(str(lot), csv_prefix='sample19')
    assert os.path.basename(logs['sample']) == 'Sample19_SamplePositioning.txt'
    assert analyze_result(load(lot))['status'] == 'ok'

    lot2 = make_lot(tmp_path / 'b')
    folder2 = lot2 / 'Debug' / 'SamplePositioning'
    (folder2 / 'Sample19_SamplePositioning.txt').rename(folder2 / 'A1_SamplePositioning.txt')
    shutil.copy(SAMPLE_LOG, folder2 / 'B2_SamplePositioning.txt')
    analysis = analyze_result(load(lot2))
    assert analysis['lots'][0]['status'] == 'mismatch'
    assert 'SamplePositioning 로그가 2개인데 CSV 이름(sample19)과 맞는 것이 없음' in \
        analysis['lots'][0]['problems'][0]


def test_slot_mismatch_is_reported(tmp_path):
    lot = make_lot(tmp_path / 'vp')
    for name in ('SAMPLE19_X_UL.csv', 'SAMPLE19_Y_UL.csv'):
        path = lot / name
        path.write_text(path.read_text(encoding='utf-8').replace('Slot No,19', 'Slot No,20'),
                        encoding='utf-8')
    analysis = analyze_result(load(lot))
    assert analysis['status'] == 'mismatch'
    assert analysis['lots'][0]['problems'] == ['로그 Slot 19 ≠ CSV Slot No 20 (다른 샘플의 로그)']


def test_network_error_is_not_reported_as_missing_and_not_cached(tmp_path, monkeypatch):
    from core import csv_loader, positioning_log
    monkeypatch.setattr(csv_loader, 'FOLDER_RETRY_DELAY', 0)
    result = load(make_lot(tmp_path / 'vp'))
    real = positioning_log.list_dir
    calls = []

    def flaky(path, *args):
        if os.path.basename(path).lower() == 'debug':
            calls.append(path)
            raise OSError(64, '지정된 네트워크 이름을 더 이상 사용할 수 없습니다')
        return real(path, *args)
    monkeypatch.setattr(positioning_log, 'list_dir', flaky)
    analysis = analyze_result(result)
    assert len(calls) == 2                                   # 한 번 더 읽어 본다
    assert analysis['status'] == 'error' and analysis['lots'][0]['status'] == 'error'
    assert analysis['message'].startswith('로그를 읽지 못함')
    assert NO_LOG_TEXT not in analysis['message']
    assert store_analysis(result, analysis) is False and 'positioning_log' not in result


def test_locked_file_and_utf16_bom(tmp_path, monkeypatch):
    from core import positioning_log
    utf16 = tmp_path / 'u_SamplePositioning.txt'
    utf16.write_bytes(SAMPLE_LOG.read_text(encoding='utf-8').encode('utf-16'))
    assert parse_sample_positioning(str(utf16))[0]['coarse_match_x'] == -2.39
    monkeypatch.setattr(positioning_log, '_read_file_bytes', lambda path: b'')
    with pytest.raises(positioning_log.LogReadError, match='잠김 또는 권한'):
        parse_sample_positioning(str(SAMPLE_LOG))
    analysis = analyze_result(load(make_lot(tmp_path / 'vp')))
    assert analysis['status'] == 'error'
    assert '파일을 열 수 없음 (잠김 또는 권한): Sample19_SamplePositioning.txt' in analysis['message']


def test_duplicate_header_prefers_micrometre_column(tmp_path):
    path = tmp_path / 'd_SamplePositioning.txt'
    path.write_text('Slot\tPoint\tCoarse Match X (pxl)\tCoarse Match X (um)\tStage X (um)\tStage Y (um)\n'
                    '19\t1\t812.0\t-2.39\t1\t2\n', encoding='utf-8')
    assert parse_sample_positioning(str(path))[0]['coarse_match_x'] == -2.39


def test_tip_slope_needs_two_locations_and_span():
    near = [{'tip_x': 1.0, 'tip_y': 72.0, 'location': 'A', 'stage_y': 0.0},
            {'tip_x': 1.1, 'tip_y': 72.0, 'location': 'B', 'stage_y': 5.0}]
    summary = tip_summary(near)
    assert summary['slope_x'] is None
    assert any('Y 범위 5 mm' in l and '10 mm 이상 필요' in l for l in summary['lines'])
    one_place = [{'tip_x': 1.0 + k, 'tip_y': 72.0, 'location': 'Center', 'stage_y': y}
                 for k, y in enumerate((0.0, 50.0))]
    assert tip_summary(one_place)['slope_x'] is None
    from core.positioning_log import _location_y
    assert _location_y('Top+5', []) is None and _location_y('y-120', []) == -120.0


def test_llm_section_survives_a_failing_workflow(real_result, monkeypatch):
    from core import positioning_log
    from core.llm_report import build_llm_report

    def boom(*_a, **_k):
        raise RuntimeError('깨진 로그')
    monkeypatch.setattr(positioning_log, 'analyze_result', boom)
    md = build_llm_report([real_result], SPEC_LIM, SPEC_DEV)
    assert '- 상태: 로그 대조 실패 (RuntimeError: 깨진 로그)' in md
    assert '## LLM 분석 요청' in md


def test_stale_read_does_not_block_and_same_analysis_is_not_redrawn(window, real_result,
                                                                    monkeypatch):
    from PySide6.QtWidgets import QApplication
    from core.positioning_log import run_key
    old = window._data_generation
    window._data_generation = old + 1           # 그사이 다시 스캔했다
    window._poslog_result = real_result
    window._poslog_reading = (run_key(real_result), old, 999)    # 지난 세대의 읽기가 아직 돈다
    _show_tab(window)                                            # 새 세대 읽기는 막히지 않는다
    assert window._poslog_reading is not None and window._poslog_reading[1] == old + 1
    window._on_positioning_done(real_result, {'status': 'no_logs'}, (old, 999))   # 늦은 결과
    assert 'positioning_log' not in real_result
    deadline = time.monotonic() + 15
    while 'positioning_log' not in real_result:
        assert time.monotonic() < deadline
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()
    assert window.poslog_table.rowCount() == 16
    drawn = []
    monkeypatch.setattr(window, '_render_positioning', lambda a: drawn.append(a))
    window._update_positioning_log(real_result)                  # 같은 분석: 다시 그리지 않는다
    assert drawn == []


def test_finished_read_is_stored_on_every_copy_of_the_run(window, real_result):
    from core.positioning_log import run_key
    copy = dict(real_result)                 # run_view가 만든 사본처럼
    other = dict(real_result, round='other')
    window.recipe_results = [dict(real_result, run_results=[real_result, other])]
    window._poslog_result = copy
    analysis = analyze_result(real_result)
    window._on_positioning_done(real_result, analysis, (window._data_generation, 1))
    assert copy['positioning_log'] is analysis
    assert window.recipe_results[0]['run_results'][0]['positioning_log'] is analysis
    assert 'positioning_log' not in other
    assert run_key(copy) == run_key(real_result)
