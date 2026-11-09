"""
workflow_folder_dialog.py — Workflow(Spec 이름)별 데이터 폴더 지정 대화상자

폴더명이 Spec 이름과 달라도 분석할 수 있게 Workflow마다 폴더를 고른다.
상태 문구·초기값·확인 가능 여부는 core.workflow_map이 정한다.
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from core.workflow_map import compute_prefill, describe_folder, validate_rows
from ui.theme import ACCENT, BG, BG3, FG, FG2, GREEN, RED


class WorkflowFolderDialog(QDialog):
    def __init__(self, parent=None, spec_keys=(), root='', remembered=None, reason=''):
        super().__init__(parent)
        self.spec_keys = list(spec_keys)
        self.root = root or ''
        self.setWindowTitle("📂 Workflow별 데이터 폴더 선택")
        self.setMinimumSize(820, 300)
        self.setStyleSheet(f"background: {BG}; color: {FG};")
        self._rows = {}     # spec_key → (checkbox, path_edit, status_label, timer)
        self._build_ui(reason, compute_prefill(self.spec_keys, self.root, remembered))
        self._refresh_ok()

    def _build_ui(self, reason, prefill):
        layout = QVBoxLayout(self)

        title = QLabel("Workflow별 데이터 폴더")
        title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {ACCENT};")
        layout.addWidget(title)

        intro = QLabel((reason + "\n\n" if reason else "")
                       + "사용에 체크한 Workflow만 분석합니다. 체크하지 않은 Workflow는 건너뜁니다.\n"
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
            status.setMinimumWidth(200)
            # 네트워크 폴더는 읽는 데 시간이 걸리므로 입력이 멈춘 뒤에 확인한다
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(300)
            timer.timeout.connect(lambda k=key: self._update_status(k))

            check.toggled.connect(lambda _checked: self._refresh_ok())
            edit.textChanged.connect(lambda _text, t=timer: t.start())
            browse.clicked.connect(lambda _=False, k=key: self._browse(k))

            grid.addWidget(check, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(edit, row, 2)
            grid.addWidget(browse, row, 3)
            grid.addWidget(status, row, 4)
            self._rows[key] = (check, edit, status, timer)
            self._update_status(key, refresh=False)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)

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

    def _browse(self, key):
        _check, edit, _status, timer = self._rows[key]
        start = edit.text().strip() or self.root
        path = QFileDialog.getExistingDirectory(self, f"{key} 데이터 폴더 선택", start)
        if path:
            edit.setText(path)
            timer.stop()
            self._update_status(key)

    def _update_status(self, key, refresh=True):
        _check, edit, status, _timer = self._rows[key]
        ok, text = describe_folder(edit.text())
        status.setText(text)
        status.setStyleSheet(f"color: {GREEN if ok else (RED if edit.text().strip() else FG2)};")
        if refresh:
            self._refresh_ok()

    def _rows_state(self):
        return [(key, check.isChecked(), edit.text().strip())
                for key, (check, edit, _status, _timer) in self._rows.items()]

    def _refresh_ok(self):
        ok, reasons = validate_rows(self._rows_state())
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)
        self.block_label.setText('' if ok else '확인할 수 없습니다: ' + ' / '.join(reasons))

    def _accept_if_valid(self):
        # 입력 직후 바로 누르면 상태 확인 타이머가 아직 돌지 않았을 수 있다
        for key, (_check, _edit, _status, timer) in self._rows.items():
            if timer.isActive():
                timer.stop()
                self._update_status(key, refresh=False)
        self._refresh_ok()
        if self.buttons.button(QDialogButtonBox.Ok).isEnabled():
            self.accept()

    def selected_mapping(self):
        """사용에 체크한 Workflow의 [(spec_key, path), ...] (Spec 순서)."""
        return [(key, path) for key, enabled, path in self._rows_state() if enabled and path]

    def folder_map(self):
        """설정에 저장할 전체 지정 내역 {spec_key: {'path': str, 'enabled': bool}}."""
        return {key: {'path': path, 'enabled': enabled}
                for key, enabled, path in self._rows_state()}
