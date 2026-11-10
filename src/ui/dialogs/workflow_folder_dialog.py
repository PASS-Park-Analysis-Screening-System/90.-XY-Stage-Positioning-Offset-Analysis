"""
workflow_folder_dialog.py — Workflow(Spec 이름)별 데이터 폴더 지정 대화상자

폴더명이 Spec 이름과 달라도 분석할 수 있게 Workflow마다 폴더를 고른다.
상태 문구·초기값·확인 가능 여부는 core.workflow_map이 정한다.

폴더 확인(네트워크 폴더는 수십 초)은 화면 스레드 밖의 작업 스레드 하나가 순서대로 한다.
대화상자는 바로 열리고, 확인 중인 행은 '확인 중…'으로 보였다가 결과가 오면 바뀐다.
결과는 ScanSession에 남아 확인 버튼과 이후 로드가 다시 읽지 않는다.
"""

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from core.csv_loader import ScanCancelled, ScanSession
from core.workflow_map import (NOTE_REMEMBERED, cached_status, clean_path, compute_prefill,
                               describe_folder, duplicate_folders, validate_rows)
from ui.theme import ACCENT, BG, BG3, FG, FG2, GREEN, ORANGE, RED

CHECKING = '확인 중…'


class _CheckBridge(QObject):
    """작업 스레드의 결과를 화면 스레드로 넘긴다 (queued 시그널)."""
    checked = Signal(str, str, object)     # (spec_key, 확인한 경로, (ok, text))
    prefilled = Signal(object)             # compute_prefill 결과
    progress = Signal(str, str, str)       # (spec_key, 확인한 경로, 진행 문구)


class WorkflowFolderDialog(QDialog):
    def __init__(self, parent=None, spec_keys=(), root='', remembered=None, reason='',
                 session=None, prefill=None):
        """prefill을 주면(스캔 스레드가 미리 계산) 그대로 쓰고, 없으면 열고 나서 계산한다."""
        super().__init__(parent)
        self.spec_keys = list(spec_keys)
        self.root = root or ''
        self.setWindowTitle("📂 Workflow별 데이터 폴더 선택")
        self.setMinimumSize(820, 300)
        self.setStyleSheet(f"background: {BG}; color: {FG};")
        # 폴더 확인 결과는 세션에 남는다 (확인 버튼과 이후 로드가 다시 읽지 않는다)
        self._session = session if session is not None else ScanSession()
        self._checker = self._session.fork()     # 대화상자를 닫으면 이 확인만 멈춘다
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='workflow-check')
        self._bridge = _CheckBridge(self)
        self._bridge.checked.connect(self._on_checked)
        self._bridge.prefilled.connect(self._apply_prefill)
        self._bridge.progress.connect(self._on_check_progress)
        self._rows = {}     # spec_key → (checkbox, path_edit, status_label, timer)
        self._notes = {}    # spec_key → 초기값 안내 문구 (경로를 바꾸면 지운다)
        self._pending = {}  # spec_key → 결과를 기다리는 경로
        self._queued = set()            # 작업 스레드에 넘긴 (spec_key, 경로)
        self._touched = set()           # 사용자가 바꾼 행 (늦게 온 초기값이 덮지 않는다)
        self._accept_requested = False  # 확인을 눌렀는데 확인 중인 행이 있다
        self._prefill_pending = prefill is None
        if prefill is None:
            prefill = self._provisional_prefill(remembered)
            remembered_copy = dict(remembered) if isinstance(remembered, dict) else {}
            self._pool.submit(self._run_prefill, remembered_copy)
        self.finished.connect(self._stop_checks)
        self._build_ui(reason, prefill)
        self._refresh_ok()

    # ── 작업 스레드 ──────────────────────────────────────

    def _provisional_prefill(self, remembered):
        """초기값을 계산하는 동안 보여 줄 값: 지난번 경로만 채우고 체크하지 않는다."""
        remembered = remembered if isinstance(remembered, dict) else {}
        prefill = {}
        for key in self.spec_keys:
            entry = remembered.get(key)
            path = entry.get('path') if isinstance(entry, dict) else ''
            path = path if isinstance(path, str) else ''
            prefill[key] = {'path': path, 'enabled': False,
                            'note': NOTE_REMEMBERED if path else ''}
        return prefill

    def _run_prefill(self, remembered):
        try:
            prefill = compute_prefill(self.spec_keys, self.root, remembered, self._checker)
        except ScanCancelled:
            return
        except Exception:
            prefill = None
        self._emit(self._bridge.prefilled, prefill)

    def _run_check(self, key, path):
        session = self._checker
        session.done = session.total = 0
        session.progress = lambda done, total, _p: self._emit(
            self._bridge.progress, key, path, f'{CHECKING} {done}/{total}')
        try:
            status = describe_folder(path, session)
        except ScanCancelled:
            return
        except Exception as e:
            status = (False, f'❌ 확인 실패 ({type(e).__name__})')
        finally:
            session.progress = None
        self._emit(self._bridge.checked, key, path, status)

    @staticmethod
    def _emit(signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:
            pass    # 대화상자가 이미 닫혀 지워졌다

    def _stop_checks(self, *_args):
        self._checker.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ── 화면 ────────────────────────────────────────────

    def _build_ui(self, reason, prefill):
        layout = QVBoxLayout(self)

        title = QLabel("Workflow별 데이터 폴더")
        title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {ACCENT};")
        layout.addWidget(title)

        intro = QLabel((reason + "\n\n" if reason else "")
                       + "사용에 체크한 Workflow만 분석합니다. 체크하지 않은 Workflow는 건너뜁니다.\n"
                         "'지난번 선택'과 선택한 폴더는 어느 Workflow인지 확인한 뒤 체크하세요.\n"
                         "Lot(Repeat) 수는 참고용이며 분석을 막지 않습니다.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {FG2}; font-size: 9pt;")
        layout.addWidget(intro)

        grid = QGridLayout()
        for col, text in enumerate(("사용", "Workflow", "데이터 폴더", "", "상태")):
            head = QLabel(text)
            head.setStyleSheet(f"color: {FG2}; font-weight: bold; font-size: 9pt;")
            grid.addWidget(head, 0, col)

        edit_style = (f"background: {BG3}; color: {FG}; border: 1px solid #585b70; "
                      f"border-radius: 4px; padding: 3px 6px;")
        for row, key in enumerate(self.spec_keys, start=1):
            check = QCheckBox()
            check.setChecked(prefill[key]['enabled'])
            name = QLabel(key)
            name.setStyleSheet("font-weight: bold;")
            edit = QLineEdit(prefill[key]['path'])
            edit.setMinimumWidth(380)
            edit.setStyleSheet(edit_style)
            browse = QPushButton("찾아보기")
            status = QLabel()
            status.setMinimumWidth(220)
            status.setWordWrap(True)
            # 네트워크 폴더는 읽는 데 시간이 걸리므로 입력이 멈춘 뒤 그 행만 확인한다
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(300)
            timer.timeout.connect(lambda k=key: self._update_status(k))

            check.toggled.connect(lambda _checked, k=key: self._on_check_toggled(k))
            edit.textChanged.connect(lambda _text, k=key, t=timer: self._on_text_changed(k, t))
            browse.clicked.connect(lambda _=False, k=key: self._browse(k))

            grid.addWidget(check, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(edit, row, 2)
            grid.addWidget(browse, row, 3)
            grid.addWidget(status, row, 4)
            self._rows[key] = (check, edit, status, timer)
            self._notes[key] = prefill[key].get('note', '')
            if self._prefill_pending:
                self._show_checking(key)
            else:
                self._update_status(key, refresh=False)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)

        self.warn_label = QLabel()
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet(f"color: {ORANGE}; font-size: 9pt;")
        layout.addWidget(self.warn_label)

        self.block_label = QLabel()
        self.block_label.setWordWrap(True)
        self.block_label.setStyleSheet(f"color: {RED}; font-size: 9pt;")
        layout.addWidget(self.block_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("분석 시작")
        self.buttons.button(QDialogButtonBox.Cancel).setText("취소")
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _apply_prefill(self, prefill):
        """늦게 온 초기값을 채운다. 사용자가 이미 바꾼 행은 그대로 둔다."""
        self._prefill_pending = False
        for key in self.spec_keys:
            check, edit, _status, _timer = self._rows[key]
            if prefill is not None and key not in self._touched:
                values = prefill[key]
                for widget in (check, edit):
                    widget.blockSignals(True)
                edit.setText(values['path'])
                check.setChecked(values['enabled'])
                for widget in (check, edit):
                    widget.blockSignals(False)
                self._notes[key] = values.get('note', '')
            self._update_status(key, refresh=False)
        self._refresh_ok()
        self._accept_if_requested()

    def _on_check_toggled(self, key):
        self._touched.add(key)
        self._refresh_ok()

    def _on_text_changed(self, key, timer):
        self._notes[key] = ''
        self._touched.add(key)
        path = clean_path(self._rows[key][1].text())
        if cached_status(path, self._session) is not None:
            timer.stop()
            self._update_status(key)
            return
        # 입력이 멈춘 뒤 확인한다. 그때까지 이 행은 확인 중이라 분석을 시작할 수 없다
        self._pending[key] = path
        self._show_checking(key)
        timer.start()
        self._refresh_ok()

    def _browse(self, key):
        _check, edit, _status, timer = self._rows[key]
        start = clean_path(edit.text()) or self.root
        path = QFileDialog.getExistingDirectory(self, f"{key} 데이터 폴더 선택", start)
        if path:
            edit.setText(path)
            timer.stop()
            self._update_status(key)

    def _show_checking(self, key, text=CHECKING):
        status = self._rows[key][2]
        status.setText(text)
        status.setStyleSheet(f"color: {FG2};")

    def _show_status(self, key, ok, text):
        _check, edit, status, _timer = self._rows[key]
        note = self._notes.get(key)
        status.setText(f"{text} ({note})" if note else text)
        status.setStyleSheet(
            f"color: {GREEN if ok else (RED if clean_path(edit.text()) else FG2)};")

    def _update_status(self, key, refresh=True):
        """행 상태를 보인다. 아직 확인하지 않은 폴더는 작업 스레드에 넘기고 '확인 중…'."""
        path = clean_path(self._rows[key][1].text())
        known = cached_status(path, self._session)
        if known is None:
            self._pending[key] = path
            self._show_checking(key)
            if (key, path) not in self._queued:
                self._queued.add((key, path))
                self._pool.submit(self._run_check, key, path)
        else:
            self._pending.pop(key, None)
            self._show_status(key, *known)
        if refresh:
            self._refresh_ok()

    def _on_check_progress(self, key, path, text):
        if self._pending.get(key) == path:
            self._show_checking(key, text)

    def _on_checked(self, key, path, status):
        self._queued.discard((key, path))
        if self._pending.get(key) != path:
            return      # 그사이 경로를 바꿨다. 결과는 세션에 남아 다음에 쓴다
        if self._rows[key][3].isActive():
            return      # 같은 경로를 다시 입력하는 중이다. 입력이 멈추면 세션에서 바로 읽는다
        self._pending.pop(key, None)
        self._show_status(key, *status)
        self._refresh_ok()
        self._accept_if_requested()

    def _rows_state(self):
        return [(key, check.isChecked(), clean_path(edit.text()))
                for key, (check, edit, _status, _timer) in self._rows.items()]

    def _waiting_rows(self):
        """체크했는데 아직 확인 결과가 없는 행."""
        return [key for key, enabled, _path in self._rows_state()
                if enabled and key in self._pending]

    def _refresh_ok(self):
        ok_btn = self.buttons.button(QDialogButtonBox.Ok)
        rows = self._rows_state()
        waiting = self._waiting_rows()
        if self._prefill_pending or waiting:
            ok_btn.setEnabled(False)
            self.block_label.setText(
                '폴더 확인 중…' if self._prefill_pending
                else f"{CHECKING} {', '.join(waiting)} — 끝나면 분석을 시작할 수 있습니다")
            self.warn_label.setText(' / '.join(
                f"⚠ 같은 폴더를 여러 Workflow에 지정했습니다: {', '.join(keys)}"
                for keys in duplicate_folders(rows)))
            return
        # 체크한 행은 모두 확인했으므로 세션에서 읽기만 한다 (파일을 다시 읽지 않는다)
        ok, reasons, warnings = validate_rows(rows, self._session)
        ok_btn.setEnabled(ok)
        self.block_label.setText('' if ok else '확인할 수 없습니다: ' + ' / '.join(reasons))
        self.warn_label.setText(' / '.join(f'⚠ {w}' for w in warnings))

    def _accept_if_valid(self):
        """확인한 결과를 그대로 쓴다. 입력 대기 중이거나 확인 중인 행만 마저 확인한다."""
        for key, (_check, _edit, _status, timer) in self._rows.items():
            if timer.isActive():
                timer.stop()
                self._update_status(key, refresh=False)
        self._refresh_ok()
        if self._prefill_pending or self._waiting_rows():
            self._accept_requested = True
            return
        self._accept_requested = False
        if self.buttons.button(QDialogButtonBox.Ok).isEnabled():
            self.accept()

    def _accept_if_requested(self):
        if self._accept_requested and not (self._prefill_pending or self._waiting_rows()):
            self._accept_if_valid()

    def warnings(self):
        """막지 않는 경고 (예: 같은 폴더를 두 Workflow에 지정)."""
        return validate_rows(self._rows_state(), self._session)[2]

    def selected_mapping(self):
        """사용에 체크한 Workflow의 [(spec_key, path), ...] (Spec 순서)."""
        return [(key, path) for key, enabled, path in self._rows_state() if enabled and path]

    def folder_map(self):
        """설정에 저장할 전체 지정 내역 {spec_key: {'path': str, 'enabled': bool}}."""
        return {key: {'path': path, 'enabled': enabled}
                for key, enabled, path in self._rows_state()}
