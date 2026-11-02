from PySide6.QtWidgets import QCheckBox, QInputDialog, QMessageBox
from PySide6.QtCore import Qt
import charts as viz
import charts as viz_pg
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE


class LotFilterMixin:
    def _update_lot_trend(self, trend_x: list, trend_y: list,
                          short_name: str = '', spec: dict = None):
        """Lot 트렌드 차트 갱신 + Lot 체크박스 동적 생성."""
        self._trend_data_x = trend_x
        self._trend_data_y = trend_y
        self._trend_short_name = short_name
        self._trend_spec = spec

        # Lot 체크박스 동적 생성. 한 축만 있는 Lot도 빠지지 않게 X, Y를 합친다.
        merged = {t.get('lot_name', f'Lot{i}'): t.get('lot_index', 0)
                  for i, t in enumerate(trend_y)}
        merged.update({t.get('lot_name', f'Lot{i}'): t.get('lot_index', 0)
                       for i, t in enumerate(trend_x)})
        lot_names = sorted(merged, key=merged.get)

        if set(lot_names) != set(self._lot_checkboxes.keys()):
            self._lot_filter_updating = True
            while self._lot_cb_flow.count():
                item = self._lot_cb_flow.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
            self._lot_checkboxes.clear()

            for name in lot_names:
                cb = QCheckBox(name)
                cb.setChecked(True)
                cb.setStyleSheet(f"""
                    QCheckBox {{ color: {FG}; font-size: 8pt; border: none; }}
                    QCheckBox::indicator {{ width: 12px; height: 12px; }}
                    QCheckBox::indicator:unchecked {{ border: 1px solid #585b70;
                        border-radius: 2px; background: {BG2}; }}
                    QCheckBox::indicator:checked {{ border: 1px solid {ACCENT};
                        border-radius: 2px; background: {ACCENT}; }}
                """)
                cb.stateChanged.connect(self._on_lot_filter_changed)
                self._lot_cb_flow.addWidget(cb)
                self._lot_checkboxes[name] = cb
            self._lot_filter_updating = False

        self._lot_filter_info.setText(f"✅ Total Lot ({len(lot_names)})")
        self._render_lot_trend_chart()


    def _render_lot_trend_chart(self):
        """현재 체크 상태에 따라 Dual-Panel Lot 트렌드 차트 렌더링."""
        checked = {n for n, cb in self._lot_checkboxes.items() if cb.isChecked()}

        # 복사본에 번호를 매긴다. 원본은 Recipe 비교 차트와 공유한다.
        fx = [dict(t) for t in self._trend_data_x if t.get('lot_name', '') in checked]
        fy = [dict(t) for t in self._trend_data_y if t.get('lot_name', '') in checked]

        # re-index for continuous display
        for i, t in enumerate(fx):
            t['lot_index'] = i
        for i, t in enumerate(fy):
            t['lot_index'] = i

        short = getattr(self, '_trend_short_name', '')
        title = f'{short} Lot Trend' if short else 'Lot Trend'
        spec = getattr(self, '_trend_spec', None)
        self._lot_trend_chart.set_widget(
            viz_pg.create_dual_trend_widget(fx, fy, spec=spec, title=title))

        # 필터 정보 라벨
        total = len(self._lot_checkboxes)
        shown = len(checked)
        excluded = total - shown
        if excluded > 0:
            self._lot_filter_info.setText(
                f"⚠ {excluded} Lots excluded → Showing {shown} (Total {total})")
        else:
            self._lot_filter_info.setText(f"✅ Total Lot ({total})")


    def _on_lot_filter_changed(self, state=None):
        """개별 Lot 체크박스 변경 → 트렌드 재렌더링."""
        if self._lot_filter_updating:
            return
        self._render_lot_trend_chart()


    def _lot_filter_select_all(self):
        """전체 Lot 선택 / 해제 토글."""
        all_checked = all(cb.isChecked() for cb in self._lot_checkboxes.values())
        self._lot_filter_updating = True
        for cb in self._lot_checkboxes.values():
            cb.setChecked(not all_checked)
        self._lot_filter_updating = False
        if not all_checked:
            # 전체 선택 시 — 버튼 텍스트는 유지
            pass
        self._render_lot_trend_chart()


    def _lot_filter_range(self):
        """범위 지정 다이얼로그 — Lot 인덱스 범위로 체크박스 설정."""
        text, ok = QInputDialog.getText(
            self, "Lot Range",
            "Enter Lot range to display:\n\n"
            "  1-5     → First 5 Lots\n"
            "  -3      → Last 3 Lots\n"
            "  3-7     → Lot 3 ~ Lot 7\n",
            text="1-5")
        if not ok or not text.strip():
            return

        lot_names = list(self._lot_checkboxes.keys())
        n = len(lot_names)
        if n == 0:
            return

        text = text.strip()
        try:
            if text.startswith('-'):
                # 마지막 N개
                count = int(text[1:])
                start, end = max(0, n - count), n
            elif '-' in text:
                parts = text.split('-')
                start = max(0, int(parts[0]) - 1)
                end = min(n, int(parts[1]))
            else:
                # 단일 숫자 → 처음 N개
                count = int(text)
                start, end = 0, min(n, count)
        except ValueError:
            QMessageBox.warning(self, "입력 오류",
                                "올바른 형식으로 입력하세요.\n예: 1-5, -3, 3-7")
            return

        self._lot_filter_updating = True
        for i, (name, cb) in enumerate(self._lot_checkboxes.items()):
            cb.setChecked(start <= i < end)
        self._lot_filter_updating = False
        self._render_lot_trend_chart()



