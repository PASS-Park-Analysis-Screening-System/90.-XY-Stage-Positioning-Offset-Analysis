"""
run_compare_controller.py — 회차(트러블슈팅 반복) 선택과 회차 비교 화면

한 Workflow 폴더 아래 1..14 같은 회차 폴더가 있으면 회차마다 따로 분석한다.
Step 상세는 회차 콤보에서 고른 회차(기본: 마지막 회차, 1st/2nd 구조는 1st)를 보여 주고,
'회차 비교' 탭은 회차별 지표를 표·차트로 나란히 보여 준다.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem

import charts as viz
from core.checklist import result_text
from core.recipe_scanner import compare_recipes, run_view
from ui.theme import BG, GREEN, RED, ORANGE
from ui.controllers.step_controller import wait_cursor


def _num(value, fmt):
    if value is None:
        return '—'
    if value != value:  # NaN
        return 'N/A'
    return format(value, fmt)


class RunCompareMixin:
    RUN_COMPARE_HEADERS = ['회차', 'Lot 수', 'X Range', 'X StdDev', 'X Avg',
                           'Y Range', 'Y StdDev', 'Y Avg', 'Cpk X/Y', '판정']

    # ── 회차 콤보 ─────────────────────────────────────────

    def _current_run_results(self):
        idx = self.current_recipe_idx
        if 0 <= idx < len(self.recipe_results):
            return self.recipe_results[idx].get('run_results') or []
        return []

    def _sync_run_combo(self):
        """현재 Step의 회차 목록을 콤보에 채운다. 회차가 하나뿐이면 숨긴다."""
        runs = self._current_run_results()
        combo = self.run_combo
        combo.blockSignals(True)
        if len(runs) >= 2:
            result = self.recipe_results[self.current_recipe_idx]
            default = result.get('default_run')
            names = [str(r.get('round', '')) for r in runs]
            items = [f"{n} (기본)" if n == default else n for n in names]
            # 목록이 같으면(같은 Step에서 회차만 바꾼 경우) 다시 채우지 않고 위치만 맞춘다
            if [combo.itemText(i) for i in range(combo.count())] != items:
                combo.clear()
                combo.addItems(items)
            current = str(result.get('round', ''))
            combo.setCurrentIndex(names.index(current) if current in names else len(names) - 1)
        else:
            combo.clear()
        combo.blockSignals(False)
        visible = len(runs) >= 2
        combo.setVisible(visible)
        self.run_combo_label.setVisible(visible)

    def _on_run_combo_changed(self, run_index):
        self._select_run(run_index)

    def _select_run(self, run_index):
        """현재 Step의 회차를 바꾼다. 이미 읽은 회차 결과를 쓰므로 디스크를 다시 읽지 않는다."""
        idx = self.current_recipe_idx
        runs = self._current_run_results()
        if not (0 <= run_index < len(runs)):
            return
        if self.recipe_results[idx].get('round') == runs[run_index].get('round'):
            self._highlight_run_row(run_index)
            return
        with wait_cursor(self, f"회차 전환 중… {runs[run_index].get('round', '')}"):
            self._switch_run(idx, runs, run_index)

    def _switch_run(self, idx, runs, run_index):
        self.recipe_results[idx] = run_view(self.recipe_results[idx], run_index)
        self.logger.info(f"회차 전환 → {self.recipes[idx]['short_name']} "
                         f"회차 {runs[run_index].get('round', '')}")
        # Step 버튼 색, Summary 표, 카드·차트를 이 회차로 다시 그린다
        self._compute_all_step_pass_states()
        self._update_summary_table(compare_recipes(self.recipe_results), self.recipe_results)
        self._render_recipe_comparison_charts(announce=False)
        self._select_step(idx)

    # ── 회차 비교 탭 ─────────────────────────────────────

    def _update_run_compare(self):
        """현재 Step의 회차 비교 표와 차트를 그린다 (Step이 바뀌었을 때만 차트를 다시 그린다)."""
        idx = self.current_recipe_idx
        result = self.recipe_results[idx] if 0 <= idx < len(self.recipe_results) else {}
        runs = result.get('runs') or []
        name = result.get('short_name') or result.get('recipe', '')
        table, charts_tabs = self.run_compare_table, self.run_compare_charts
        if len(runs) < 2:
            self.run_compare_label.setText(
                f"{name + ': ' if name else ''}회차가 1개라 비교할 수 없습니다")
            table.setRowCount(0)
            table.hide()
            charts_tabs.hide()
            self._run_compare_key = None
            self._run_charts_pending = None
            return

        self.run_compare_label.setText(
            f"{name}: 회차 {len(runs)}개 비교 (분석 기준: 안정화 Die 제외). "
            f"행을 클릭하면 Step 상세의 회차가 바뀝니다.")
        table.show()
        charts_tabs.show()
        table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            mark = ' ⚠' if run['warnings'] else ''
            x, y = run['x'], run['y']
            cells = [f"{run['run']}{mark}", str(run['lot_count']),
                     _num(x['range'], '.3f'), _num(x['stddev'], '.3f'), _num(x['avg'], '.1f'),
                     _num(y['range'], '.3f'), _num(y['stddev'], '.3f'), _num(y['avg'], '.1f'),
                     f"{_num(x['cpk'], '.2f')} / {_num(y['cpk'], '.2f')}",
                     result_text(run['result'])]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if col == 0 and run['warnings']:
                    item.setToolTip('\n'.join(run['warnings'][:8]))
                    item.setForeground(QBrush(QColor(ORANGE)))
                if col == len(cells) - 1 and run['result'] is not None:
                    item.setBackground(QBrush(QColor(GREEN if run['result'] else RED)))
                    item.setForeground(QBrush(QColor(BG)))
                table.setItem(row, col, item)

        key = (idx, id(result.get('run_results')))
        if getattr(self, '_run_compare_key', None) != key:
            self._run_compare_key = key
            spec = self.settings.get('spec_deviation', {}).get(result.get('short_name', ''), {})
            run_results = result.get('run_results') or []

            def draw():
                try:
                    self.chart_widgets['Run Metrics'].set_figure(viz.plot_run_comparison_metrics(
                        runs, spec.get('spec_range'), spec.get('spec_stddev'), f'— {name}'))
                    self.chart_widgets['Run Boxplot'].set_figure(viz.plot_run_comparison_boxplot(
                        run_results, f'— {name}'))
                except Exception as e:
                    self.logger.error(f"회차 비교 차트 오류: {type(e).__name__}: {e}")
            # '회차 비교' 탭이 보일 때 그린다 (가려져 있으면 처음 보일 때)
            self._run_charts_pending = draw
            self._flush_hidden_charts()
        names = [str(r['run']) for r in runs]
        current = str(result.get('round', ''))
        self._highlight_run_row(names.index(current) if current in names else -1)

    def _highlight_run_row(self, row):
        table = self.run_compare_table
        table.blockSignals(True)
        if 0 <= row < table.rowCount():
            table.selectRow(row)
        else:
            table.clearSelection()
        table.blockSignals(False)

    def _on_run_row_clicked(self, row, _col=0):
        if 0 <= row < self.run_combo.count():
            self.run_combo.setCurrentIndex(row)    # 콤보 변경이 _select_run을 부른다
