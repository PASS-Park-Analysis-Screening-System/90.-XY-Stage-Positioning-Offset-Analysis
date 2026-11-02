from PySide6.QtWidgets import QLabel, QPushButton, QCheckBox
from functools import partial
from core import (compute_deviation_matrix, extract_die_positions, evaluate_deviation_pass,
                  filter_stabilization_die, stabilization_die)
from core.die_analysis import select_display_rows
from core import compute_statistics, filter_by_method
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE


class StepMixin:
    def _build_nav(self):
        while self.nav_layout.count():
            w = self.nav_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.step_buttons.clear()
        self.step_pass_states = {}

        lbl = QLabel("Workflow:")
        lbl.setStyleSheet("font-size: 10pt;")
        self.nav_layout.addWidget(lbl)

        for i, r in enumerate(self.recipes):
            btn = QPushButton(f"Step {r['index']}: {r['short_name']}")
            btn.setProperty("step", True)
            btn.clicked.connect(partial(self._select_step, i))
            btn.setToolTip("색상은 Summary 체크리스트 판정입니다 (안정화 Die 제외, Die 필터와 무관)")
            self.nav_layout.addWidget(btn)
            self.step_buttons.append(btn)
            if i < len(self.recipes) - 1:
                sep = QLabel(" ▶ ")
                sep.setStyleSheet(f"color: {FG2};")
                self.nav_layout.addWidget(sep)
        self.nav_layout.addStretch()


    def _compute_all_step_pass_states(self):
        """scan complete 후 모든 Step Pass/Fail 일괄 계산 후 버튼 색상 즉시 반영."""
        dev_spec = self.settings.get('spec_deviation', {})
        for i, result in enumerate(self.recipe_results):
            # Summary 체크리스트·분석 시트와 같은 21 Die 기준 (안정화 Die 제외)
            data = filter_stabilization_die(result.get('raw_data', []))
            short = result.get('short_name', '')
            ds = dev_spec.get(short, {})
            spec_r = ds.get('spec_range')
            spec_s = ds.get('spec_stddev')
            if not data:
                self.step_pass_states[i] = None
                continue
            dx = compute_deviation_matrix(data, 'X')
            dy = compute_deviation_matrix(data, 'Y')
            sx = compute_statistics(filter_by_method(data, 'X'))
            sy = compute_statistics(filter_by_method(data, 'Y'))
            # PASS/FAIL 판정은 core.evaluate_deviation_pass 단일 출처를 사용 (CardMixin과 동일).
            px = evaluate_deviation_pass(sx, dx, spec_r, spec_s)
            py = evaluate_deviation_pass(sy, dy, spec_r, spec_s)
            if px is None or py is None:
                self.step_pass_states[i] = None
            else:
                self.step_pass_states[i] = px and py
        self._refresh_step_buttons()


    def _select_step(self, idx):
        if idx < 0 or idx >= len(self.recipes):
            return
        self.current_recipe_idx = idx
        for i, btn in enumerate(self.step_buttons):
            is_pass = self.step_pass_states.get(i)  # True/False/None
            # Active step: always accent regardless of pass/fail
            if i == idx:
                btn.setProperty("active_step", True)
                btn.setProperty("step", False)
                btn.setProperty("step_pass", False)
                btn.setProperty("step_fail", False)
            elif is_pass is True:
                btn.setProperty("active_step", False)
                btn.setProperty("step", False)
                btn.setProperty("step_pass", True)
                btn.setProperty("step_fail", False)
            elif is_pass is False:
                btn.setProperty("active_step", False)
                btn.setProperty("step", False)
                btn.setProperty("step_pass", False)
                btn.setProperty("step_fail", True)
            else:  # None = not yet analyzed
                btn.setProperty("active_step", False)
                btn.setProperty("step", True)
                btn.setProperty("step_pass", False)
                btn.setProperty("step_fail", False)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        recipe = self.recipes[idx]
        self.step_title.setText(f"Step {recipe['index']}: {recipe['name']}")
        self.logger.info(f"Step 전환 → {recipe['name']}")

        if idx < len(self.recipe_results) and self.recipe_results[idx].get('raw_data'):
            result = self.recipe_results[idx]
            self.raw_data = result.get('raw_data', [])
            # 실제 측정 데이터에서 Die 좌표 추출
            self._dynamic_die_positions = extract_die_positions(self.raw_data)
            self._display_result(result, recipe)
        else:
            self.statusBar().showMessage(f"데이터 로드 중: {recipe['short_name']}...")

    # ──────────────────────────────────────────────
    # Display Result
    # ──────────────────────────────────────────────

    def _display_result(self, result, recipe):
        raw = result.get('raw_data', [])

        # Die 체크박스 동적 생성 (첫 호출 or Step 전환 시)
        from core import extract_die_number
        from charts.wafer import _color_from_die
        die_nums_in_data = sorted(set(
            extract_die_number(r.get('site_id', ''))
            for r in raw if extract_die_number(r.get('site_id', '')) is not None))

        if (self._die_filter_reset_pending
                or set(die_nums_in_data) != set(self._die_checkboxes.keys())):
            self._die_filter_reset_pending = False

            # 안정화 Die는 해제 상태로 시작한다. 카드·표·차트의 기본값을
            # Summary 체크리스트·분석 시트의 21 Die와 맞추기 위함이다.
            default_off = stabilization_die(raw)

            # 기존 체크박스 제거 (접힌 상태)
            while self._die_cb_flow.count():
                item = self._die_cb_flow.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
            self._die_checkboxes.clear()

            # 기존 체크박스 제거 (펼친 상태 그리드)
            while self._die_grid_layout.count():
                item = self._die_grid_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
            self._die_grid_checkboxes.clear()

            def _make_die_cb(die_idx, hex_color):
                """Die 체크박스 생성 헬퍼 (공통 스타일)."""
                cb = QCheckBox(f"Die {die_idx + 1}")
                cb.setChecked(die_idx != default_off)
                cb.setStyleSheet(f"""
                    QCheckBox {{ color: {hex_color}; font-size: 8pt; font-weight: bold; border: none; }}
                    QCheckBox::indicator {{ width: 12px; height: 12px; }}
                    QCheckBox::indicator:unchecked {{ border: 1px solid #585b70; border-radius: 2px;
                                                     background: {BG2}; }}
                    QCheckBox::indicator:checked {{ border: 1px solid {hex_color}; border-radius: 2px;
                                                   background: {hex_color}; }}
                """)
                return cb

            # 새 체크박스 생성 — 접힌 상태(FlowLayout) + 펼친 상태(GridLayout)
            grid_cols = 2  # 그리드 2열
            for i, d in enumerate(die_nums_in_data):
                color = _color_from_die(d)
                r_c, g_c, b_c = [int(c * 255) for c in color[:3]]
                hex_c = f"#{r_c:02x}{g_c:02x}{b_c:02x}"

                # 접힌 상태 체크박스
                cb_flow = _make_die_cb(d, hex_c)
                cb_flow.stateChanged.connect(self._on_die_filter_changed)
                self._die_cb_flow.addWidget(cb_flow)
                self._die_checkboxes[d] = cb_flow

                # 펼친 상태 그리드 체크박스
                cb_grid = _make_die_cb(d, hex_c)
                cb_grid.stateChanged.connect(self._on_die_filter_changed)
                row, col = divmod(i, grid_cols)
                self._die_grid_layout.addWidget(cb_grid, row, col)
                self._die_grid_checkboxes[d] = cb_grid

            if self._die_filter_expanded:
                self._render_mini_die_map()

        # 필터 적용: 체크 해제된 Die 제외
        excluded = {d for d, cb in self._die_checkboxes.items() if not cb.isChecked()}
        data = select_display_rows(raw, excluded)

        # 기본 기준과 다르면 카드·표·차트가 Summary·Step 버튼·내보내기와 달라진다고 알린다
        default_off = stabilization_die(raw)
        self._die_filter_is_custom = excluded != ({default_off} if default_off is not None else set())
        excluded_names = ', '.join(f'Die {d + 1}' for d in sorted(excluded))
        if not data:
            self.filter_info_label.setText(
                f"⚠ 분석할 수 있는 행이 없습니다  |  Analyzing 0 of {len(raw)}")
            self.filter_info_label.setToolTip(
                "Site ID를 읽지 못했거나 모든 Die를 해제했습니다.")
        elif self._die_filter_is_custom:
            self.filter_info_label.setText(
                f"⚠ Custom: Analyzing {len(data)} of {len(raw)}  |  Summary와 기준 다름")
            self.filter_info_label.setToolTip(
                f"제외: {excluded_names or '없음'}\n"
                "카드·표·그래프만 적용됩니다. "
                "Summary·Step 버튼·Lot Trend·내보내기는 분석 기준으로 고정됩니다.")
        else:
            self.filter_info_label.setText(
                f"✅ Analysis basis: {excluded_names or 'No die'} excluded  |  "
                f"Analyzing {len(data)} of {len(raw)}")
            self.filter_info_label.setToolTip("")

        self._update_cards(data, recipe)
        self._update_die_avg_tables()    # self._dev_x/y 기반 → Die 필터 적용됨
        self._update_deviation_tables()  # self._dev_x/y 기반 → Die 필터 적용됨
        self._update_raw_table()         # self.raw_data 기반 → Die 필터 미적용 (전체 표시)
        self._update_charts(data, result, recipe)
        self._render_die_position()
        stats = result.get('statistics', {})
        self.statusBar().showMessage(
            f"Step {recipe['index']}: {recipe['short_name']} — "
            f"{len(data)} ({len(excluded)} Dies excluded) | "
            f"Outliers: {result.get('outlier_count', 0)}  💡 Double-click row → TIFF")


    def _refresh_step_buttons(self):
        """Step 버튼 색상만 갱신 (재귀 없이). _update_cards에서 호출."""
        active = self.current_recipe_idx
        for i, btn in enumerate(self.step_buttons):
            is_pass = self.step_pass_states.get(i)
            has_data = i in self.step_pass_states  # 한번이라도 계산됐으면 True

            # 모든 property 초기화
            for prop in ('active_step', 'step', 'step_pass', 'step_fail',
                         'step_active_pass', 'step_active_fail'):
                btn.setProperty(prop, False)

            if i == active:
                # 활성 Step: Pass/Fail 색상 + 흰 테두리
                if not has_data:
                    btn.setProperty('active_step', True)   # 파란색 + 흰 테두리
                elif is_pass is True:
                    btn.setProperty('step_active_pass', True)  # 초록 + 흰 테두리
                elif is_pass is False:
                    btn.setProperty('step_active_fail', True)  # 빨간 + 흰 테두리
                else:
                    btn.setProperty('active_step', True)   # 파란색 + 흰 테두리
            else:
                # 비활성 Step: Pass/Fail만 표시
                if not has_data:
                    btn.setProperty('step', True)      # 파란색
                elif is_pass is True:
                    btn.setProperty('step_pass', True) # 초록색
                elif is_pass is False:
                    btn.setProperty('step_fail', True) # 빨간색
                else:
                    btn.setProperty('step', True)      # 파란색

            btn.style().unpolish(btn)
            btn.style().polish(btn)


