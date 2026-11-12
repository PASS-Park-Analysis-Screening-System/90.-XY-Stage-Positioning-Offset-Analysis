"""스캐너 회전 시험 — 폐루프 2지점 스윕의 κ 복원, 판정 구간, 로그·CSV·xlsx 해석, 글·CSV, 창.

합성 측정: 지점 좌표 c(mm)에서 이득 g = g0 + κ·c/1000 (κ µrad/µm), 직각 기울기 φ(mrad)를
화면 좌표(px)로 바꿔 0.03 px 잡음을 더하고 로그처럼 소수 둘째 자리로 적는다.
(실제 PQC Non-linearity 분석 시트는 Desktop에 있고 저장소에 넣지 않는다 — 여기서는 같은 모양의 작은 시트를 만든다)
"""
import csv
import json
import math
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import scanner_rotation_test as sr  # noqa: E402

PS = sr.DEFAULT_PIXEL_SIZE_UM
CMDS = sr.DEFAULT_COMMANDS


def _pixels(gain, perp_mrad, axis='Y', cmds=CMDS, noise=0.03, seed=0):
    """지시 µm → 화면 매칭 좌표 (x_px, y_px)."""
    rng = np.random.default_rng(seed)
    cmds = np.asarray(cmds, float)
    along = gain * cmds
    perp = perp_mrad / 1000.0 * cmds
    x_um, y_um = (perp, along) if axis == 'Y' else (along, perp)
    x = 1200.0 + x_um / (sr.PIXEL_X_SIGN * PS) + rng.normal(0, noise, cmds.size)
    y = 680.0 + y_um / (sr.PIXEL_Y_SIGN * PS) + rng.normal(0, noise, cmds.size)
    return x, y


def _log(gain, perp_mrad=13.0, axis='Y', seed=0, missing=(), extra=False):
    x, y = _pixels(gain, perp_mrad, axis, seed=seed)
    lines = []
    for i, c in enumerate(CMDS):
        if c in missing:
            lines.append('15:22:%02d Match fail' % i)
            continue
        lines.append(f'15:22:{i:02d} Match 1 : ({x[i]:.2f}, {y[i]:.2f}),Angle = 0.02,Score = 0.97')
        if extra:
            lines.append(f'15:22:{i:02d} Match 2 : ({x[i] + 300:.2f}, {y[i]:.2f}),Angle = 0.10,Score = 0.41')
            lines.append('')
    return '\n'.join(lines)


def _csv(gain, perp_mrad=13.0, axis='Y', seed=0, header=True, delim=','):
    x, y = _pixels(gain, perp_mrad, axis, seed=seed)
    rows = [delim.join(['command_um', 'x_px', 'y_px', 'angle', 'score'])] if header else []
    rows += [delim.join([f'{c:g}', f'{x[i]:.3f}', f'{y[i]:.3f}', '0.01', '0.98']) for i, c in enumerate(CMDS)]
    return '\n'.join(rows)


def _gains(kappa, coords=sr.DEFAULT_SPOT_MM, g0=1.0):
    return tuple(g0 + kappa * c / 1000.0 for c in coords)


# ── κ 복원 ───────────────────────────────────────────────

@pytest.mark.parametrize('kappa, status', [(0.107, sr.STATUS_FAIL), (0.0, sr.STATUS_OK), (-0.085, sr.STATUS_WARN)])
def test_log_text_recovers_known_kappa(kappa, status):
    ga, gb = _gains(kappa)
    a = sr.parse_spot_text(_log(ga, 13.1, seed=1), CMDS, 'A')
    b = sr.parse_spot_text(_log(gb, 13.4, seed=2), CMDS, 'B')
    res = sr.analyze_rotation_test(a, b, 'Y', PS, (-120, 120), stage='출하 검사', date='2026-11-12T16:00:00')
    assert res['kappa'] == pytest.approx(kappa, abs=0.003)
    assert 0 < res['kappa_sigma'] < 0.003
    assert res['status'] == status and res['loop'] == 'closed' and res['axis'] == 'Y'
    assert res['stage'] == '출하 검사' and res['date'] == '2026-11-12T16:00:00'
    assert res['distance_mm'] == 240
    a_loc, b_loc = res['locations']
    assert a_loc['name'] == 'A' and a_loc['stage_x_mm'] == -120 and b_loc['stage_x_mm'] == 120
    assert a_loc['gain'] == pytest.approx(ga, abs=0.001) and b_loc['gain'] == pytest.approx(gb, abs=0.001)
    assert a_loc['perp_mrad'] == pytest.approx(13.1, abs=0.3) and b_loc['perp_mrad'] == pytest.approx(13.4, abs=0.3)
    assert a_loc['n'] == 15 and a_loc['residual_pp_um'] < 0.1
    assert res['reasons'][0].startswith('κ = ')
    assert any(sr.STATUS_LABELS[status] in r for r in res['reasons'])
    json.dumps(res, allow_nan=False)                     # 진단 JSON에 그대로 들어간다


def test_documented_example_gain_difference():
    """이득 차 0.0256 / 240 mm → κ 0.107 (WI 계산 예)."""
    kappa, sigma = sr.kappa_from_gains(1.0012, 1.0268, -120, 120, 1e-4, 1e-4)
    assert kappa == pytest.approx(0.1067, abs=1e-4)
    assert sigma == pytest.approx(math.sqrt(2) * 1e-4 / 240 * 1000)
    assert sr.kappa_from_gains(1.0, 1.1, 50, 50) == (None, None)


@pytest.mark.parametrize('header, delim', [(True, ','), (False, ','), (True, '\t')])
def test_csv_text_recovers_known_kappa(header, delim):
    ga, gb = _gains(0.107)
    a = sr.parse_spot_text(_csv(ga, seed=3, header=header, delim=delim), CMDS, 'A')
    b = sr.parse_spot_text(_csv(gb, seed=4, header=header, delim=delim), CMDS, 'B')
    assert a.kind == 'csv' and a.commands.tolist() == list(CMDS)
    res = sr.analyze_rotation_test(a, b)
    assert res['kappa'] == pytest.approx(0.107, abs=0.003) and res['status'] == 'fail'


def test_x_sweep_control_and_combine():
    ga, gb = _gains(0.02)
    ya, yb = _gains(0.107)
    y_res = sr.analyze_rotation_test(sr.parse_spot_text(_log(ya), CMDS, 'A'), sr.parse_spot_text(_log(yb), CMDS, 'B'))
    x_res = sr.analyze_rotation_test(sr.parse_spot_text(_log(ga, axis='X', seed=5), CMDS, 'A'),
                                     sr.parse_spot_text(_log(gb, axis='X', seed=6), CMDS, 'B'), axis='X')
    assert x_res['locations'][0]['stage_y_mm'] == -120 and x_res['locations'][0]['stage_x_mm'] is None
    assert x_res['kappa'] == pytest.approx(0.02, abs=0.003) and x_res['status'] == 'ok'
    both = sr.combine_control(y_res, x_res)
    assert both['status'] == 'fail' and both['control']['axis'] == 'X'
    assert both['control']['kappa'] == x_res['kappa']
    assert '대조 X 스윕' in both['reasons'][-1] and '참고' in both['reasons'][-1]
    assert y_res['control'] is None                        # 원본은 그대로


def test_closed_loop_sanity_reasons():
    """개루프처럼 이득이 1에서 멀면 servo·픽셀 크기를 확인하라고 한다. 좌표가 같으면 κ 없음."""
    a = sr.parse_spot_text(_log(1.19), CMDS, 'A')
    b = sr.parse_spot_text(_log(1.33), CMDS, 'B')
    res = sr.analyze_rotation_test(a, b)
    assert any('servo OFF' in r for r in res['reasons'])
    same = sr.analyze_rotation_test(a, b, coords_mm=(100, 100))
    assert same['kappa'] is None and same['status'] == 'na'
    assert '좌표가 같아' in same['reasons'][0]


def test_boundary_uncertainty_is_flagged():
    ga, gb = _gains(0.100)                                  # 경계 위 — 잡음(σ ≈ 0.0004)으로 어느 쪽이든 나온다
    a = sr.parse_spot_text(_log(ga, seed=7), CMDS, 'A')
    b = sr.parse_spot_text(_log(gb, seed=8), CMDS, 'B')
    res = sr.analyze_rotation_test(a, b)
    assert any('판정 경계 0.1' in r for r in res['reasons'])


# ── 판정 구간·규격 ───────────────────────────────────────

@pytest.mark.parametrize('kappa, status', [
    (0.0, 'ok'), (0.07, 'ok'), (-0.07, 'ok'), (0.0701, 'warn'), (0.10, 'warn'), (-0.10, 'warn'),
    (0.1001, 'fail'), (-0.2, 'fail'), (None, 'na'), (float('nan'), 'na'),
])
def test_status_bands(kappa, status):
    assert sr.classify_kappa(kappa) == status


def test_spec_from_settings_overrides_defaults():
    spec = sr.resolve_spec({'kappa_max': 0.2, 'kappa_warn': 'bad', 'provisional': False, 'unknown': 1})
    assert spec['kappa_max'] == 0.2 and spec['kappa_warn'] == 0.07 and spec['provisional'] is False
    assert 'unknown' not in spec
    assert sr.classify_kappa(0.15, spec) == 'warn'
    assert sr.resolve_spec({'kappa_max': 0.05})['kappa_warn'] == 0.05      # 주의 ≤ 초과 경계
    assert sr.spec_text().startswith('잠정 규격') and sr.spec_text(spec).startswith('규격')


# ── 해석 가장자리 ────────────────────────────────────────

def test_parse_commands():
    assert sr.parse_commands('-70:70:10') == list(CMDS)
    assert sr.parse_commands('70:-70:-10') == list(reversed(CMDS))
    assert sr.parse_commands('−70, −60; 0\n10 20') == [-70, -60, 0, 10, 20]
    assert sr.format_commands(CMDS) == '-70:70:10'
    assert sr.format_commands([0, 5, 20]) == '0, 5, 20'
    for bad in ('', '-70:70', '-70:70:0', '70:-70:10', '1, 2, x', '5, 5, 5', '1, 2'):
        with pytest.raises(sr.RotationTestError):
            sr.parse_commands(bad)


def test_log_blank_lines_timestamps_and_extra_candidates():
    ga, _ = _gains(0.0)
    sweep = sr.parse_spot_text('\n\n' + _log(ga, extra=True) + '\n\n   \nGRAB done\n', CMDS, 'A')
    assert sweep.kind == 'log' and sweep.x_px.size == 15 and np.isfinite(sweep.x_px).all()
    assert np.allclose(sweep.angle, 0.02) and np.allclose(sweep.score, 0.97)


def test_missing_steps_are_kept_in_place_and_reported():
    ga, gb = _gains(0.107)
    sweep = sr.parse_spot_text(_log(ga, missing=(-30.0,)), CMDS, 'A')       # 'Match fail' 줄
    assert np.isnan(sweep.x_px[4]) and np.isfinite(sweep.x_px).sum() == 14
    marked = _log(gb).splitlines()
    marked[10] = '  -  '                                   # 사용자가 '-'로 빠진 단계를 표시
    b = sr.parse_spot_text('\n'.join(marked), CMDS, 'B')
    res = sr.analyze_rotation_test(sweep, b)
    assert res['locations'][0]['n'] == 14 and res['locations'][0]['missing_um'] == [-30.0]
    assert res['locations'][1]['missing_um'] == [30.0]
    assert any('명령 -30 µm 단계 측정 없음' in r for r in res['reasons'])
    assert res['locations'][0]['points']['along_um'][4] is None
    assert res['kappa'] == pytest.approx(0.107, abs=0.004)


def test_wrong_step_count_is_rejected():
    lines = _log(1.0).splitlines()
    with pytest.raises(sr.RotationTestError, match='명령 15단계인데 측정 줄이 14개'):
        sr.parse_spot_text('\n'.join(lines[:-1]), CMDS, 'A')
    with pytest.raises(sr.RotationTestError, match='16개'):
        sr.parse_spot_text('\n'.join(lines + lines[:1]), CMDS, 'A')
    with pytest.raises(sr.RotationTestError, match='붙여 넣거나'):
        sr.parse_spot_text('  \n ', CMDS, 'A')


def test_leading_command_column_from_sheet_copy():
    """분석 시트 A~D 열 복사(명령 탭 Match …)는 줄 앞 명령을 쓴다 — 명령 목록 길이와 달라도 된다."""
    x, y = _pixels(1.0, 0.0)
    text = '\n'.join(f'{c:g}\tMatch 1 : ({x[i]:.2f}, {y[i]:.2f}),\tAngle = 0.03,\tScore = 0.98'
                     for i, c in enumerate(CMDS) if i % 2 == 0)
    sweep = sr.parse_spot_text(text, CMDS, 'A')
    assert sweep.commands.tolist() == list(CMDS[::2])
    assert '줄 앞의 명령 값' in sweep.notes[0]


def test_too_few_points_and_bad_csv():
    lines = ['누락'] * 13 + _log(1.0).splitlines()[13:]
    sweep = sr.parse_spot_text('\n'.join(lines), CMDS, 'A')
    with pytest.raises(sr.RotationTestError, match='2점뿐'):
        sr.analyze_sweep(sweep, 'Y', PS, 'A')
    with pytest.raises(sr.RotationTestError, match='숫자가 아닙니다'):
        sr.parse_spot_text('command_um,x_px,y_px\n-70,abc,1\n', CMDS, 'A')
    with pytest.raises(sr.RotationTestError, match='머리글'):
        sr.parse_spot_text('foo,bar\n1,2\n', CMDS, 'A')
    blank = sr.parse_spot_text('command_um,x_px,y_px\n-10,,\n0,1,1\n10,2,2\n20,3,3\n', CMDS, 'A')
    assert np.isnan(blank.x_px[0]) and blank.commands.tolist() == [-10, 0, 10, 20]
    with pytest.raises(sr.RotationTestError, match='픽셀 크기'):
        sr.pixel_to_um([1, 2], [1, 2], 0)


def test_read_text_file_encodings(tmp_path):
    p = tmp_path / 'a.txt'
    p.write_bytes('메모\n15:22:33 Match 1 : (1.0, 2.0),Angle = 0.0,Score = 0.9\n'.encode('cp949'))
    assert '메모' in sr.read_text_file(p)
    with pytest.raises(sr.RotationTestError, match='읽을 수 없습니다'):
        sr.read_text_file(tmp_path / 'none.txt')


# ── PQC Non-linearity 시트 (개루프) ───────────────────────

GAINS = {'X1': 1.21, 'X2': 1.30, 'Y1': 1.19, 'Y2': 1.33}
STROKES = {'X1': (257.8, 259.0), 'X2': (267.2, 267.2), 'Y1': (244.1, 244.8), 'Y2': (272.3, 272.0)}


def _make_workbook(path, blank_um=False, sheets=None):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = 'After Graph Merge'
    names = sheets or [f'{ch}_{ph}' for ph in ('Before', 'After') for ch in ('X1', 'X2', 'Y1', 'Y2')]
    for k, name in enumerate(names):
        ch = name[:2]
        ws = wb.create_sheet(name)
        ws['B2'] = f'{ch}, {name[3:]} Linearization'
        ws['L2'] = 'Det\xa0Stroke'
        ws['M2'], ws['N2'] = STROKES[ch]
        ws['H3'], ws['I3'] = 'um/pxl', PS
        for col, text in zip('ABCDFGHIKQ', ('Move', 'Text', 'Angle', 'Score', 'Vision\nPixel X', 'Vision\nPixel Y',
                                            'Position\nX (µm)', 'Position\nY (µm)', f'{ch[0]}\xa0Detector',
                                            'Nonlinearity')):
            ws[f'{col}7'] = text
        x, y = _pixels(GAINS[ch], 15.0, ch[0], seed=20 + k)
        x_um, y_um = sr.pixel_to_um(x, y, PS)
        along = y_um if ch[0] == 'Y' else x_um
        for i, c in enumerate(CMDS):
            r = 8 + i
            ws[f'A{r}'] = c
            ws[f'B{r}'] = f'Match 1 : ({x[i]:.2f}, {y[i]:.2f}),'
            ws[f'F{r}'], ws[f'G{r}'] = round(float(x[i]), 2), round(float(y[i]), 2)
            if not blank_um:
                ws[f'H{r}'], ws[f'I{r}'] = float(x_um[i]), float(y_um[i])
            ws[f'K{r}'] = float(along[i]) * 1.002
            if i < 14:
                ws[f'Q{r}'] = 0.01 * math.sin(i)
        ws['P23'], ws['Q23'] = 'Max', 0.03
    wb.save(path)
    return path


def test_xlsx_open_loop_gains_differences_and_strokes(tmp_path):
    path = _make_workbook(tmp_path / 'nl.xlsx')
    parsed = sr.parse_nonlinearity_xlsx(path)
    assert [s['name'] for s in parsed['sheets']] == [f'{c}_{p}' for p in ('Before', 'After')
                                                     for c in ('X1', 'X2', 'Y1', 'Y2')]
    y1 = parsed['sheets'][2]
    assert y1['commands'] == list(CMDS) and y1['stroke_before'] == 244.1 and y1['stroke_after'] == 244.8
    assert y1['pixel_size_um'] == pytest.approx(PS)
    res = sr.analyze_open_loop(parsed, axis='Y', stage='기타', date='2026-11-12T10:00:00')
    assert res['loop'] == 'open' and res['status'] == 'na' and res['kappa'] is None
    assert [loc['name'] for loc in res['locations']] == ['Y1', 'Y2']
    assert res['locations'][0]['stage_x_mm'] is None
    assert res['locations'][0]['gain'] == pytest.approx(1.19, abs=0.002)
    ol = res['open_loop']
    assert ol['phase'] == 'After' and ol['source'] == 'nl.xlsx' and len(ol['channels']) == 8
    y_after = next(p for p in ol['pairs'] if p['phase'] == 'After' and p['axis'] == 'Y')
    assert y_after['diff_pct'] == pytest.approx((1.33 / 1.19 - 1) * 100, abs=0.3)
    assert y_after['stroke_ratio'] == pytest.approx(272.0 / 244.8, abs=1e-4)
    ch = next(c for c in ol['channels'] if c['sheet'] == 'Y2_After')
    assert ch['vision_per_detector'] == pytest.approx(1 / 1.002, abs=1e-3)
    assert ch['detector_gain'] == pytest.approx(1.33 * 1.002, abs=0.003)
    assert '개루프' in res['reasons'][0] and '가설 ④' in res['reasons'][0]
    assert any('Y2/Y1 +11.' in r for r in res['reasons'])
    assert any('좌표를 넣지 않아' in r for r in res['reasons'])
    json.dumps(res, allow_nan=False)

    with_pos = sr.analyze_open_loop(parsed, axis='Y', positions_mm=(-120, 120))
    assert with_pos['status'] == 'na' and with_pos['locations'][1]['stage_x_mm'] == 120
    assert with_pos['kappa'] == pytest.approx((1.33 - 1.19) / 240 * 1000, abs=0.02)
    assert any('개루프 비틀림' in r and '판정하지 않습니다' in r for r in with_pos['reasons'])
    x_res = sr.analyze_open_loop(parsed, axis='X')
    assert [loc['name'] for loc in x_res['locations']] == ['X1', 'X2']


def test_xlsx_without_um_columns_uses_pixels_and_partial_sheets(tmp_path):
    path = _make_workbook(tmp_path / 'px.xlsx', blank_um=True, sheets=['Y1_Before', 'Y2_Before', 'X1_After'])
    parsed = sr.parse_nonlinearity_xlsx(path)
    res = sr.analyze_open_loop(parsed)
    assert res['open_loop']['phase'] == 'Before'
    assert res['locations'][1]['gain'] == pytest.approx(1.33, abs=0.002)
    none = sr.analyze_open_loop(parsed, axis='X')
    assert none['locations'] == [] and any('X1·X2 시트가 함께 있는' in r for r in none['reasons'])


def test_xlsx_errors_and_cancel(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = 'Sheet1'
    wb.save(tmp_path / 'other.xlsx')
    with pytest.raises(sr.RotationTestError, match='X1_Before'):
        sr.parse_nonlinearity_xlsx(tmp_path / 'other.xlsx')
    (tmp_path / 'bad.xlsx').write_bytes(b'not a zip')
    with pytest.raises(sr.RotationTestError, match='열 수 없습니다'):
        sr.parse_nonlinearity_xlsx(tmp_path / 'bad.xlsx')
    path = _make_workbook(tmp_path / 'nl.xlsx')

    def stop(done, total):
        if done >= 1:
            raise sr.AnalysisCancelled()
    with pytest.raises(sr.AnalysisCancelled):
        sr.parse_nonlinearity_xlsx(path, progress=stop)
    os.remove(path)                                        # 읽기 전용으로 열었다 닫았으니 잠겨 있지 않다


# ── 글·CSV ───────────────────────────────────────────────

def _closed_result():
    ga, gb = _gains(0.107)
    a = sr.parse_spot_text(_log(ga, missing=(10.0,)), CMDS, 'A')
    b = sr.parse_spot_text(_log(gb, seed=9), CMDS, 'B')
    xa, xb = _gains(0.01)
    ctrl = sr.analyze_rotation_test(sr.parse_spot_text(_log(xa, axis='X'), CMDS, 'A'),
                                    sr.parse_spot_text(_log(xb, axis='X', seed=10), CMDS, 'B'), axis='X')
    return sr.combine_control(sr.analyze_rotation_test(a, b, stage='스캐너 교체·선형화 후'), ctrl)


def test_report_text_and_csv(tmp_path):
    res = _closed_result()
    text = sr.format_report(res)
    assert text.startswith('[스캐너 회전 시험]') and '스캐너 교체·선형화 후' in text and '폐루프' in text
    assert '판정: 초과' in text and '잠정 규격' in text and '[대조 X 스윕]' in text
    assert '지점 A: 스테이지 X -120 mm' in text and '지점 A: 스테이지 Y -120 mm' in text
    out = tmp_path / 'rot.csv'
    sr.write_csv(out, res)
    with open(out, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f))
    assert rows[0][0] == '스캐너 회전 시험' and rows[1][5] == '초과'
    assert sr.LOCATION_HEADERS in rows and sr.POINT_HEADERS in rows
    loc_rows = [r for r in rows if len(r) == len(sr.LOCATION_HEADERS) and r[0] in ('Y 스윕', '대조 X 스윕')]
    assert len(loc_rows) == 4 and loc_rows[0][10] == '10'
    point_rows = [r for r in rows if len(r) == len(sr.POINT_HEADERS) and r[0] == 'Y 스윕']
    assert len(point_rows) == 30 and point_rows[8][2] == '10' and point_rows[8][5] == ''   # 빠진 단계는 빈 칸
    assert all(not r[1].startswith('=') for r in rows if r and r[0] == '이유')


def test_open_loop_report_and_csv(tmp_path):
    res = sr.analyze_open_loop(sr.parse_nonlinearity_xlsx(_make_workbook(tmp_path / 'nl.xlsx')))
    text = sr.format_report(res)
    assert '개루프' in text and '판정: 없음' in text and 'Y2_After' in text
    out = tmp_path / 'open.csv'
    sr.write_csv(out, res)
    with open(out, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f))
    assert sr.CHANNEL_HEADERS in rows
    assert sum(1 for r in rows if r and r[0].endswith(('_Before', '_After'))) == 8


def test_csv_text_cells_cannot_start_formulas(tmp_path):
    res = _closed_result()
    res['stage'] = '=HYPERLINK("x")'
    res['reasons'] = ['-1+1', '@SUM(A1)']
    out = tmp_path / 'f.csv'
    sr.write_csv(out, res)
    with open(out, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f))
    assert rows[0][2].startswith("'=") and [r[1] for r in rows if r and r[0] == '이유'] == ["'-1+1", "'@SUM(A1)"]


# ── 화면 ─────────────────────────────────────────────────

@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _settle(qapp, mod):
    mod._wait_live_threads(cancel=False)
    for _ in range(3):
        qapp.processEvents()


def test_dialog_closed_loop_copy_csv_and_add_to_diagnosis(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog, QWidget
    from ui.dialogs import scanner_rotation_dialog as mod

    parent = QWidget()
    parent.settings = {'rotation_spec': {'kappa_max': 0.10, 'kappa_warn': 0.07}}
    parent._xy_diagnosis = {'schema': 'pass.xy.diagnosis/1', 'rotation_test': None}
    dlg = mod.ScannerRotationDialog(parent)
    try:
        assert not dlg.btn_copy.isEnabled() and not dlg.btn_add.isEnabled() and dlg.btn_run.isEnabled()
        ga, gb = _gains(0.107)
        dlg.spot_a.text.setPlainText(_log(ga, seed=11))
        dlg.spot_b.text.setPlainText(_log(gb, seed=12))
        dlg.stage_combo.setCurrentText('출하 검사')
        dlg.run_and_wait()
        res = dlg.result
        assert res is not None and res['status'] == 'fail' and res['kappa'] == pytest.approx(0.107, abs=0.003)
        assert dlg.table.rowCount() == 2 and dlg.table.item(0, 2).text() == 'X -120'
        assert '초과' in dlg.chip.text() and '잠정 규격' in dlg.spec_label.text()
        assert dlg.kappa_label.text().startswith('κ = +0.10')
        assert '분석 완료 — Y 스윕' in dlg.status.text()
        assert len(dlg.figure.axes) == 2 and len(dlg.figure.axes[0].lines) == 4

        # 픽셀 크기만 바꾸면 다시 해석하지 않고 바로 다시 계산한다
        g_before = res['locations'][0]['gain']
        dlg.pixel_size.setValue(0.35)
        assert dlg._task is None
        assert dlg.result['locations'][0]['gain'] == pytest.approx(g_before * 0.35 / PS, rel=1e-4)
        dlg.pixel_size.setValue(round(PS, 5))

        # X 스윕(대조)을 분석하면 Y 결과에 붙는다
        dlg.set_axis('X')
        assert dlg.spot_a.coord_label.text() == '스테이지 Y' and dlg.result['axis'] == 'Y'
        xa, xb = _gains(0.01)
        dlg.spot_a.text.setPlainText(_log(xa, axis='X', seed=13))
        dlg.spot_b.text.setPlainText(_log(xb, axis='X', seed=14))
        assert dlg.result is not None and dlg.result['control'] is None      # Y 결과는 남는다
        dlg.run_and_wait()
        assert dlg.result['axis'] == 'Y' and dlg.result['control']['axis'] == 'X'
        assert dlg.table.rowCount() == 4 and dlg.table.item(2, 0).text() == '대조 X 스윕'
        assert dlg.figure.axes[0].get_title().startswith('X 스윕')

        dlg._copy()
        assert QApplication.clipboard().text().startswith('[스캐너 회전 시험]')
        out = tmp_path / 'rot.csv'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: (str(out), '')))
        dlg._save_csv()
        assert out.exists() and '저장' in dlg.status.text()

        dlg.btn_add.click()
        assert parent._rotation_test_result['kappa'] == dlg.result['kappa']
        assert parent._xy_diagnosis['rotation_test'] is parent._rotation_test_result
        assert parent._rotation_test_result is not dlg.result                 # 복사본
        json.dumps(parent._xy_diagnosis, allow_nan=False)
        assert '진단에 추가했습니다' in dlg.status.text()

        # 입력이 바뀌면 그 축 결과는 버린다
        dlg.spot_a.text.setPlainText('')
        assert dlg.result['control'] is None
    finally:
        _settle(qapp, mod)
        dlg.deleteLater()
        parent.deleteLater()


def test_dialog_reports_parse_error_and_file_loading(qapp, tmp_path):
    from ui.dialogs import scanner_rotation_dialog as mod
    dlg = mod.ScannerRotationDialog()
    try:
        dlg.spot_a.text.setPlainText('\n'.join(_log(1.0).splitlines()[:-1]))
        dlg.spot_b.text.setPlainText(_log(1.0))
        dlg.run_and_wait()
        assert dlg.result is None and '명령 15단계인데 측정 줄이 14개' in dlg.status.text()
        assert dlg.btn_run.isEnabled() and not dlg.btn_add.isEnabled()

        p = tmp_path / 'a.csv'
        p.write_text(_csv(1.0), encoding='utf-8')
        dlg.load_spot_file(dlg.spot_a, str(p))
        _settle(qapp, mod)
        assert dlg.spot_a.text.toPlainText().startswith('command_um') and dlg.spot_a.source.text() == str(p)
        dlg.load_spot_file(dlg.spot_b, str(tmp_path / 'none.txt'))
        _settle(qapp, mod)
        assert dlg.spot_b.source.text().startswith('읽기 실패')

        dlg._add_to_diagnosis()                    # 부모 창이 없으면 아무 일도 하지 않는다
        dlg.commands_edit.setText('-70:70')
        dlg.run_and_wait()
        assert '시작:끝:간격' in dlg.status.text()
    finally:
        _settle(qapp, mod)
        dlg.deleteLater()


def test_dialog_xlsx_open_loop_mode(qapp, tmp_path):
    from PySide6.QtWidgets import QWidget
    from ui.dialogs import scanner_rotation_dialog as mod
    parent = QWidget()
    dlg = mod.ScannerRotationDialog(parent)
    try:
        dlg.set_xlsx_path(_make_workbook(tmp_path / 'nl.xlsx'))
        assert dlg._mode() == mod.MODE_XLSX and not dlg.spot_a.text.isEnabled()
        dlg.run_and_wait()
        res = dlg.result
        assert res['loop'] == 'open' and res['status'] == 'na'
        assert '개루프' in dlg.chip.text() and '판정하지 않습니다' in dlg.spec_label.text()
        assert dlg.table.rowCount() == 8 and dlg.table.horizontalHeaderItem(0).text() == '시트'
        assert len(dlg.figure.axes[0].lines) == 8                              # Y1·Y2 × Before·After
        dlg.chk_xlsx_coords.setChecked(True)
        assert dlg.result['kappa'] is not None and dlg.result['status'] == 'na'
        dlg._add_to_diagnosis()
        assert parent._rotation_test_result['loop'] == 'open'
        assert 'rotation_test' in dlg.status.text() or '진단을 만들면' in dlg.status.text()

        dlg.set_mode(mod.MODE_CLOSED)                                        # 모드를 바꾸면 그 모드 결과
        assert dlg.result is None
        dlg.set_mode(mod.MODE_XLSX)
        assert dlg.result['loop'] == 'open'
    finally:
        _settle(qapp, mod)
        dlg.deleteLater()
        parent.deleteLater()


def test_dialog_cancels_stale_run_when_inputs_change(qapp, tmp_path):
    from ui.dialogs import scanner_rotation_dialog as mod
    import main
    assert main.rotation_threads_alive is mod.threads_alive          # main()의 os._exit 판단에 들어간다
    dlg = mod.ScannerRotationDialog()
    try:
        dlg.set_xlsx_path(_make_workbook(tmp_path / 'nl.xlsx'))
        dlg._run()
        task = dlg._task
        dlg.set_xlsx_path(_make_workbook(tmp_path / 'nl2.xlsx'))       # 다른 파일 → 진행 중인 해석은 멈춘다
        assert task.cancelled and dlg._task is None and dlg.result is None
        assert task.wait(10000)
        for _ in range(3):
            qapp.processEvents()
        assert dlg.result is None and not dlg._analysis_tasks and dlg.btn_run.isEnabled()
        assert not mod.threads_alive()
    finally:
        _settle(qapp, mod)
        dlg.deleteLater()


def test_main_window_button_opens_single_dialog(qapp, monkeypatch):
    import main
    from PySide6.QtWidgets import QPushButton
    monkeypatch.setattr(main, 'load_settings', lambda: dict(spec_deviation={}))
    monkeypatch.setattr(main, 'save_settings', lambda _: None)
    win = main.DataAnalyzerApp()
    monkeypatch.setattr(win, '_save_settings', lambda: None)
    try:
        buttons = [b for b in win.findChildren(QPushButton) if b.text() == '스캐너 회전 시험 분석']
        assert len(buttons) == 1
        buttons[0].click()
        dlg = win._scanner_rotation_dialog
        assert dlg.isVisible() and dlg.parent() is win and not dlg.isModal()
        dlg.close()
        buttons[0].click()
        assert win._scanner_rotation_dialog is dlg and dlg.isVisible()
        dlg.close()
    finally:
        win.deleteLater()
        qapp.processEvents()
