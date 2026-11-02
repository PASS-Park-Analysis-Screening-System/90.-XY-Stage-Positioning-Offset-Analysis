"""
main.py — XY Stage Positioning Offset Analysis (PySide6)

Layout:
  [Left]  Top:   X/Y Stat Cards (Pass/Fail)
          Bottom: [시스템 로그] | [데이터 테이블]
  [Right] Full-height chart tabs (Contour, Vector Map, Scatter, etc.)
"""

import os
import sys
import math
import subprocess
import threading
from datetime import datetime
from functools import partial

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSplitter, QPushButton, QLabel, QLineEdit, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QFrame, QDialog, QComboBox, QSlider, QToolTip, QCheckBox, QInputDialog,
    QAbstractItemView, QSizePolicy, QStatusBar, QScrollArea, QTextEdit,
    QListWidget, QTextBrowser, QDialogButtonBox, QStyle,
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QSize, QRect, QPoint
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut, QGuiApplication

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.csv_loader import scan_lot_folders, batch_load, get_scan_summary
from core import (
    compute_statistics, compute_group_statistics, compute_trend,
    detect_outliers, compute_repeatability, compute_cpk,
    filter_by_method, filter_valid_only, compute_deviation_matrix,
    compute_xy_product, compute_affine_transform, DIE_POSITIONS, get_die_position,
    compute_pareto_data, compute_correlation, extract_die_positions,
    filter_stabilization_die,
)
from core.exporter import export_combined_csv, export_excel_report
from core.settings import load_settings, save_settings, add_recent_folder, parse_geometry_string
## sparkline_delegate not currently used (gauge removed)
from core.recipe_scanner import scan_recipes, load_recipe_data, load_all_recipes, compare_recipes
import charts as viz
import charts as viz_pg

# 
#  분리된 UI 모듈 import
# 
from ui.theme import *
from ui.widgets.system_logger import SystemLogger
from ui.widgets.copyable_table import CopyableTable
from ui.widgets.flow_layout import FlowLayout
from ui.widgets.chart_widget import ChartWidget, InteractiveChartWidget
from ui.widgets.stat_card import StatCard
from ui.workers.data_loader_thread import DataLoaderThread
from ui.color_helpers import _heatmap_diverging, _heatmap_single, _contrast_fg
from ui.dialogs.guide_dialog import GuideDialog


from ui.controllers.scan_controller import ScanMixin
from ui.controllers.ui_builder_mixin import UIBuilderMixin
from ui.dialogs.guide_dialog import GuideDialog
from ui.dialogs.repeat_contour_dialog import RepeatContourDialog
from ui.dialogs.spec_config_dialog import SpecConfigDialog
from ui.controllers.step_controller import StepMixin
from ui.controllers.card_controller import CardMixin
from ui.controllers.table_controller import TableMixin
from ui.controllers.chart_controller import ChartMixin
from ui.controllers.xy_legend_controller import XYLegendMixin
from ui.controllers.die_filter_controller import DieFilterMixin
from ui.controllers.lot_filter_controller import LotFilterMixin
from ui.controllers.tiff_controller import TiffMixin
from ui.controllers.export_controller import ExportMixin

# ═══════════════════════════════════════════════
#  Main Application
# ═══════════════════════════════════════════════
class DataAnalyzerApp(UIBuilderMixin, QMainWindow, ScanMixin, StepMixin, CardMixin, TableMixin, ChartMixin, XYLegendMixin, DieFilterMixin, LotFilterMixin, TiffMixin, ExportMixin):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("📊 XY Stage Offset Analyzer — Workflow")
        # Cross-display: keep the floor small enough for a DPI-scaled laptop
        # (1920×1080 @150% = 1280×720 logical). Initial sizing is delegated to
        # the geometry-restore / fit-to-screen path in _restore_geometry().
        self.setMinimumSize(1024, 640)

        # State
        self.settings = load_settings()
        self.folder_path = self.settings.get('last_folder', '')
        self.recipes = []
        self.recipe_results = []
        self.current_recipe_idx = -1
        self.raw_data = []
        self.lot_list = []
        self.last_tiff_folder = ''
        self._dev_x = {}
        self._dev_y = {}
        self._loader_thread = None
        self.step_pass_states = {}  # {idx: True/False/None}
        self._trend_data_x = []  # 현재 Step의 X trend 원본 (Lot 필터용)
        self._trend_data_y = []  # 현재 Step의 Y trend 원본
        self._trend_spec = None  # Spec 한계선용
        self._lot_filter_updating = False
        self._lot_checkboxes = {}  # {lot_name: QCheckBox}
        self._die_filter_reset_pending = False  # True면 다음 표시 때 Die 필터를 기본값으로 재생성

        self._build_ui()
        self._restore_settings()  # 폴더 + 저장된 창 geometry 복원(또는 최대화)

    # ──────────────────────────────────────────────
    # Build UI
    # ──────────────────────────────────────────────
    def eventFilter(self, obj, event):
        """스플리터 핸들 더블클릭 → 좌측 패널 토글."""
        from PySide6.QtCore import QEvent
        if (hasattr(self, '_main_splitter')
                and obj is self._main_splitter.handle(1)
                and event.type() == QEvent.MouseButtonDblClick):
            self._toggle_left_panel()
            return True
        return super().eventFilter(obj, event)

    # ──────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────
    def closeEvent(self, event):
        self._save_settings()
        # 실행 중인 백그라운드 로드 스레드 정리 —
        # 'QThread: Destroyed while thread is still running' 크래시 방지.
        thread = getattr(self, '_loader_thread', None)
        if thread is not None and thread.isRunning():
            thread.quit()
            if not thread.wait(3000):
                # run()이 이벤트 루프 없이 CPU/IO 작업 중이라 quit()에 반응하지 않으면
                # 종료 시점이므로 강제 종료로 destroy-while-running을 막는다.
                thread.terminate()
                thread.wait()
        import matplotlib.pyplot as plt
        plt.close('all')
        event.accept()

    def _save_settings(self):
        self.settings['last_folder'] = self.path_edit.text()
        geom = self.geometry()
        self.settings['window_geometry'] = f"{geom.width()}x{geom.height()}+{geom.x()}+{geom.y()}"
        save_settings(self.settings)

    def _restore_settings(self):
        if self.folder_path and os.path.isdir(self.folder_path):
            self.path_edit.setText(self.folder_path)
            QTimer.singleShot(100, self._scan_folder)
        self._restore_geometry()

    def _restore_geometry(self):
        """저장된 window_geometry("WxH+X+Y")를 현재 화면 범위로 클램프해 복원.

        다른 모니터(해상도/DPI 배율이 다른)에서 저장된 geometry를 그대로 복원하면
        창이 화면 밖으로 나가거나 화면보다 커질 수 있다. 저장된 위치가 속한 화면의
        availableGeometry()에 맞춰 크기/위치를 클램프한다. 저장값이 없으면
        화면에 맞춰 중앙 배치(_fit_to_screen).
        """
        parsed = parse_geometry_string(self.settings.get('window_geometry', ''))
        if parsed is not None:
            x, y, w, h = parsed
            screen = (QGuiApplication.screenAt(QPoint(x, y))
                      or QGuiApplication.primaryScreen())
            if screen is not None:
                avail = screen.availableGeometry()
                # 화면(작업영역)이 최소 크기보다 작으면 클램프해도 넘치므로
                # 최대화로 폴백(_fit_to_screen). (skill common-mistake #5)
                if (avail.width() >= self.minimumWidth()
                        and avail.height() >= self.minimumHeight()):
                    # 화면보다 크지 않게 + 최소 크기 보장
                    w = max(min(w, avail.width()), self.minimumWidth())
                    h = max(min(h, avail.height()), self.minimumHeight())
                    # 타이틀바가 화면 밖으로 나가지 않도록 위치 클램프
                    x = max(avail.x(), min(x, avail.x() + avail.width() - w))
                    y = max(avail.y(), min(y, avail.y() + avail.height() - h))
                    self.setGeometry(x, y, w, h)
                    return
        self._fit_to_screen()

    def _fit_to_screen(self, ratio=0.85, cap=(1600, 1050)):
        """창을 현재 화면 작업영역의 ratio 비율로(최대 cap) 중앙 배치.

        화면(작업영역)이 최소 크기보다 작으면 최대화로 폴백한다.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        if avail.width() < self.minimumWidth() or avail.height() < self.minimumHeight():
            self.showMaximized()
            return
        w = max(min(int(avail.width() * ratio), cap[0]), self.minimumWidth())
        h = max(min(int(avail.height() * ratio), cap[1]), self.minimumHeight())
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2,
                  avail.y() + (avail.height() - h) // 2)

    # ──────────────────────────────────────────────
    # Navigation
    # ──────────────────────────────────────────────
def main():
    # Cross-display High-DPI: MUST run before the QApplication instance exists,
    # or it is silently ignored. PassThrough keeps crisp fractional scaling (×1.5).
    if QApplication.instance() is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLE)
    window = DataAnalyzerApp()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
