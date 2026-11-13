"""
fit_table.py — 행 수만큼 높이를 맞추는 표 (전문 분석 창: 세로 스크롤 없이 한 화면에)
"""
from PySide6.QtWidgets import QAbstractScrollArea, QHeaderView, QSizePolicy, QTableWidget


class FitTable(QTableWidget):
    """빈 줄 없이 행 수만큼의 높이 — 행이 다 들어가면 안쪽 세로 스크롤이 없다.

    열은 내용 폭이고 stretch 열(근거·글)만 남는 폭을 쓴다. wrap이면 긴 글을 줄바꿈하고 행 높이를 내용에 맞춘다
    (폭이 바뀌면 다시 맞춘다). 자리가 모자랄 때만 min_rows 행 높이까지 줄어들며 그때 안쪽 스크롤이 생긴다.
    행을 채운 뒤에는 fit()을 부른다.
    """

    def __init__(self, headers, stretch=None, wrap=False, min_rows=3, parent=None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        if stretch is not None:
            header.setSectionResizeMode(stretch, QHeaderView.Stretch)
        self.setWordWrap(wrap)
        self.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._min_rows = min_rows
        self._fitting = False
        self.horizontalScrollBar().rangeChanged.connect(lambda *_: self.fit())
        self.fit()

    def fit(self):
        """높이를 머리줄 + 모든 행(+ 가로 스크롤 막대)에 맞춘다."""
        if self._fitting:
            return
        self._fitting = True
        try:
            self.resizeRowsToContents()
            head = 0 if self.horizontalHeader().isHidden() else self.horizontalHeader().sizeHint().height()
            hbar = self.horizontalScrollBar()
            extra = head + 2 * self.frameWidth() + (hbar.sizeHint().height() if hbar.maximum() > 0 else 0)
            full = extra + self.verticalHeader().length()
            rows = [self.rowHeight(r) for r in range(min(self.rowCount(), self._min_rows))]
            self.setMaximumHeight(full)
            self.setMinimumHeight(min(full, extra + (sum(rows) if rows else 0)))
            self.updateGeometry()
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.wordWrap() and event.size().width() != event.oldSize().width():
            self.fit()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()
