from ui.widgets.copyable_table import CopyableTable
from PySide6.QtWidgets import QTableWidgetItem, QHeaderView
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from ui.color_helpers import _heatmap_diverging, _heatmap_single, _contrast_fg, verdict_color
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE
from core.die_analysis import filter_stabilization_die
from core.checklist import compute_axis_metrics, result_text
from core.statistics import (classify_row, axis_reference_means,
                             spec_anomaly_enabled, ANOMALY_FAILED,
                             ANOMALY_OUTLIER, ANOMALY_SPEC)


# ── Summary 테이블 행 레이아웃 — ui_builder_mixin과 공유하는 단일 출처 ──
#  Row 0    : Checklist 그룹 헤더
#  Row 1-4  : Checklist (X/Y Dev Range, X/Y Dev StdDev)
#  Row 5-6  : X / Y Result
#  Row 7    : Statistics 그룹 헤더
#  Row 8-15 : 통계 상세 (R, N, Mean, StdDev, Min, Max, CV%, Outliers)
SUMMARY_ROW_LABELS = [
    'Checklist',                                       # 0
    'X Dev Range (µm)', 'Y Dev Range (µm)',            # 1, 2
    'X Dev StdDev (µm)', 'Y Dev StdDev (µm)',          # 3, 4
    'X Result', 'Y Result',                            # 5, 6
    'Statistics',                                      # 7
    'R', 'N', 'Mean', 'StdDev', 'Min', 'Max', 'CV%', 'Outliers',  # 8-15
]
SUMMARY_GROUP_HEADER_ROWS = (0, 7)
SUMMARY_CHK_ROWS = (1, 2, 3, 4)
ROW_X_RANGE, ROW_Y_RANGE, ROW_X_STD, ROW_Y_STD = 1, 2, 3, 4
ROW_X_RESULT, ROW_Y_RESULT = 5, 6
ROW_R, ROW_N, ROW_MEAN, ROW_STDDEV, ROW_MIN, ROW_MAX, ROW_CV, ROW_OUT = range(8, 16)


class TableMixin:
    def _update_summary_table(self, comparison, recipe_results=None):
        t = self.sum_table
        recipe_results = recipe_results or []
        dev_spec = self.settings.get('spec_deviation', {})
        n = len(comparison)

        # 열 = Recipe 이름
        t.setColumnCount(n)
        t.setHorizontalHeaderLabels([c.get('recipe', f'R{i+1}')
                                     for i, c in enumerate(comparison)])

        # ── 그룹 헤더 행: 셀은 비운 채 빈 아이템으로 채움 ──
        # (텍스트는 수직 헤더 레이블로만 표시)
        for _hdr_row in SUMMARY_GROUP_HEADER_ROWS:
            for c in range(n):
                it = QTableWidgetItem('')
                it.setFlags(Qt.ItemIsEnabled)
                t.setItem(_hdr_row, c, it)


        TOOLTIP_CHK = '체크리스트 기입 항목입니다'

        for col, c in enumerate(comparison):
            recipe_name = c.get('recipe', '')
            ds = dev_spec.get(recipe_name, {})
            spec_r = ds.get('spec_range')
            spec_s = ds.get('spec_stddev')

            result = recipe_results[col] if col < len(recipe_results) else {}
            raw    = result.get('raw_data', []) if result else []
            # 안정화 Die(#1, 첫 측정 site) 제외 — 체크리스트는 Die 필터와 무관하게
            # 항상 21 Die 기준으로 고정 (guide_dialog 5.0 참조).
            data   = filter_stabilization_die(raw)

            x_range = x_std = y_range = y_std = None
            px = py = None
            ref_x = ref_y = False

            if data:
                # 값·판정은 체크리스트 단일 출처(core.compute_axis_metrics)를 쓴다.
                # 측정이 없는 축은 0.000이 아니라 '—'(None), 판정도 '—'다.
                # 1 Repeat은 판정하되 참고(reference)로 표시한다
                mx = compute_axis_metrics(data, 'X', recipe_name,
                                          self.settings.get('spec_limits', {}), dev_spec)
                my = compute_axis_metrics(data, 'Y', recipe_name,
                                          self.settings.get('spec_limits', {}), dev_spec)
                x_range, x_std, px, ref_x = mx['range'], mx['stddev'], mx['result'], mx['reference']
                y_range, y_std, py, ref_y = my['range'], my['stddev'], my['result'], my['reference']

            def _chk_item(val, spec):
                """체크리스트 수치 셀 — 값만, Spec 초과면 빨간 배경, 볼드."""
                if val is None:
                    it = QTableWidgetItem('—')
                    it.setBackground(QColor(BG2)); it.setForeground(QColor(FG2))
                else:
                    it = QTableWidgetItem(f'{val:.3f}')
                    if spec is not None and val > spec:
                        it.setBackground(QColor(RED)); it.setForeground(QColor(BG))
                    else:
                        it.setBackground(QColor(BG2))
                        it.setForeground(QColor(GREEN if (spec and val <= spec) else FG))
                    from PySide6.QtGui import QFont as _QFont
                    f = it.font(); f.setBold(True); it.setFont(f)
                it.setTextAlignment(Qt.AlignCenter)
                it.setToolTip(TOOLTIP_CHK)
                return it

            def _flag_item(flag, reference=False):
                if flag is None:
                    it = QTableWidgetItem('—')
                    it.setBackground(QColor(BG3)); it.setForeground(QColor(FG2))
                else:
                    it = QTableWidgetItem(result_text(flag, reference))
                    it.setBackground(QColor(verdict_color(flag, reference)))
                    it.setForeground(QColor(BG))
                    if reference:
                        it.setToolTip('Repeat이 1개라 Die 간 편차로만 판정한 참고 판정입니다 '
                                      '(반복성 미확인, 정식 판정은 2 Repeat 이상)')
                it.setTextAlignment(Qt.AlignCenter)
                return it

            def _stat_item(text):
                it = QTableWidgetItem(str(text))
                it.setTextAlignment(Qt.AlignCenter)
                it.setBackground(QColor(BG2)); it.setForeground(QColor(FG))
                return it

            # Checklist (그룹 헤더 다음). 판정하지 않은 축의 값은 Spec 색을 입히지 않는다
            spec_defined = spec_r is not None and spec_s is not None
            not_judged_x = spec_defined and px is None
            not_judged_y = spec_defined and py is None
            t.setItem(ROW_X_RANGE, col, _chk_item(x_range, None if not_judged_x else spec_r))
            t.setItem(ROW_Y_RANGE, col, _chk_item(y_range, None if not_judged_y else spec_r))
            t.setItem(ROW_X_STD,   col, _chk_item(x_std,   None if not_judged_x else spec_s))
            t.setItem(ROW_Y_STD,   col, _chk_item(y_std,   None if not_judged_y else spec_s))
            # Result
            t.setItem(ROW_X_RESULT, col, _flag_item(px, ref_x))
            t.setItem(ROW_Y_RESULT, col, _flag_item(py, ref_y))
            # 통계 상세 (그룹 헤더 다음)
            t.setItem(ROW_R,      col, _stat_item(c.get('round', '—')))
            t.setItem(ROW_N,      col, _stat_item(c.get('data_count', '—')))
            def _stat_text(key):
                # 0.0으로 나가면 측정값이 있는 정상 결과처럼 보인다
                return f"{c.get(key, 0):.1f}" if c.get('data_count') else '—'
            t.setItem(ROW_MEAN,   col, _stat_item(_stat_text('mean')))
            t.setItem(ROW_STDDEV, col, _stat_item(_stat_text('stdev')))
            t.setItem(ROW_MIN,    col, _stat_item(_stat_text('min')))
            t.setItem(ROW_MAX,    col, _stat_item(_stat_text('max')))
            t.setItem(ROW_CV,     col, _stat_item(_stat_text('cv_percent')))
            t.setItem(ROW_OUT,    col, _stat_item(c.get('outliers', 0)))


    def _fill_die_avg_heatmap(self, table: CopyableTable, die_stats: list,
                              single_repeat: bool = False):
        """single_repeat: Repeat(Lot)이 1개라 Die 안 StdDev·Range는 계산하지 않는다 ('—')."""
        table.clear()
        headers = ['Die', 'Avg (µm)', 'StdDev', 'Range']
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        if not die_stats:
            table.setRowCount(1)
            table.setItem(0, 0, QTableWidgetItem("No data"))
            return

        table.setRowCount(len(die_stats))
        avgs = [ds['avg'] for ds in die_stats]
        stds = [ds['stddev'] for ds in die_stats]
        rngs = [ds['range'] for ds in die_stats]
        avg_max = max(abs(v) for v in avgs) if avgs else 1.0
        std_max = max(stds) if stds else 1.0
        rng_max = max(rngs) if rngs else 1.0

        for i, ds in enumerate(die_stats):
            # Die label
            item_die = QTableWidgetItem(ds['die'])
            item_die.setBackground(QColor(BG3))
            item_die.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 0, item_die)
            # Avg — diverging
            bg = _heatmap_diverging(ds['avg'] / avg_max if avg_max > 0 else 0)
            item = QTableWidgetItem(f"{ds['avg']:.3f}")
            item.setBackground(bg); item.setForeground(_contrast_fg(bg))
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 1, item)
            if single_repeat:
                for col in (2, 3):
                    item = QTableWidgetItem('—')
                    item.setBackground(QColor(BG2)); item.setForeground(QColor(FG2))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setToolTip('Lot 1개라 계산 안 함 (Die마다 값 1개)')
                    table.setItem(i, col, item)
                continue
            # StdDev — single
            bg = _heatmap_single(ds['stddev'] / std_max if std_max > 0 else 0)
            item = QTableWidgetItem(f"{ds['stddev']:.3f}")
            item.setBackground(bg); item.setForeground(_contrast_fg(bg))
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 2, item)
            # Range — single
            bg = _heatmap_single(ds['range'] / rng_max if rng_max > 0 else 0)
            item = QTableWidgetItem(f"{ds['range']:.3f}")
            item.setBackground(bg); item.setForeground(_contrast_fg(bg))
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 3, item)


    def _update_die_avg_tables(self):
        for table, dev in ((self.die_x_table, self._dev_x), (self.die_y_table, self._dev_y)):
            self._fill_die_avg_heatmap(table, dev.get('die_stats', []),
                                       single_repeat=len(dev.get('repeat_labels') or []) == 1)


    def _fill_deviation_table(self, table: CopyableTable, dev_result):
        table.clear()
        die_labels = dev_result.get('die_labels', [])
        repeat_labels = dev_result.get('repeat_labels', [])
        matrix = dev_result.get('matrix', {})
        if not die_labels or not repeat_labels:
            table.setRowCount(1); table.setColumnCount(1)
            table.setItem(0, 0, QTableWidgetItem("No data"))
            return

        table.setColumnCount(len(die_labels) + 1)
        table.setHorizontalHeaderLabels([''] + die_labels)
        table.setRowCount(len(repeat_labels))

        all_vals = [matrix.get(rl, {}).get(dl) for rl in repeat_labels
                    for dl in die_labels if matrix.get(rl, {}).get(dl) is not None]
        v_max = max(abs(v) for v in all_vals) if all_vals else 1.0

        for i, rl in enumerate(repeat_labels):
            item_rl = QTableWidgetItem(rl)
            item_rl.setBackground(QColor(BG3))
            item_rl.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 0, item_rl)
            for j, dl in enumerate(die_labels):
                v = matrix.get(rl, {}).get(dl)
                if v is None:
                    item = QTableWidgetItem("—")
                    item.setBackground(QColor(BG2))
                else:
                    item = QTableWidgetItem(f"{v:.3f}")
                    bg = _heatmap_diverging(v / v_max if v_max > 0 else 0)
                    item.setBackground(bg)
                    item.setForeground(_contrast_fg(bg))
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(i, j + 1, item)


    def _update_deviation_tables(self):
        self._fill_deviation_table(self.dev_x_table, self._dev_x)
        self._fill_deviation_table(self.dev_y_table, self._dev_y)


    def _current_recipe_short_name(self) -> str:
        idx = getattr(self, 'current_recipe_idx', -1)
        results = getattr(self, 'recipe_results', []) or []
        if 0 <= idx < len(results):
            r = results[idx]
            return r.get('short_name') or r.get('recipe', '')
        return ''

    def _update_raw_table(self):
        """Raw Data 테이블 갱신 + 이상값 필터 적용.

        필터는 **표시만** 걸러낸다. self.raw_data 자체는 손대지 않으므로
        통계·차트·Spec 판정은 필터 상태와 무관하게 동일하다.
        """
        t = self.raw_table
        t.setRowCount(0)

        checks = getattr(self, 'raw_filter_checks', {})
        wanted = {ANOMALY_FAILED: checks.get('failed'),
                  ANOMALY_OUTLIER: checks.get('outlier'),
                  ANOMALY_SPEC: checks.get('spec')}
        wanted = {k for k, cb in wanted.items() if cb is not None and cb.isChecked()}

        spec_limits = self.settings.get('spec_limits', {})
        recipe = self._current_recipe_short_name()
        # Spec 판정은 PASS/FAIL(Dev Range/StdDev)과 같은 편차 기준을 쓴다
        analysis = filter_stabilization_die(self.raw_data)
        axis_means = axis_reference_means(analysis)

        shown = 0
        for r in self.raw_data:
            kinds = classify_row(r, spec_limits, recipe, axis_means=axis_means)

            # 아무것도 체크하지 않으면 전체 표시, 여러 개면 OR
            if wanted and not (wanted & set(kinds)):
                continue

            row = t.rowCount()
            t.insertRow(row)
            shown += 1

            failed = ANOMALY_FAILED in kinds
            io = r.get('is_outlier', False)
            out_of_spec = ANOMALY_SPEC in kinds
            value = r.get('value', 0)
            vals = [r.get('lot_name', ''), r.get('site_id', ''),
                    r.get('method', ''),
                    'nan' if failed and not isinstance(value, str) and value != value
                    else f"{value:.3f}" if isinstance(value, (int, float)) else str(value),
                    '✅' if r.get('valid', True) else '❌',
                    '⚠️' if io else '']
            if spec_anomaly_enabled():
                vals.append('❌' if out_of_spec else '')
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignCenter)
                if io or failed or out_of_spec:
                    item.setForeground(QColor(RED))
                t.setItem(row, col, item)

        label = getattr(self, 'raw_filter_label', None)
        if label is not None:
            total = len(self.raw_data)
            basis = f"분석 대상 {len(analysis)}행, 제외 행 포함 표시"
            label.setText(f"표시 {shown} / 전체 {total} ({basis})" if wanted
                          else f"전체 {total}행 ({basis})")

    # ──────────────────────────────────────────────
    # Charts — Hybrid: matplotlib + pyqtgraph
    # ──────────────────────────────────────────────

