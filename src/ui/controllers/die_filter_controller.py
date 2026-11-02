from PySide6.QtCore import QSize
from PySide6.QtWidgets import QStyle
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from ui.widgets.chart_widget import sync_canvas_dpi
import charts as viz
from core import stabilization_die
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE


class DieFilterMixin:
    def _on_die_filter_changed(self, state):
        """개별 Die 체크박스 변경 → 양쪽 동기화 + 재분석."""
        if self._die_filter_updating:
            return
        self._die_filter_updating = True

        # 변경된 체크박스가 어느 쪽인지 판별하여 반대쪽 동기화
        sender = self.sender() if hasattr(self, 'sender') else None
        for d, cb_flow in self._die_checkboxes.items():
            cb_grid = self._die_grid_checkboxes.get(d)
            if cb_grid is None:
                continue
            if sender is cb_flow:
                cb_grid.setChecked(cb_flow.isChecked())
            elif sender is cb_grid:
                cb_flow.setChecked(cb_grid.isChecked())

        self._die_filter_updating = False

        # 미니 맵 갱신 (펼친 상태인 경우)
        if self._die_filter_expanded:
            self._render_mini_die_map()

        if self.recipe_results and self.current_recipe_idx < len(self.recipe_results):
            result = self.recipe_results[self.current_recipe_idx]
            recipe = self.recipes[self.current_recipe_idx]
            self._display_result(result, recipe)


    def _die_filter_select_all(self):
        """전체 Die 선택."""
        self._die_filter_updating = True
        for cb in self._die_checkboxes.values():
            cb.setChecked(True)
        for cb in self._die_grid_checkboxes.values():
            cb.setChecked(True)
        self._die_filter_updating = False
        if self._die_filter_expanded:
            self._render_mini_die_map()
        self._on_die_filter_changed(None)


    def _die_filter_exclude_stabilization(self):
        """안정화 Die 제외 — 첫 번째 측정 Die 체크 해제."""
        if not self.recipe_results or self.current_recipe_idx >= len(self.recipe_results):
            return
        raw = self.recipe_results[self.current_recipe_idx].get('raw_data', [])
        if not raw:
            return
        first_die = stabilization_die(raw)
        if first_die is None:
            self.logger.warn("제외할 안정화 Die(Site 0001)가 없습니다.")
            return

        # 먼저 전체 선택 후, 안정화 Die만 해제 (양쪽 모두)
        self._die_filter_updating = True
        for cb in self._die_checkboxes.values():
            cb.setChecked(True)
        for cb in self._die_grid_checkboxes.values():
            cb.setChecked(True)
        if first_die in self._die_checkboxes:
            self._die_checkboxes[first_die].setChecked(False)
        if first_die in self._die_grid_checkboxes:
            self._die_grid_checkboxes[first_die].setChecked(False)
        self._die_filter_updating = False
        if self._die_filter_expanded:
            self._render_mini_die_map()
        self._on_die_filter_changed(None)

    # ──────────────────────────────────────────────
    # Die 필터 확장 토글
    # ──────────────────────────────────────────────

    def _toggle_die_filter_expand(self):
        """Die 필터 접힘/펼침 토글."""
        self._die_filter_expanded = not self._die_filter_expanded
        if self._die_filter_expanded:
            self._die_expand_btn.setIcon(self.style().standardIcon(
                QStyle.StandardPixmap.SP_ArrowUp))
            self._die_expand_btn.setToolTip("Die 필터 접기")
            self._die_cb_container.setVisible(False)
            self._die_expanded_panel.setVisible(True)
            self._render_mini_die_map()
        else:
            self._die_expand_btn.setIcon(self.style().standardIcon(
                QStyle.StandardPixmap.SP_ArrowDown))
            self._die_expand_btn.setToolTip("Die 필터 확장 — 포지션 맵과 함께 보기")
            self._die_cb_container.setVisible(True)
            self._die_expanded_panel.setVisible(False)


    def _render_mini_die_map(self):
        """미니 Die Position Map 렌더링 (확장 패널 좌측)."""
        import matplotlib.pyplot as plt

        # 체크 해제된 Die 수집
        excluded = {d for d, cb in self._die_checkboxes.items() if not cb.isChecked()}
        dyn = getattr(self, '_dynamic_die_positions', None)

        fig, scatter_map = viz.plot_die_position_map_mini(
            dynamic_positions=dyn,
            wafer_radius_um=self._get_wafer_radius_um(),
            excluded_dies=excluded)
        self._mini_die_scatter_map = scatter_map

        # 기존 캔버스 교체
        if self._mini_map_canvas is not None:
            self._mini_map_layout.removeWidget(self._mini_map_canvas)
            self._mini_map_canvas.setParent(None)
            old_fig = self._mini_map_canvas.figure
            self._mini_map_canvas.deleteLater()
            plt.close(old_fig)

        canvas = FigureCanvasQTAgg(fig)
        canvas.setStyleSheet("border: none;")
        self._mini_map_layout.addWidget(canvas)
        self._mini_map_canvas = canvas
        # 캔버스를 새로 만들 때마다 화면 DPI에 동기화 (pattern d)
        sync_canvas_dpi(canvas)

        # pick_event 연결 — Die 원 클릭 시 체크박스 토글
        canvas.mpl_connect('pick_event', self._on_mini_map_pick)


    def _on_mini_map_pick(self, event):
        """미니 맵에서 Die 원 클릭 → 체크박스 토글."""
        artist = event.artist
        # scatter_map에서 어떤 Die인지 찾기
        for die_idx, sc in self._mini_die_scatter_map.items():
            if sc is artist:
                # 해당 Die 체크박스 토글 (양쪽)
                cb_flow = self._die_checkboxes.get(die_idx)
                cb_grid = self._die_grid_checkboxes.get(die_idx)
                if cb_flow:
                    new_state = not cb_flow.isChecked()
                    self._die_filter_updating = True
                    cb_flow.setChecked(new_state)
                    if cb_grid:
                        cb_grid.setChecked(new_state)
                    self._die_filter_updating = False
                    # 미니 맵 갱신 + 재분석
                    self._render_mini_die_map()
                    if self.recipe_results and self.current_recipe_idx < len(self.recipe_results):
                        result = self.recipe_results[self.current_recipe_idx]
                        recipe = self.recipes[self.current_recipe_idx]
                        self._display_result(result, recipe)
                break


    # ──────────────────────────────────────────────
    # Lot Trend Filter (Die 필터와 독립)
    # ──────────────────────────────────────────────

