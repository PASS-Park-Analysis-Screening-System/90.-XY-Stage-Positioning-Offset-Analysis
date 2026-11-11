from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT

import math
from functools import partial
from core.statistics import spec_anomaly_enabled
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE
from ui.widgets.stat_card import StatCard
from ui.widgets.system_logger import SystemLogger
from ui.widgets.copyable_table import CopyableTable
from ui.widgets.flow_layout import FlowLayout
from ui.widgets.chart_widget import ChartWidget, InteractiveChartWidget
from ui.widgets.busy_overlay import BusyOverlay
import charts as viz
import charts as viz_pg
from core import get_die_position
from core.version import APP_VERSION_LABEL
from ui.controllers.table_controller import (
    SUMMARY_ROW_LABELS, SUMMARY_GROUP_HEADER_ROWS, SUMMARY_CHK_ROWS)

class UIBuilderMixin:
    def _build_run_compare_widget(self):
        """선택한 Workflow의 회차 비교: 표 + Range/StdDev 추이 + 편차 박스플롯."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        self.run_compare_label = QLabel("회차가 1개라 비교할 수 없습니다")
        self.run_compare_label.setStyleSheet(f"color: {FG2}; font-size: 10pt;")
        self.run_compare_label.setWordWrap(True)
        layout.addWidget(self.run_compare_label)

        splitter = QSplitter(Qt.Vertical)
        self.run_compare_table = QTableWidget(0, len(self.RUN_COMPARE_HEADERS))
        self.run_compare_table.setHorizontalHeaderLabels(self.RUN_COMPARE_HEADERS)
        self.run_compare_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.run_compare_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.run_compare_table.setSelectionMode(QTableWidget.SingleSelection)
        self.run_compare_table.verticalHeader().setVisible(False)
        self.run_compare_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.run_compare_table.setToolTip("행을 클릭하면 Step 상세의 회차가 바뀝니다")
        self.run_compare_table.cellClicked.connect(self._on_run_row_clicked)
        splitter.addWidget(self.run_compare_table)

        self.run_compare_charts = QTabWidget()
        self.run_compare_charts.setDocumentMode(True)
        self.chart_widgets['Run Metrics'] = ChartWidget()
        self.chart_widgets['Run Boxplot'] = ChartWidget()
        self.run_compare_charts.addTab(self.chart_widgets['Run Metrics'], 'Range / StdDev')
        self.run_compare_charts.addTab(self.chart_widgets['Run Boxplot'], '편차 Boxplot')
        splitter.addWidget(self.run_compare_charts)
        self.run_compare_table.setMinimumHeight(180)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)
        self.run_compare_table.hide()
        self.run_compare_charts.hide()
        return widget

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 4, 8, 2)
        root_layout.setSpacing(2)

        # ===== TOP BAR =====
        top = QHBoxLayout()
        top.addWidget(QLabel("📁"))
        self.path_edit = QLineEdit(self.folder_path)
        self.path_edit.setMinimumWidth(250)
        self.path_edit.setMaximumWidth(450)
        top.addWidget(self.path_edit)

        self.btn_browse = QPushButton("Open")
        self.btn_browse.clicked.connect(self._browse_folder)
        top.addWidget(self.btn_browse)

        self.btn_scan = QPushButton("Scan & Analysis")
        self.btn_scan.setProperty("accent", True)
        self.btn_scan.clicked.connect(self._scan_folder)
        top.addWidget(self.btn_scan)

        # 폴더명이 Spec 이름과 달라도 Workflow마다 폴더를 골라 분석한다
        self.btn_workflow = QPushButton("Workflow별 선택")
        self.btn_workflow.setToolTip("Workflow(Spec)마다 데이터 폴더를 직접 지정해 분석합니다")
        self.btn_workflow.clicked.connect(self._open_workflow_dialog)
        top.addWidget(self.btn_workflow)

        # 폴더 확인·데이터 로드 중에만 보인다. 중지하면 이전 결과를 그대로 둔다 (Esc도 같다)
        self.btn_cancel_scan = QPushButton("중지 (Esc)")
        self.btn_cancel_scan.setToolTip("진행 중인 폴더 확인·데이터 로드를 멈춥니다. 이전 결과는 그대로입니다")
        self.btn_cancel_scan.clicked.connect(self._cancel_scan)
        self.btn_cancel_scan.setVisible(False)
        top.addWidget(self.btn_cancel_scan)
        self._cancel_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._cancel_shortcut.activated.connect(self._cancel_scan)
        self._cancel_shortcut.setEnabled(False)    # 진행 중일 때만 켠다

        # Wafer 크기 선택 (200mm / 300mm)
        top.addSpacing(16)
        wafer_label = QLabel("Wafer Size:")
        wafer_label.setStyleSheet(f"color: {FG2}; font-size: 9pt;")
        top.addWidget(wafer_label)
        self.wafer_combo = QComboBox()
        self.wafer_combo.addItems(['200mm', '300mm'])
        wafer_size = self.settings.get('wafer_size', 300)
        self.wafer_combo.setCurrentIndex(0 if wafer_size == 200 else 1)
        self.wafer_combo.setFixedWidth(80)
        self.wafer_combo.setStyleSheet(f"""
            QComboBox {{
                background: {BG3}; color: {FG}; border: 1px solid #585b70;
                border-radius: 4px; padding: 3px 8px; font-size: 9pt;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background: {BG2}; color: {FG}; selection-background-color: {ACCENT};
            }}
        """)
        self.wafer_combo.currentIndexChanged.connect(self._on_wafer_size_changed)
        top.addWidget(self.wafer_combo)

        top.addStretch()

        root_layout.addLayout(top)

        # ===== STEP NAV =====
        self.nav_layout = QHBoxLayout()
        self.nav_layout.setSpacing(4)
        nav_container = QWidget()
        nav_container.setLayout(self.nav_layout)
        root_layout.addWidget(nav_container)
        self.step_buttons = []

        # ===== MAIN SPLITTER =====
        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, 1)

        # ════════════ LEFT PANEL ════════════
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(4)

        self.step_title = QLabel("Step: 폴더를 선택하세요")
        self.step_title.setStyleSheet(f"font-size: 16pt; font-weight: bold; color: {ACCENT};")
        left_layout.addWidget(self.step_title)
        # 회차(트러블슈팅 반복)가 둘 이상인 Workflow만 보인다. 카드·차트·PDF가 이 회차를 쓴다
        self.run_combo_label = QLabel("회차:")
        self.run_combo_label.setStyleSheet(f"color: {FG2}; font-size: 10pt;")
        self.run_combo = QComboBox()
        self.run_combo.setMinimumWidth(140)
        self.run_combo.setToolTip("Step 상세(카드·차트·내보내기)에 쓸 회차")
        self.run_combo.currentIndexChanged.connect(self._on_run_combo_changed)
        self.run_combo_label.hide()
        self.run_combo.hide()

        # --- Stat Cards ---
        cards = QHBoxLayout()
        self.card_x = StatCard("X Offset")
        self.card_y = StatCard("Y Offset")
        cards.addWidget(self.card_x)
        cards.addWidget(self.card_y)
        left_layout.addLayout(cards)

        btn_spec = QPushButton("⚙️ Spec Config")
        btn_spec.clicked.connect(self._open_spec_config)
        # 회차 콤보는 Spec Config 버튼 줄에 둔다 (제목 줄에 두면 긴 제목과 합쳐 패널 최소 폭이 커진다)
        spec_row = QHBoxLayout()
        spec_row.addWidget(self.run_combo_label)
        spec_row.addWidget(self.run_combo)
        spec_row.addStretch()
        spec_row.addWidget(btn_spec)
        left_layout.addLayout(spec_row)

        # --- Bottom Tabs ---
        self.main_tabs = QTabWidget()
        left_layout.addWidget(self.main_tabs, 1)

        # Tab 1: System Log
        log_widget = QTextEdit()
        self.main_tabs.addTab(log_widget, "System Log")
        self.logger = SystemLogger(log_widget)

        # Tab 2: Data Table Hub
        data_widget = QWidget()
        data_layout = QVBoxLayout(data_widget)
        data_layout.setContentsMargins(0, 0, 0, 0)
        self.data_tabs = QTabWidget()
        data_layout.addWidget(self.data_tabs)

        # ── Die 필터 패널 ──
        die_filter_frame = QFrame()
        die_filter_frame.setStyleSheet(f"""
            QFrame {{ background: {BG2}; border: 1px solid {BG3};
                     border-radius: 4px; margin: 2px 4px; }}
        """)
        die_filter_layout = QVBoxLayout(die_filter_frame)
        die_filter_layout.setContentsMargins(8, 4, 8, 4)
        die_filter_layout.setSpacing(4)

        # 헤더 행: 타이틀 + ▼/▲ 토글 + 전체 선택 + 안정화 제외 + 정보
        filter_header = QHBoxLayout()
        filter_header.setSpacing(8)
        lbl_filter = QLabel("Die Filter")
        lbl_filter.setStyleSheet(f"color: {ACCENT}; font-size: 9pt; font-weight: bold; border: none;")
        filter_header.addWidget(lbl_filter)

        # Toggle button (expand/collapse)
        self._die_expand_btn = QPushButton()
        self._die_expand_btn.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_ArrowDown))
        self._die_expand_btn.setIconSize(QSize(14, 14))
        self._die_expand_btn.setFixedSize(24, 22)
        self._die_expand_btn.setToolTip("Die 필터 확장 — 포지션 맵과 함께 보기")
        self._die_expand_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; border: none; border-radius: 3px; }}
            QPushButton:hover {{ background: {ACCENT}; }}
        """)
        self._die_expand_btn.clicked.connect(self._toggle_die_filter_expand)
        filter_header.addWidget(self._die_expand_btn)

        self.die_select_all_btn = QPushButton("✅ Select All")
        self.die_select_all_btn.setMinimumHeight(22)
        self.die_select_all_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; color: {FG2}; border: none;
                          border-radius: 3px; padding: 0 8px; font-size: 8pt; }}
            QPushButton:hover {{ background: {ACCENT}; color: white; }}
        """)
        self.die_select_all_btn.clicked.connect(self._die_filter_select_all)
        filter_header.addWidget(self.die_select_all_btn)

        self.die_stab_btn = QPushButton("🚫 Exclude Stabilization Die")
        self.die_stab_btn.setMinimumHeight(22)
        self.die_stab_btn.setToolTip(
            "처음 측정된 Die를 자동으로 체크 해제합니다.\n"
            "장비 안정화 목적으로 첫 Die를 이중 측정한 경우 사용하세요.")
        self.die_stab_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; color: #f38ba8; border: none;
                          border-radius: 3px; padding: 0 8px; font-size: 8pt; }}
            QPushButton:hover {{ background: #f38ba8; color: white; }}
        """)
        self.die_stab_btn.clicked.connect(self._die_filter_exclude_stabilization)
        filter_header.addWidget(self.die_stab_btn)

        self.filter_info_label = QLabel("")
        self.filter_info_label.setStyleSheet("color: #f9e2af; font-size: 8pt; border: none;")
        filter_header.addWidget(self.filter_info_label)
        filter_header.addStretch()
        die_filter_layout.addLayout(filter_header)

        # 접힌 상태: Die 체크박스 한줄 (기존 FlowLayout)
        self._die_cb_container = QWidget()
        self._die_cb_container.setStyleSheet("border: none;")
        self._die_cb_flow = FlowLayout(self._die_cb_container, margin=2, spacing=4)
        die_filter_layout.addWidget(self._die_cb_container)

        # 펼친 상태: 고정 300px, 좌(미니맵) + 우(그리드 체크박스)
        self._die_expanded_panel = QWidget()
        # pattern c: 고정 높이 대신 유연한 범위 — 짧은 화면에서 줄어들 수 있게
        # (내부 die-grid는 _die_grid_scroll로 이미 스크롤 처리됨)
        self._die_expanded_panel.setMinimumHeight(180)
        self._die_expanded_panel.setMaximumHeight(360)
        self._die_expanded_panel.setStyleSheet("border: none;")
        self._die_expanded_panel.setVisible(False)
        exp_layout = QHBoxLayout(self._die_expanded_panel)
        exp_layout.setContentsMargins(0, 4, 0, 0)
        exp_layout.setSpacing(4)

        # 좌측: 미니 Die Position Map 캔버스 컨테이너
        self._mini_map_container = QWidget()
        self._mini_map_container.setStyleSheet("border: none;")
        self._mini_map_layout = QVBoxLayout(self._mini_map_container)
        self._mini_map_layout.setContentsMargins(0, 0, 0, 0)
        self._mini_map_canvas = None  # FigureCanvasQTAgg — 동적으로 교체
        exp_layout.addWidget(self._mini_map_container, 5)

        # 우측: Die 체크박스 그리드 (스크롤 가능)
        self._die_grid_scroll = QScrollArea()
        self._die_grid_scroll.setWidgetResizable(True)
        self._die_grid_scroll.setStyleSheet(f"""
            QScrollArea {{ background: {BG2}; border: none; }}
            QWidget {{ background: {BG2}; }}
        """)
        self._die_grid_widget = QWidget()
        self._die_grid_layout = QGridLayout(self._die_grid_widget)
        self._die_grid_layout.setContentsMargins(4, 4, 4, 4)
        self._die_grid_layout.setSpacing(4)
        self._die_grid_scroll.setWidget(self._die_grid_widget)
        exp_layout.addWidget(self._die_grid_scroll, 3)

        die_filter_layout.addWidget(self._die_expanded_panel)

        self._die_checkboxes = {}  # {die_num(0-based): QCheckBox} — 접힌 상태용
        self._die_grid_checkboxes = {}  # {die_num(0-based): QCheckBox} — 펼친 상태용
        self._die_filter_updating = False  # 재진입 방지 플래그
        self._die_filter_expanded = False  # 토글 상태
        self._mini_die_scatter_map = {}  # {die_idx: scatter_artist}

        data_layout.addWidget(die_filter_frame)
        self.main_tabs.addTab(data_widget, "Data Table")

        # Sub 1: Summary — 통합 전치 테이블 (행 레이아웃은 SUMMARY_ROW_LABELS 참조)
        class _ChecklistBorderDelegate(QStyledItemDelegate):
            """체크리스트 수치 행 첫 열 좌측에 Accent 바(4px)."""
            _CHK_ROWS = set(SUMMARY_CHK_ROWS)
            _BAR_CLR = '#89b4fa'
            _BAR_W   = 4

            def paint(self, painter, option, index):
                super().paint(painter, option, index)
                if index.row() not in self._CHK_ROWS:
                    return
                if index.column() != 0:
                    return
                from PySide6.QtGui import QColor as QC
                painter.save()
                painter.setPen(Qt.NoPen)
                painter.setBrush(QC(self._BAR_CLR))
                rc = option.rect
                painter.drawRect(rc.x(), rc.y(), self._BAR_W, rc.height())
                painter.restore()


        summary_widget = QWidget()
        summary_layout = QVBoxLayout(summary_widget)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(0)

        self.sum_table = CopyableTable()
        # 행 레이아웃은 table_controller.SUMMARY_ROW_LABELS 단일 출처를 사용
        self.sum_table.setRowCount(len(SUMMARY_ROW_LABELS))
        self.sum_table.setVerticalHeaderLabels(SUMMARY_ROW_LABELS)
        vhdr = self.sum_table.verticalHeader()
        vhdr.setVisible(True)
        # pattern c: 고정 폭 대신 최소 폭 — 세로 헤더는 라벨 길이에 맞춰 자동 산정되며
        # 폰트 배율이 커지면 늘어나고, 작아도 148px 아래로는 줄지 않는다(현재 폭 유지).
        vhdr.setMinimumWidth(148)
        vhdr.setDefaultSectionSize(24)
        for _grp_row in SUMMARY_GROUP_HEADER_ROWS:
            self.sum_table.setRowHeight(_grp_row, 26)
        self.sum_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # 셀 delegate (Accent 바)
        self._chk_delegate = _ChecklistBorderDelegate(self.sum_table)
        self.sum_table.setItemDelegate(self._chk_delegate)

        self.sum_table.setSizeAdjustPolicy(
            self.sum_table.SizeAdjustPolicy.AdjustToContents)
        summary_layout.addWidget(self.sum_table)
        summary_layout.addStretch(1)
        self.data_tabs.addTab(summary_widget, "Summary")

        # Sub 2: Die 평균 (X/Y sub-tabs)
        die_widget = QWidget()
        die_layout = QVBoxLayout(die_widget)
        die_layout.setContentsMargins(0, 0, 0, 0)
        self.die_tabs = QTabWidget()
        die_layout.addWidget(self.die_tabs)
        self.die_x_table = CopyableTable()
        self.die_y_table = CopyableTable()
        self.die_tabs.addTab(self.die_x_table, "X Die Average")
        self.die_tabs.addTab(self.die_y_table, "Y Die Average")
        self.data_tabs.addTab(die_widget, "Die Average")

        # Sub 3: Raw Deviation (X/Y)
        dev_widget = QWidget()
        dev_layout = QVBoxLayout(dev_widget)
        dev_layout.setContentsMargins(0, 0, 0, 0)
        self.dev_tabs = QTabWidget()
        dev_layout.addWidget(self.dev_tabs)
        self.dev_x_table = CopyableTable()
        self.dev_y_table = CopyableTable()
        self.dev_tabs.addTab(self.dev_x_table, "X offset")
        self.dev_tabs.addTab(self.dev_y_table, "Y offset")
        self.data_tabs.addTab(dev_widget, "Raw Deviation")

        # Sub 4: Raw Data — 폴더 열기 버튼 포함
        raw_widget = QWidget()
        raw_layout = QVBoxLayout(raw_widget)
        raw_layout.setContentsMargins(0, 0, 0, 0)
        raw_layout.setSpacing(2)

        # 폴더 열기 툴바
        tiff_bar2 = QHBoxLayout()
        btn_tiff_open = QPushButton("📂 Open TIFF Folder")
        btn_tiff_open.clicked.connect(self._open_tiff_folder)
        tiff_bar2.addWidget(btn_tiff_open)
        self.tiff_path_label = QLabel("Double-click row → Load TIFF")
        self.tiff_path_label.setStyleSheet(f"color: {FG2}; font-size: 8pt;")
        tiff_bar2.addWidget(self.tiff_path_label, 1)

        # ─── 이상값 필터 — 표시만 걸러내며 통계·차트·판정에는 영향이 없다 ───
        self.raw_filter_checks = {}
        for key, label, tip in (
            ('failed', '❌ 측정 실패',
             '값이 NaN이거나 Valid=FALSE인 행\n(장비가 정렬·패턴인식에 실패한 측정)'),
            ('outlier', '⚠️ 이상치',
             'IQR 기준을 벗어난 측정값\n(측정은 성공했으나 값이 튀는 경우)'),
            ('spec', '📏 Spec 초과',
             'spec_limits의 LSL/USL를 벗어난 측정값'),
        ):
            # Spec 초과는 Recipe별 판별 조건 확정 전까지 노출하지 않는다
            # (core.statistics.SPEC_ANOMALY_ENABLED)
            if key == 'spec' and not spec_anomaly_enabled():
                continue
            cb = QCheckBox(label)
            cb.setToolTip(tip + '\n\n※ 표시만 걸러냅니다. 통계·차트·판정은 그대로입니다.')
            cb.stateChanged.connect(self._update_raw_table)
            tiff_bar2.addWidget(cb)
            self.raw_filter_checks[key] = cb

        self.raw_filter_label = QLabel("")
        self.raw_filter_label.setStyleSheet(f"color: {FG2}; font-size: 8pt;")
        tiff_bar2.addWidget(self.raw_filter_label)

        raw_bar_w = QWidget()
        raw_bar_w.setLayout(tiff_bar2)
        raw_layout.addWidget(raw_bar_w)

        self.raw_table = CopyableTable()
        cols_r = ['Lot', 'Site', 'Axis', 'HZ1_O', 'V', 'Out']
        fixed_widths = [(4, 30), (5, 30)]
        if spec_anomaly_enabled():
            cols_r.append('Spec')
            fixed_widths.append((6, 40))
        self.raw_table.setColumnCount(len(cols_r))
        self.raw_table.setHorizontalHeaderLabels(cols_r)
        raw_hdr = self.raw_table.horizontalHeader()
        for col in range(len(cols_r)):
            raw_hdr.setSectionResizeMode(col, QHeaderView.Stretch)
        for col, width in fixed_widths:
            raw_hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            self.raw_table.setColumnWidth(col, width)
        self.raw_table.cellDoubleClicked.connect(self._on_row_double_click)
        raw_layout.addWidget(self.raw_table, 1)
        self.data_tabs.addTab(raw_widget, "Raw Data")

        # Cross-display (pattern c): 좌측 컨트롤 컬럼을 QScrollArea로 감싸 짧은/배율
        # 화면에서 세로 콘텐츠가 잘리지 않게 한다(특히 Die 필터 확장 시).
        # 단, QScrollArea의 minimumSizeHint(~68px)는 내부 위젯의 자연 최소폭을
        # 물려받지 않으므로, 내부 컨텐츠 최소폭(측정값 ≈710px)을 명시해 스플리터
        # 핸들로 좁혀도 우측 컨트롤이 잘려 사라지지 않게 한다(기존 가로 동작 유지).
        # 가로 스크롤은 AsNeeded — 폰트 배율 등으로 컨텐츠가 더 넓어질 때만 노출.
        left_scroll = QScrollArea()
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 빌드 시점 스냅샷. 이후 폰트 배율 등으로 컨텐츠가 더 넓어지면 minimumWidth는
        # 재계산되지 않지만, 위의 AsNeeded 가로 스크롤이 안전망 역할을 한다.
        left_scroll.setMinimumWidth(max(710, left.minimumSizeHint().width()))
        left_scroll.setFrameShape(QFrame.NoFrame)
        splitter.addWidget(left_scroll)

        # ════════════ RIGHT PANEL (2-Tier Grouped Tabs) ════════════
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(0)

        # ─── Outer Category Tabs ───
        self.chart_category_tabs = QTabWidget()
        right_layout.addWidget(self.chart_category_tabs)

        self.chart_widgets = {}
        self._inner_tabs = {}  # category_name → inner QTabWidget

        def _add_chart(category, name, widget, register=True):
            if category not in self._inner_tabs:
                inner = QTabWidget()
                inner.setDocumentMode(True)
                self._inner_tabs[category] = inner
                self.chart_category_tabs.addTab(inner, category)
            self._inner_tabs[category].addTab(widget, name)
            if register:
                self.chart_widgets[name] = widget

        # Contour X/Y — Repeat Contour 버튼을 컨테이너에 포함하여 차트 짤림 방지
        _contour_help = (
            "Contour Map 색상 안내\n\n"
            "색상은 편차의 ± 방향을 나타냅니다:\n"
            "  🟢 초록 = 양(+) 방향 오프셋\n"
            "  🟡 노랑 = 0 근처 (편차 없음)\n"
            "  🔴 빨강 = 음(−) 방향 오프셋\n\n"
            "• 색상 대비가 클수록 공간적 편향 존재\n"
            "• 한쪽 빨강 ↔ 반대쪽 초록 = Stage Tilt\n"
            "• 전체가 한 색으로 치우침 = Translation Offset\n\n"
            "💡 Raw Deviation 테이블은 |절대값| 기준이므로\n"
            "   색상이 다를 수 있습니다 (크기 vs 방향)")
        for axis_name in ('Contour X', 'Contour Y'):
            axis = 'X' if 'X' in axis_name else 'Y'
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(2)

            toolbar_row = QHBoxLayout()
            toolbar_row.setContentsMargins(8, 4, 8, 0)
            btn = QPushButton(f"Repeat Contour ({axis})")
            btn.clicked.connect(partial(self._open_repeat_contour, axis))
            toolbar_row.addWidget(btn)

            help_btn = QPushButton()
            help_btn.setIcon(self.style().standardIcon(
                QStyle.StandardPixmap.SP_MessageBoxInformation))
            help_btn.setIconSize(QSize(16, 16))
            help_btn.setFixedSize(22, 22)
            help_btn.setCursor(Qt.WhatsThisCursor)
            help_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: none; }}
                QPushButton:hover {{ background: {BG3}; border-radius: 11px; }}
            """)
            help_btn.setToolTip(_contour_help)
            help_btn.clicked.connect(
                lambda checked=False, b=help_btn, t=_contour_help:
                    QToolTip.showText(b.mapToGlobal(
                        b.rect().bottomLeft()), t, b, b.rect(), 10000))
            toolbar_row.addWidget(help_btn)
            toolbar_row.addStretch()
            container_layout.addLayout(toolbar_row)

            cw = ChartWidget()
            container_layout.addWidget(cw, 1)
            self.chart_widgets[axis_name] = cw
            _add_chart('Basic Analysis', axis_name, container, register=False)

        for name in ['X*Y Offset', 'Die Position']:
            _add_chart('Basic Analysis', name, ChartWidget())

        # Vector Map (슬라이더 컨트롤 포함)
        vm_container = QWidget()
        vm_layout = QVBoxLayout(vm_container)
        vm_layout.setContentsMargins(0, 0, 0, 0)
        vm_layout.setSpacing(2)

        # 슬라이더 바
        slider_bar = QHBoxLayout()
        slider_bar.setContentsMargins(8, 4, 8, 0)
        lbl_title = QLabel("Arrow Scale:")
        lbl_title.setStyleSheet(f"color: {FG2}; font-size: 9pt;")
        slider_bar.addWidget(lbl_title)

        self.vector_scale_slider = QSlider(Qt.Horizontal)
        self.vector_scale_slider.setRange(5, 50)  # 5% ~ 50%
        self.vector_scale_slider.setValue(10)      # 기본 10%
        self.vector_scale_slider.setTickPosition(QSlider.TicksBelow)
        self.vector_scale_slider.setTickInterval(5)
        self.vector_scale_slider.setFixedWidth(200)
        self.vector_scale_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {BG3}; height: 6px; border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT}; width: 14px; height: 14px;
                margin: -4px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ACCENT}; border-radius: 3px;
            }}
        """)
        slider_bar.addWidget(self.vector_scale_slider)

        self.vector_scale_label = QLabel("10%")
        self.vector_scale_label.setFixedWidth(35)
        self.vector_scale_label.setStyleSheet(f"color: {FG}; font-size: 9pt; font-weight: bold;")
        slider_bar.addWidget(self.vector_scale_label)

        _help_text = (
            "화살표 배율 (기본 10%)\n\n"
            "• 값이 클수록 화살표가 길어집니다\n"
            "• 10% = 최대 편차 벡터가 웨이퍼 반경의 10% 길이\n"
            "• 작은 편차가 잘 안 보이면 배율을 높이세요\n\n"
            "예) 300mm 웨이퍼, 10% → 최대 화살표 ≈ 15,000µm\n"
            "예) 300mm 웨이퍼, 30% → 최대 화살표 ≈ 45,000µm")
        help_btn = QPushButton()
        help_btn.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation))
        help_btn.setIconSize(QSize(16, 16))
        help_btn.setFixedSize(22, 22)
        help_btn.setCursor(Qt.WhatsThisCursor)
        help_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; }}
            QPushButton:hover {{ background: {BG3}; border-radius: 11px; }}
        """)
        help_btn.setToolTip(_help_text)
        help_btn.clicked.connect(
            lambda: QToolTip.showText(help_btn.mapToGlobal(
                help_btn.rect().bottomLeft()), _help_text, help_btn, help_btn.rect(), 8000))
        slider_bar.addWidget(help_btn)

        # ── Show Values 토글 버튼 ──
        self.vector_show_values_btn = QPushButton("📐 Show Values")
        self.vector_show_values_btn.setCheckable(True)
        self.vector_show_values_btn.setChecked(False)
        self.vector_show_values_btn.setMinimumHeight(24)
        self.vector_show_values_btn.setToolTip(
            "ON: 각 화살표 옆에 편차 크기(µm)를 표시합니다\n"
            "OFF: 패턴/방향 파악에 집중합니다")
        self.vector_show_values_btn.setStyleSheet(f"""
            QPushButton {{
                color: {FG2}; background: {BG2};
                border: 1px solid #45475a; border-radius: 4px;
                padding: 0 8px; font-size: 8pt;
            }}
            QPushButton:checked {{
                color: {BG}; background: {ACCENT};
                border: 1px solid {ACCENT};
                font-weight: bold;
            }}
            QPushButton:hover:!checked {{ background: {BG3}; }}
        """)
        self.vector_show_values_btn.toggled.connect(
            lambda _: self._on_vector_scale_changed(self.vector_scale_slider.value()))
        slider_bar.addWidget(self.vector_show_values_btn)

        slider_bar.addStretch()

        vm_layout.addLayout(slider_bar)

        # Chart area
        self._vector_chart = ChartWidget()
        vm_layout.addWidget(self._vector_chart, 1)
        self.chart_widgets['Vector Map'] = self._vector_chart

        self.vector_scale_slider.valueChanged.connect(self._on_vector_scale_changed)

        _add_chart('Basic Analysis', 'Vector Map', vm_container, register=False)

        # 인터랙티브 (pyqtgraph)
        # ─── 📈 Lot 트렌드: 컨테이너 (Lot 필터 + 차트) ───
        lot_trend_container = QWidget()
        lt_layout = QVBoxLayout(lot_trend_container)
        lt_layout.setContentsMargins(0, 0, 0, 0)
        lt_layout.setSpacing(2)

        # 도움말 + 필터 버튼 행
        lt_toolbar = QHBoxLayout()
        lt_toolbar.setContentsMargins(8, 4, 8, 0)

        _lot_trend_help = (
            "Lot 트렌드 차트 가이드\n\n"
            "그래프 요소:\n"
            "  🔵 파란 실선 (●) = Lot별 Mean (평균 오프셋)\n"
            "  🟢 초록 점선 (▲) = Lot별 Max (최대값)\n"
            "  🔴 빨강 점선 (▼) = Lot별 Min (최소값)\n"
            "  ░░ 반투명 밴드   = Mean ± 1σ (표준편차 범위)\n"
            "  --- 회색 점선     = Overall Mean (전체 평균)\n\n"
            "패턴 해석:\n"
            "  📈 우상향  = Lot 진행 시 오프셋 증가 (드리프트)\n"
            "  📉 우하향  = 오프셋 감소 (자동 보정 의심)\n"
            "  ➡️ 수평   = 안정적 (정상)\n"
            "  🔀 지그재그 = 불안정 (재현성 점검 필요)\n\n"
            "Lot 필터:\n"
            "  • 체크 해제 → 해당 Lot 제외 후 트렌드 재계산\n"
            "  • [이상 Lot 제외] 장비 이상 Lot를 빼고 추세 확인\n"
            "  • [범위 지정] 처음 N개 / 마지막 N개 구간 비교")

        lt_help_btn = QPushButton()
        lt_help_btn.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation))
        lt_help_btn.setIconSize(QSize(16, 16))
        lt_help_btn.setFixedSize(22, 22)
        lt_help_btn.setCursor(Qt.WhatsThisCursor)
        lt_help_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; }}
            QPushButton:hover {{ background: {BG3}; border-radius: 11px; }}
        """)
        lt_help_btn.setToolTip(_lot_trend_help)
        lt_help_btn.clicked.connect(
            lambda checked=False, b=lt_help_btn, t=_lot_trend_help:
                QToolTip.showText(b.mapToGlobal(
                    b.rect().bottomLeft()), t, b, b.rect(), 10000))
        lt_toolbar.addWidget(lt_help_btn)

        lt_lbl = QLabel("Lot Filter")
        lt_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 9pt; font-weight: bold;")
        lt_toolbar.addWidget(lt_lbl)

        self._lot_select_all_btn = QPushButton("✅ Select All")
        self._lot_select_all_btn.setMinimumHeight(22)
        self._lot_select_all_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; color: {FG2}; border: none;
                          border-radius: 3px; padding: 0 8px; font-size: 8pt; }}
            QPushButton:hover {{ background: {ACCENT}; color: white; }}
        """)
        self._lot_select_all_btn.clicked.connect(self._lot_filter_select_all)
        lt_toolbar.addWidget(self._lot_select_all_btn)

        self._lot_range_btn = QPushButton("Set Range")
        self._lot_range_btn.setMinimumHeight(22)
        self._lot_range_btn.setToolTip(
            "표시할 Lot 범위를 지정합니다.\n"
            "예: 1-5 (처음 5개), -3 (마지막 3개)")
        self._lot_range_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; color: {ORANGE}; border: none;
                          border-radius: 3px; padding: 0 8px; font-size: 8pt; }}
            QPushButton:hover {{ background: {ORANGE}; color: white; }}
        """)
        self._lot_range_btn.clicked.connect(self._lot_filter_range)
        lt_toolbar.addWidget(self._lot_range_btn)

        self._lot_filter_info = QLabel("")
        self._lot_filter_info.setStyleSheet("color: #f9e2af; font-size: 8pt;")
        lt_toolbar.addWidget(self._lot_filter_info)
        lt_toolbar.addStretch()
        lt_layout.addLayout(lt_toolbar)

        # Lot 체크박스 컨테이너
        self._lot_cb_container = QWidget()
        self._lot_cb_container.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        self._lot_cb_flow = FlowLayout(self._lot_cb_container, margin=2, spacing=4)
        lt_layout.addWidget(self._lot_cb_container)

        # 트렌드 차트 위젯
        self._lot_trend_chart = InteractiveChartWidget()
        lt_layout.addWidget(self._lot_trend_chart, 1)
        self.chart_widgets['Lot Trend'] = self._lot_trend_chart
        _add_chart('Interactive', 'Lot Trend', lot_trend_container, register=False)

        # ─── 🎯 XY Scatter: 컨테이너 (툴바 + 차트 + 사이드 범례) ───
        xy_scatter_container = QWidget()
        xs_layout = QVBoxLayout(xy_scatter_container)
        xs_layout.setContentsMargins(0, 0, 0, 0)
        xs_layout.setSpacing(2)

        xs_toolbar = QHBoxLayout()
        xs_toolbar.setContentsMargins(8, 4, 8, 0)

        # 안내 라벨
        xs_info = QLabel("💡 우측 범례에서 Die를 클릭하면 해당 Die만 강조됩니다")
        xs_info.setStyleSheet(f"color: {FG2}; font-size: 8pt;")
        xs_toolbar.addWidget(xs_info)
        xs_toolbar.addStretch()

        # Log 스케일 토글 버튼
        self._xy_log_btn = QPushButton("Log Scale")
        self._xy_log_btn.setCheckable(True)
        self._xy_log_btn.setMinimumHeight(22)
        self._xy_log_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; color: {FG2}; border: none;
                          border-radius: 3px; padding: 0 10px; font-size: 8pt; }}
            QPushButton:hover {{ background: {ACCENT}; color: white; }}
            QPushButton:checked {{ background: {ACCENT}; color: white; font-weight: bold; }}
        """)
        self._xy_log_btn.setToolTip(
            "Signed Log 변환: sign × log₁₀(1 + |value|)\n"
            "양/음 오프셋의 부호를 유지하면서 큰 값 차이를 압축합니다.")
        self._xy_log_btn.clicked.connect(self._toggle_xy_log_scale)
        xs_toolbar.addWidget(self._xy_log_btn)

        xs_layout.addLayout(xs_toolbar)

        # 본문: 차트(좌) + 범례 패널(우)
        xs_body = QHBoxLayout()
        xs_body.setSpacing(2)

        self._xy_scatter_chart = InteractiveChartWidget()
        xs_body.addWidget(self._xy_scatter_chart, 7)
        self.chart_widgets['XY Scatter'] = self._xy_scatter_chart

        # 사이드 범례 패널
        self._xy_legend_panel = QWidget()
        # pattern c: 고정 폭 대신 범위 — 고DPI에서 '■ DieNN' 텍스트가 잘리지 않게.
        # xs_body에서 차트가 stretch 7이므로 범례는 110~200 내에서만 폭을 흡수.
        self._xy_legend_panel.setMinimumWidth(110)
        self._xy_legend_panel.setMaximumWidth(200)
        self._xy_legend_panel.setStyleSheet(f"background: {BG2};")
        legend_vbox = QVBoxLayout(self._xy_legend_panel)
        legend_vbox.setContentsMargins(4, 4, 4, 4)
        legend_vbox.setSpacing(2)

        # [전체 표시] 리셋 버튼
        self._xy_legend_reset_btn = QPushButton("Show All")
        self._xy_legend_reset_btn.setMinimumHeight(22)
        self._xy_legend_reset_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; color: {ACCENT}; border: none;
                          border-radius: 3px; font-size: 8pt; font-weight: bold; }}
            QPushButton:hover {{ background: {ACCENT}; color: white; }}
        """)
        self._xy_legend_reset_btn.clicked.connect(self._xy_legend_reset)
        legend_vbox.addWidget(self._xy_legend_reset_btn)

        # Die 버튼 스크롤 영역
        self._xy_legend_scroll = QScrollArea()
        self._xy_legend_scroll.setWidgetResizable(True)
        self._xy_legend_scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: {BG2}; }}
            QWidget {{ background: {BG2}; }}
        """)
        self._xy_legend_btn_widget = QWidget()
        self._xy_legend_btn_layout = QVBoxLayout(self._xy_legend_btn_widget)
        self._xy_legend_btn_layout.setContentsMargins(0, 2, 0, 2)
        self._xy_legend_btn_layout.setSpacing(2)
        self._xy_legend_btn_layout.addStretch()
        self._xy_legend_scroll.setWidget(self._xy_legend_btn_widget)
        legend_vbox.addWidget(self._xy_legend_scroll, 1)

        xs_body.addWidget(self._xy_legend_panel)
        xs_layout.addLayout(xs_body, 1)

        self._xy_log_mode = False
        self._xy_legend_buttons = {}  # {die_label: QPushButton}
        self._xy_highlighted_dies = set()  # 현재 범례에서 선택된 Die 세트

        _add_chart('Interactive', 'XY Scatter', xy_scatter_container, register=False)


        # ─── 📊 분포: X/Y 서브탭 ───
        dist_tabs = QTabWidget()
        dist_tabs.setDocumentMode(True)
        dist_x = InteractiveChartWidget()
        dist_y = InteractiveChartWidget()
        dist_tabs.addTab(dist_x, 'X')
        dist_tabs.addTab(dist_y, 'Y')
        self.chart_widgets['Distribution X'] = dist_x
        self.chart_widgets['Distribution Y'] = dist_y
        _add_chart('Interactive', 'Distribution', dist_tabs, register=False)

        # TIFF
        tiff_cw = InteractiveChartWidget()
        tiff_viewer = viz_pg.create_tiff_widget()
        tiff_cw.set_widget(tiff_viewer)
        _add_chart('Interactive', 'TIFF', tiff_cw)
        self._tiff_viewer = tiff_viewer

        # 고급 분석 (pyqtgraph — Phase 2)
        for name in ['Pareto', 'Correlation']:
            _add_chart('Advanced', name, InteractiveChartWidget())

        # ─── 🌐 3D Surface: X/Y 서브탭 ───
        surface_tabs = QTabWidget()
        surface_tabs.setDocumentMode(True)
        surface_x = InteractiveChartWidget()
        surface_y = InteractiveChartWidget()
        surface_mag = InteractiveChartWidget()
        surface_tabs.addTab(surface_x, 'X')
        surface_tabs.addTab(surface_y, 'Y')
        surface_tabs.addTab(surface_mag, 'X+Y (Mag)')
        self.chart_widgets['3D X'] = surface_x
        self.chart_widgets['3D Y'] = surface_y
        self.chart_widgets['3D X+Y'] = surface_mag
        _add_chart('Advanced', '3D Surface', surface_tabs, register=False)

        # 비교 (matplotlib — Recipe Comparison: 3개 서브탭)
        for name in ['Boxplot', 'Trend', 'Heatmap']:
            _add_chart('Comparison', name, ChartWidget())
        _add_chart('Comparison', '회차 비교', self._build_run_compare_widget(), register=False)
        # 가려진 비교 차트는 탭이 보일 때 그린다
        self.chart_category_tabs.currentChanged.connect(self._flush_hidden_charts)
        self._inner_tabs['Comparison'].currentChanged.connect(self._flush_hidden_charts)

        # ── Remark 탭 (Export + Help 하위탭) ──────────────────────
        # ── Export 하위탭 ─────────────────────────────────────────
        export_widget = QWidget()
        export_layout = QVBoxLayout(export_widget)
        export_layout.setContentsMargins(40, 40, 40, 40)
        export_layout.setSpacing(16)

        export_header = QLabel("Data Export")
        export_header.setStyleSheet(f"color:{ACCENT}; font-size:16pt; font-weight:bold;")
        export_header.setAlignment(Qt.AlignCenter)
        export_layout.addWidget(export_header)

        export_desc = QLabel("분석 결과를 다양한 형식으로 내보낼 수 있습니다.")
        export_desc.setStyleSheet(f"color:{FG2}; font-size:10pt;")
        export_desc.setAlignment(Qt.AlignCenter)
        export_layout.addWidget(export_desc)
        export_layout.addSpacing(10)

        export_buttons_data = [
            ('Excel Export', 'Die별 편차, Summary, Raw Data 등\n전체 데이터를 Excel 파일로 저장', self._export_excel, ACCENT),
            ('Quality Report', '측정 실패·이상치·Spec 초과 행과 System Log를\n전체 Recipe 기준으로 Excel 저장 (추적정보 포함)', self._export_quality_report, ORANGE),
            ('CSV Export', 'Raw Data를 CSV 형식으로 저장\n타 프로그램에서 불러오기 용이', self._export_csv, GREEN),
            ('PDF Report', '차트 + 통계 포함 PDF 보고서 생성\n출력 및 공유용', self._export_pdf, RED),
            ('AI 분석용 Markdown', 'Spec 초과 원인 분석을 LLM에 맡기기 위한 Markdown 한 파일\n'
             '요약·원인 단서·회차 비교·Die×Repeat 편차 표 포함', self._export_llm_markdown, PURPLE),
        ]
        for text, desc, slot, color in export_buttons_data:
            btn = QPushButton(f"{text}")
            btn.setMinimumHeight(60)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {BG3}; color: {FG};
                    border: 1px solid {color}40; border-left: 4px solid {color};
                    border-radius: 6px; font-size: 12pt; font-weight: bold;
                    text-align: left; padding: 12px 20px;
                }}
                QPushButton:hover {{ background: #45475a; border-color: {color}; }}
            """)
            btn.setToolTip(desc)
            btn.clicked.connect(slot)
            export_layout.addWidget(btn)

            desc_label = QLabel(f"    {desc.split(chr(10))[0]}")
            desc_label.setStyleSheet(f"color:{FG2}; font-size:8pt; margin-bottom:4px;")
            export_layout.addWidget(desc_label)

        export_layout.addStretch()

        # ── Help 하위탭 ───────────────────────────────────────────
        help_widget = QWidget()
        help_layout = QVBoxLayout(help_widget)
        help_layout.setContentsMargins(40, 30, 40, 30)
        help_layout.setSpacing(12)

        help_scroll_content = QWidget()
        help_scroll_layout = QVBoxLayout(help_scroll_content)
        help_scroll_layout.setContentsMargins(0, 0, 0, 0)
        help_scroll_layout.setSpacing(14)

        # 1) 프로그램 정보
        info_header = QLabel("XY Stage Positioning Offset Analysis")
        info_header.setStyleSheet(f"color:{ACCENT}; font-size:14pt; font-weight:bold;")
        info_header.setAlignment(Qt.AlignCenter)
        help_scroll_layout.addWidget(info_header)

        info_version = QLabel(f"{APP_VERSION_LABEL}  |  Production & Quality Control Team, Manufacturing Dept.")
        info_version.setStyleSheet(f"color:{FG2}; font-size:9pt;")
        info_version.setAlignment(Qt.AlignCenter)
        help_scroll_layout.addWidget(info_version)

        from PySide6.QtWidgets import QFrame as _QFrame
        sep1 = _QFrame(); sep1.setFrameShape(_QFrame.HLine)
        sep1.setStyleSheet(f"color:{BG3};"); help_scroll_layout.addWidget(sep1)

        dev_label = QLabel(
            "Developer :  Levi.Beak\n"
            "Team          :  Production &amp; Quality Control Team, Manufacturing Dept.\n"
            "Email          :  levi.beak@parksystems.com"
        )
        dev_label.setStyleSheet(f"color:{FG}; font-size:9pt; line-height:160%;")
        dev_label.setAlignment(Qt.AlignCenter)
        dev_label.setTextFormat(Qt.RichText)
        help_scroll_layout.addWidget(dev_label)

        sep2 = _QFrame(); sep2.setFrameShape(_QFrame.HLine)
        sep2.setStyleSheet(f"color:{BG3};"); help_scroll_layout.addWidget(sep2)

        # 2) Analysis Guide 버튼
        btn_guide = QPushButton("Analysis Guide")
        btn_guide.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; color: {ACCENT};
                          border: 1px solid {ACCENT}60; border-radius: 6px;
                          font-size: 11pt; font-weight: bold; padding: 10px 24px; }}
            QPushButton:hover {{ background: {ACCENT}; color: {BG}; }}
        """)
        btn_guide.setCursor(Qt.PointingHandCursor)
        btn_guide.clicked.connect(self._show_guide_dialog)
        btn_guide.setFixedHeight(44)
        help_scroll_layout.addWidget(btn_guide, alignment=Qt.AlignCenter)

        sep3 = _QFrame(); sep3.setFrameShape(_QFrame.HLine)
        sep3.setStyleSheet(f"color:{BG3};"); help_scroll_layout.addWidget(sep3)

        # 3) 체크리스트 기입 방법 — 제목
        guide_title = QLabel("체크리스트 기입 방법")
        guide_title.setStyleSheet(f"color:{FG}; font-size:11pt; font-weight:bold;")
        guide_title.setAlignment(Qt.AlignLeft)
        help_scroll_layout.addWidget(guide_title)

        guide_sub = QLabel("Summary 탭의 Checklist 섹션 값을 Recipe 열 순서대로 확인하여 기입하세요.")
        guide_sub.setStyleSheet(f"color:{FG2}; font-size:9pt;")
        guide_sub.setWordWrap(True)
        help_scroll_layout.addWidget(guide_sub)

        # 4) 다이어그램 이미지
        import os
        _img_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'checklist_guide.png')
        _img_path = os.path.normpath(_img_path)
        from PySide6.QtGui import QPixmap
        if os.path.exists(_img_path):
            img_label = QLabel()
            pix = QPixmap(_img_path)
            img_label.setPixmap(pix.scaledToWidth(850, Qt.SmoothTransformation))
            img_label.setAlignment(Qt.AlignCenter)
            help_scroll_layout.addWidget(img_label)
        else:
            no_img = QLabel("[이미지 없음: assets/checklist_guide.png]")
            no_img.setStyleSheet(f"color:{FG2}; font-size:8pt;")
            help_scroll_layout.addWidget(no_img)

        # 5) 단계별 텍스트 가이드
        steps_text = (
            "<ol style='color:#cdd6f4; font-size:9pt; line-height:200%;'>"
            "<li><b>Summary</b> 탭을 클릭합니다.</li>"
            "<li>상단 <b>Checklist</b> 섹션에서 각 Recipe 열의 값을 확인합니다.<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;<span style='color:#a6adc8;'>"
            "① Vision Pattern &nbsp;② In-Die Align &nbsp;③ LLC Translation &nbsp;④ Global Align</span></li>"
            "<li><b>X Result</b> / <b>Y Result</b> (PASS/FAIL) 를 최종 확인 후 진행합니다.</li>"
            "<li><b>X Dev Range</b>, <b>Y Dev Range</b>, <b>X Dev StdDev</b>, <b>Y Dev StdDev</b> 값을 "
            "순서대로 체크리스트에 기입합니다.</li>"        
            "<li>결과를 <b>Remark &gt; Export</b> 탭에서 Excel로 저장할 수 있습니다.</li>"
            "</ol>"
        )
        steps_label = QLabel(steps_text)
        steps_label.setTextFormat(Qt.RichText)
        steps_label.setWordWrap(True)
        steps_label.setStyleSheet(f"background:{BG3}; border-radius:6px; padding:14px;")
        help_scroll_layout.addWidget(steps_label)
        help_scroll_layout.addStretch()

        # 스크롤 영역 적용
        help_scroll = QScrollArea()
        help_scroll.setWidget(help_scroll_content)
        help_scroll.setWidgetResizable(True)
        help_scroll.setFrameShape(QScrollArea.NoFrame)
        help_layout.addWidget(help_scroll)

        # ── Remark 카테고리 탭으로 등록 ───────────────────────────
        inner_remark = QTabWidget()
        inner_remark.setDocumentMode(True)
        inner_remark.addTab(export_widget, 'Export')
        inner_remark.addTab(help_widget,   'Help')
        self._inner_tabs['Remark'] = inner_remark
        self.chart_category_tabs.addTab(inner_remark, 'Remark')


        splitter.addWidget(right)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([960, 960])  # 초기 5:5 비율

        # ─── 좌측 패널 토글 (F11 + 스플리터 더블클릭) ───
        self._main_splitter = splitter
        # 폴더 확인·데이터 읽기·화면 그리기 동안 결과 영역을 덮는 진행 표시
        self._busy_overlay = BusyOverlay(splitter, on_cancel=self._cancel_scan)
        # 좌측 패널 핸들 보관(스크롤 wrapper). F11/더블클릭 토글은 _toggle_left_panel에서
        # 스플리터 sizes로 동작하므로 이 참조 자체를 읽지는 않는다.
        self._left_panel = left_scroll
        self._saved_splitter_sizes = None

        shortcut = QShortcut(QKeySequence('F11'), self)
        shortcut.activated.connect(self._toggle_left_panel)
        splitter.handle(1).installEventFilter(self)

        # Render die position immediately
        QTimer.singleShot(200, self._render_die_position)

        # Boot log
        QTimer.singleShot(50, lambda: self.logger.head(f"XY Stage Offset Analyzer {APP_VERSION_LABEL} 시작"))
        QTimer.singleShot(100, lambda: self.logger.info(
            "폴더를 선택하고 '스캔 & 분석'을 눌러주세요."))

        # Status bar: 영구 위젯은 showMessage가 덮어쓰지 않아 실행 버전이 항상 보인다
        # 작업 중에만 보이는 진행 막대 (n/m을 알면 채우고, 모르면 바쁨 표시)
        self.status_progress = QProgressBar()
        self.status_progress.setTextVisible(False)
        self.status_progress.setFixedSize(180, 12)
        self.status_progress.setStyleSheet(
            f"QProgressBar {{ background: {BG3}; border: none; border-radius: 5px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}")
        self.status_progress.hide()
        self.statusBar().addPermanentWidget(self.status_progress)
        self.version_status_label = QLabel(APP_VERSION_LABEL)
        self.version_status_label.setStyleSheet(f"color:{FG2}; padding:0 8px;")
        self.statusBar().addPermanentWidget(self.version_status_label)
        self.statusBar().showMessage("폴더를 선택하세요.")

    # ──────────────────────────────────────────────
    # Chart Navigation
    # ──────────────────────────────────────────────
    def _select_chart(self, name: str):
        """프로그래밍 방식으로 차트 선택 (카테고리 + 내부 탭 자동 전환)."""
        for cat_name, inner_tab in self._inner_tabs.items():
            for i in range(inner_tab.count()):
                if inner_tab.tabText(i) == name:
                    self.chart_category_tabs.setCurrentWidget(inner_tab)
                    inner_tab.setCurrentIndex(i)
                    return

    def _toggle_left_panel(self):
        """좌측 패널 접기/펼치기 (F11)."""
        sizes = self._main_splitter.sizes()
        if sizes[0] > 0:
            self._saved_splitter_sizes = sizes
            self._main_splitter.setSizes([0, sum(sizes)])
        else:
            if self._saved_splitter_sizes:
                self._main_splitter.setSizes(self._saved_splitter_sizes)
            else:
                total = sum(sizes)
                self._main_splitter.setSizes([total // 2, total // 2])

    def _show_guide_dialog(self):
        from ui.dialogs.guide_dialog import GuideDialog
        # 이미 열려 있으면 앞으로 가져오기 (중복 창 방지)
        if hasattr(self, '_guide_dlg') and self._guide_dlg is not None \
                and self._guide_dlg.isVisible():
            self._guide_dlg.raise_()
            self._guide_dlg.activateWindow()
            return
        self._guide_dlg = GuideDialog(self)
        self._guide_dlg.setModal(False)   # 비모달 — 부모 창 락 해제
        self._guide_dlg.show()

    def _open_repeat_contour(self, axis: str = 'X'):
        from ui.dialogs.repeat_contour_dialog import RepeatContourDialog
        # Provide dev_data and dyn_positions from app state to the dialog
        dev_data = self._dev_x if axis == 'X' else self._dev_y
        dyn_pos = getattr(self, '_dynamic_die_positions', None)
        dlg = RepeatContourDialog(self, axis, dev_data, dyn_pos)
        dlg.exec()

    def _open_spec_config(self):
        from ui.dialogs.spec_config_dialog import SpecConfigDialog
        dlg = SpecConfigDialog(self, self.settings)
        dlg.exec()


