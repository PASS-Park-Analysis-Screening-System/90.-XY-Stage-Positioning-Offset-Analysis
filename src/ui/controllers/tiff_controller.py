from PySide6.QtWidgets import QMessageBox, QFileDialog, QApplication
from PySide6.QtCore import Qt, QThread, Signal
import os
import re
import traceback
from core.tiff_loader import load_tiff
from charts import MultiTiffViewerWidget
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED, ORANGE, PURPLE


def find_tiffs_for_row(round_path, lot_name, site_id):
    """site_id를 포함하는 TIFF 파일을 해당 Lot 폴더에서 탐색한다 (작업 스레드에서 부른다).

    Debug/Capture 하위폴더는 제외하고 루트 파일만 검색합니다.
    os.scandir은 목록과 함께 파일·폴더 여부를 주므로 항목마다 따로 묻지 않는다
    (네트워크 폴더는 TIFF가 40개 넘게 있어 그만큼 왕복이 늘었다).

    Returns:
        (matched 경로 목록, Lot 폴더, 실패 문구 또는 '')
    """
    try:
        with os.scandir(round_path) as it:
            round_entries = [(e.name, e.is_dir()) for e in it]
    except (OSError, TypeError, ValueError):
        return [], '', "TIFF 탐색 실패: round_path 없음"

    # ① lot_name으로 해당 Lot 폴더 찾기
    lot_folder = None
    for name, folder in round_entries:
        if folder and (name == lot_name or lot_name in name or name in lot_name):
            lot_folder = os.path.join(round_path, name)
            break
    if not lot_folder:
        lot_folder = round_path

    # ② 루트 폴더의 파일만 검색 (Debug/Capture 하위폴더 제외)
    try:
        with os.scandir(lot_folder) as it:
            tiffs = [e.name for e in it
                     if e.name.lower().endswith(('.tif', '.tiff')) and e.is_file()]
    except OSError:
        tiffs = []

    matched = [os.path.normpath(os.path.join(lot_folder, f)) for f in tiffs if site_id in f]
    if not matched:
        site_num = re.sub(r'[^0-9]', '', site_id)
        if site_num:
            matched = [os.path.normpath(os.path.join(lot_folder, f))
                       for f in tiffs if site_num in f]
    return matched, lot_folder, ''


class TiffLoadThread(QThread):
    """더블클릭한 행의 TIFF를 찾고 읽는다. 네트워크 폴더에서 화면이 멈추지 않게 한다."""
    done = Signal(object)

    def __init__(self, round_path, lot_name, site_id, parent=None):
        super().__init__(parent)
        self.round_path, self.lot_name, self.site_id = round_path, lot_name, site_id

    def run(self):
        out = {'lot_name': self.lot_name, 'site_id': self.site_id, 'results': [],
               'failures': [], 'error': '', 'no_pspylib': False}
        try:
            out['matched'], out['lot_folder'], out['message'] = find_tiffs_for_row(
                self.round_path, self.lot_name, self.site_id)
            if out['matched']:
                try:
                    import pspylib.tiff.reader  # noqa: F401
                except ImportError:
                    out['no_pspylib'] = True
                else:
                    for tp in out['matched']:
                        try:
                            out['results'].append(load_tiff(tp))
                        except Exception as fe:
                            out['failures'].append((os.path.basename(tp), str(fe)))
        except Exception as e:
            out.setdefault('matched', [])
            out.setdefault('lot_folder', '')
            out.setdefault('message', '')
            out['error'] = f"{e}\n{traceback.format_exc()}"
        self.done.emit(out)


class TiffMixin:
    _tiff_thread = None

    def _on_row_double_click(self, row, col):
        lot_name = self.raw_table.item(row, 0)
        site_id = self.raw_table.item(row, 1)
        if not lot_name or not site_id:
            return
        lot_name, site_id = lot_name.text(), site_id.text()
        if self._tiff_thread is not None and self._tiff_thread.isRunning():
            self.statusBar().showMessage("TIFF를 읽는 중입니다. 끝나면 다시 시도하세요.")
            return

        round_path = ''
        if 0 <= self.current_recipe_idx < len(self.recipe_results):
            round_path = self.recipe_results[self.current_recipe_idx].get('round_path', '')
        if not round_path:
            self.logger.warn("TIFF 탐색 실패: round_path 없음")
            self.statusBar().showMessage(f"⚠ TIFF 없음: {lot_name}/{site_id} (log 확인)")
            return

        # 폴더 목록과 TIFF 읽기는 작업 스레드가 한다 (네트워크 폴더에서 화면이 멈추지 않게)
        self.statusBar().showMessage(f"TIFF 찾는 중... {lot_name}/{site_id}")
        thread = TiffLoadThread(round_path, lot_name, site_id)
        thread.done.connect(self._on_tiff_loaded)
        self._tiff_thread = thread
        thread.start()

    def _on_tiff_loaded(self, out):
        thread, self._tiff_thread = self._tiff_thread, None
        if thread is not None:
            thread.wait(2000)
        lot_name, site_id = out['lot_name'], out['site_id']
        if out['message']:
            self.logger.warn(out['message'])
        elif out['lot_folder']:
            self.logger.info(f"TIFF 탐색: {os.path.basename(out['lot_folder'])} / {site_id}")
        if out['error']:
            self.logger.error(f"TIFF 오류: {out['error'].splitlines()[0]}")
            self.logger.info(out['error'])
            self.statusBar().showMessage(f"⚠ TIFF 오류: {out['error'].splitlines()[0]}")
            return

        tiff_paths = out['matched']
        if not tiff_paths:
            if not out['message']:
                self.logger.warn(f"TIFF 없음: {lot_name}/{site_id} in {out['lot_folder']}")
            self.statusBar().showMessage(f"⚠ TIFF 없음: {lot_name}/{site_id} (log 확인)")
            return
        self.last_tiff_folder = os.path.normpath(out['lot_folder'])
        self.tiff_path_label.setText(self.last_tiff_folder)

        # pspylib 사전 확인
        if out['no_pspylib']:
            msg = "PSPylib 미설치: pip install pspylib-*.whl\n\nTIFF 런더링을 위해 PSPylib가 필요합니다."
            self.logger.error(msg.split('\n')[0])
            QMessageBox.warning(self, "PSPylib 없음", msg)
            return

        self.logger.info(f"TIFF 로드: {lot_name}/{site_id} ({len(tiff_paths)}개)")
        for p in tiff_paths:
            self.logger.info(f"  → {os.path.basename(p)}")
        for name, error in out['failures']:
            self.logger.warn(f"TIFF 로드 실패 [{name}]: {error}")

        results = out['results']
        if not results:
            self.statusBar().showMessage("⚠ TIFF 로드 실패")
            return
        try:
            # 모든 TIFF를 서브탭으로 표시
            self._tiff_viewer.set_results(results)
            self._show_tiff()
            self.statusBar().showMessage(f"✅ TIFF {len(results)}개 로드 완료: {lot_name}/{site_id}")
        except Exception as e:
            self.logger.error(f"TIFF 오류: {e}")
            self.logger.info(traceback.format_exc())
            self.statusBar().showMessage(f"⚠ TIFF 오류: {e}")



    def _show_tiff(self):
        """TIFF 탭으로 전환."""
        self._select_chart('🔬 TIFF')


    def _open_tiff_folder(self):
        folder = os.path.normpath(self.last_tiff_folder) if self.last_tiff_folder else ''
        if folder and os.path.isdir(folder):
            os.startfile(folder)
        elif folder:
            self.statusBar().showMessage(
                f"⚠ 폴더 없음: {folder}")
        else:
            self.statusBar().showMessage(
                "⚠ 먼저 '원본 데이터' 탭에서 행을 더블클릭하여 TIFF를 로드하세요.")

    # ──────────────────────────────────────────────
    # Export
    # ──────────────────────────────────────────────

