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
import time
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
from core.version import APP_VERSION_LABEL
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
from ui.dialogs.image_shift_dialog import threads_alive as image_shift_threads_alive
from ui.controllers.run_compare_controller import RunCompareMixin
from ui.controllers.positioning_controller import PositioningMixin
from ui.controllers.diagnosis_controller import DiagnosisMixin

# ═══════════════════════════════════════════════
#  Main Application
# ═══════════════════════════════════════════════
class DataAnalyzerApp(UIBuilderMixin, QMainWindow, ScanMixin, StepMixin, CardMixin, TableMixin, ChartMixin, XYLegendMixin, DieFilterMixin, LotFilterMixin, TiffMixin, ExportMixin, RunCompareMixin, PositioningMixin, DiagnosisMixin):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"📊 XY Stage Offset Analyzer {APP_VERSION_LABEL} — Workflow")
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
        self.last_tiff_folder = ''
        self._dev_x = {}
        self._dev_y = {}
        self._loader_thread = None
        self.step_pass_states = {}  # {idx: True/False/None}
        self.step_reference_states = {}  # {idx: 1 Repeat 참고 판정 여부}
        self._trend_data_x = []  # 현재 Step의 X trend 원본 (Lot 필터용)
        self._trend_data_y = []  # 현재 Step의 Y trend 원본
        self._trend_spec = None  # Spec 한계선용
        self._lot_filter_updating = False
        self._lot_checkboxes = {}  # {lot_name: QCheckBox}
        self._die_filter_reset_pending = False  # True면 다음 표시 때 Die 필터를 기본값으로 재생성
        self._scanned_folder = ''  # recipe_results를 읽어 온 폴더

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
        # 진행 중인 폴더 확인·로드·TIFF 읽기를 모두 중지시키고 잠깐만 기다린다.
        # 네트워크 읽기 하나가 끝나야 멈추므로 오래 기다리지 않는다. 강제 종료(terminate)는
        # 하지 않는다 — 파일 핸들을 쥔 채 죽을 수 있다. 남은 스레드는 main()이 정리한다.
        threads = self._background_threads()
        for thread in threads:
            if hasattr(thread, 'cancel'):
                thread.cancel()
        cancel_diagnosis = getattr(self, '_cancel_diagnosis_reads', None)
        if cancel_diagnosis is not None:    # 진단 Log 읽기(파이썬 스레드)도 남은 Lot은 읽지 않게
            cancel_diagnosis()
        deadline = time.monotonic() + 1.5
        for thread in threads:
            thread.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        import matplotlib.pyplot as plt
        plt.close('all')
        event.accept()

    def _background_threads(self):
        """실행 중인 작업 스레드 (폴더 확인, 로드, TIFF, 중지했지만 아직 끝나지 않은 것)."""
        candidates = [getattr(self, name, None)
                      for name in ('_scan_thread', '_loader_thread', '_tiff_thread')]
        candidates += list(self.__dict__.get('_retired_threads') or [])
        return [t for t in candidates
                if t is not None and hasattr(t, 'isRunning') and t.isRunning()]

    def background_busy(self) -> bool:
        return bool(self._background_threads())

    def _save_settings(self):
        # 지난 폴더는 마지막으로 로드를 마친 폴더다 (중지한 로드나 입력만 한 경로가 아니다)
        self.settings['last_folder'] = self.folder_path
        geom = self.geometry()
        self.settings['window_geometry'] = f"{geom.width()}x{geom.height()}+{geom.x()}+{geom.y()}"
        save_settings(self.settings)

    def _restore_settings(self):
        # 지난 폴더가 네트워크 경로면 isdir 한 번도 수 초가 걸린다. 창을 먼저 띄우고
        # 폴더 확인은 스캔 스레드가 한다 (없으면 경고 없이 System Log에만 남긴다)
        if self.folder_path:
            self.path_edit.setText(self.folder_path)
            QTimer.singleShot(100, partial(self._scan_folder, startup=True))
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
    code = app.exec()
    # 네트워크 읽기에 묶인 작업 스레드가 남으면 창 없이 프로세스가 남는다 (설정은 저장했다)
    # 이미지 이동량 분석 창의 QThread가 남은 채 Python이 끝나면 'Destroyed while thread is still running'으로 죽는다
    if window.background_busy() or _worker_threads_alive() or image_shift_threads_alive():
        os._exit(code)
    sys.exit(code)


def _worker_threads_alive() -> bool:
    """끝나지 않은 Python 작업 스레드 (대화상자 확인·Lot 병렬 읽기). 있으면 종료가 기다린다."""
    main_thread = threading.main_thread()
    return any(t is not main_thread and not t.daemon and t.is_alive()
               for t in threading.enumerate())


if __name__ == '__main__':
    main()
