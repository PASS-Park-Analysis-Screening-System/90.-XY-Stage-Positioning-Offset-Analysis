"""
stat_card.py — X/Y 축 통계 요약 카드
"""

import math

from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt
from ui.theme import BG2, FG, FG2, BG, GREEN, RED, ORANGE


class StatCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statcard")
        self.setStyleSheet(f"""
            #statcard {{ background: {BG2}; border: 1px solid #585b70;
                         border-radius: 6px; padding: 8px; }}
            #statcard QLabel {{ background: transparent; }}
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(2)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {FG2};")
        layout.addWidget(lbl_title)

        self.lbl_basis = QLabel("")
        self.lbl_basis.setStyleSheet(f"font-size: 8pt; color: {FG2};")
        layout.addWidget(self.lbl_basis)

        self.lbl_avg = self._row(layout, "Avg (nm):")
        self.lbl_rng, self.lbl_rng_spec = self._row_with_spec(layout, "Dev Range (µm):")
        self.lbl_std, self.lbl_std_spec = self._row_with_spec(layout, "Dev StdDev (µm):")
        self.lbl_cpk = self._row(layout, "Cpk:")

        self.badge = QLabel("—")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setStyleSheet(
            f"background: gray; color: {BG}; font-weight: bold; "
            f"padding: 4px; border-radius: 4px; font-size: 10pt;"
        )
        layout.addWidget(self.badge)

    def _row(self, layout, label_text):
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {FG2}; font-size: 9pt;")
        val = QLabel("—")
        val.setAlignment(Qt.AlignRight)
        val.setStyleSheet(f"color: {FG}; font-weight: bold; font-size: 12pt;")
        row.addWidget(lbl)
        row.addWidget(val)
        layout.addLayout(row)
        return val

    def _row_with_spec(self, layout, label_text):
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {FG2}; font-size: 9pt;")
        val = QLabel("—")
        val.setAlignment(Qt.AlignRight)
        val.setStyleSheet(f"color: {FG}; font-weight: bold; font-size: 12pt;")
        spec_lbl = QLabel("")
        spec_lbl.setAlignment(Qt.AlignRight)
        spec_lbl.setStyleSheet(f"font-size: 9pt; min-width: 90px;")
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(val)
        row.addWidget(spec_lbl)
        layout.addLayout(row)
        return val, spec_lbl

    def _format_spec_delta(self, value, spec):
        """Spec 대비 표기 생성: 초과=빨강▲, 여유=초록✓"""
        if value is None or spec is None or spec == 0:
            return "", ""
        ratio = value / spec * 100
        if value > spec:
            pct = ratio - 100
            text = f"/ {spec}  ▲+{pct:.0f}%"
            color = RED
        else:
            pct = 100 - ratio
            text = f"/ {spec}  ✓{pct:.0f}%"
            color = GREEN
        return text, color

    def set_basis(self, die_count: int, repeat_count: int, custom: bool = False):
        """카드 값이 어떤 Die × Repeat로 계산됐는지 표시한다."""
        text = f"{die_count} Die × {repeat_count} Repeat"
        if custom:
            text += "  ⚠ Custom"
        self.lbl_basis.setText(text)
        self.lbl_basis.setToolTip(
            "Die 필터가 기본과 다릅니다. 이 카드와 표·그래프만 적용되며 "
            "Summary·Step 버튼·Lot Trend·내보내기는 분석 기준으로 고정됩니다." if custom else "")
        self.lbl_basis.setStyleSheet(
            f"font-size: 8pt; color: {ORANGE if custom else FG2};"
            + (" font-weight: bold;" if custom else ""))

    def clear_stats(self):
        """값, 판정, 분석 기준을 지운다."""
        self.update_stats(None, None, None, None)
        self.lbl_basis.setText("")
        self.lbl_basis.setToolTip("")

    def update_stats(self, avg, rng, std, cpk, is_pass: bool = None,
                     spec_r=None, spec_s=None, warned: bool = False,
                     reference: bool = False):
        """reference: 1 Repeat 참고 판정 — 판정 값은 보이되 ⚠와 '1 Repeat 참고 판정'을 붙인다."""
        reference = bool(reference) and is_pass is not None
        # 측정이 없는 축은 값이 None이다
        self.lbl_avg.setText("—" if avg is None else f"{avg:.3f}")
        self.lbl_rng.setText("—" if rng is None else f"{rng:.3f}")
        self.lbl_std.setText("—" if std is None else f"{std:.3f}")
        # Cpk: 계산 불가(σ=0 등)는 compute_cpk가 NaN을 반환 → 'N/A'로 표시하여
        # 실제 Cpk 0.00(평균이 규격 한계에 위치)과 혼동되지 않게 한다.
        # None은 판정하지 않은 축이다 → 판정과 같은 '—'.
        cpk_unavailable = isinstance(cpk, float) and math.isnan(cpk)
        self.lbl_cpk.setText("—" if cpk is None else "N/A" if cpk_unavailable else f"{cpk:.2f}")
        if reference and cpk is not None:
            self.lbl_cpk.setText(self.lbl_cpk.text() + " (참고)")
        self.lbl_cpk.setToolTip("1 Repeat이라 Cpk도 참고값입니다" if reference else "")

        # Spec 대비 표기. 판정하지 않은 값에는 ✓나 ▲를 붙이지 않는다
        if is_pass is None and spec_r is not None and spec_s is not None:
            spec_r = spec_s = None
        rng_text, rng_color = self._format_spec_delta(rng, spec_r)
        self.lbl_rng_spec.setText(rng_text)
        self.lbl_rng_spec.setStyleSheet(
            f"font-size: 9pt; color: {rng_color}; min-width: 90px;")

        std_text, std_color = self._format_spec_delta(std, spec_s)
        self.lbl_std_spec.setText(std_text)
        self.lbl_std_spec.setStyleSheet(
            f"font-size: 9pt; color: {std_color}; min-width: 90px;")

        self.badge.setToolTip(
            "Repeat이 1개라 Die 간 편차로만 판정했습니다. 반복성은 알 수 없어 참고용입니다 "
            "(정식 판정은 2 Repeat 이상)." if reference else "")
        if is_pass is None:
            self.badge.setText("—")
            self.badge.setStyleSheet(
                f"background: gray; color: {BG}; font-weight: bold; "
                f"padding: 4px; border-radius: 4px; font-size: 10pt;"
            )
        elif reference:
            # 1.1.3의 '⚠ PASS'와 같은 표기에 이유를 붙인다. 색은 판정 색 (PASS는 경고 주황)
            self.badge.setText(f"⚠ {'PASS' if is_pass else 'FAIL'} · 1 Repeat 참고 판정")
            self.badge.setStyleSheet(
                f"background: {ORANGE if is_pass else RED}; color: {BG}; font-weight: bold; "
                f"padding: 4px; border-radius: 4px; font-size: 10pt;"
            )
        elif is_pass:
            self.badge.setText("⚠ PASS" if warned else "✅ PASS")
            self.badge.setStyleSheet(
                f"background: {ORANGE if warned else GREEN}; color: {BG}; font-weight: bold; "
                f"padding: 4px; border-radius: 4px; font-size: 10pt;"
            )
        else:
            self.badge.setText("❌ FAIL")
            self.badge.setStyleSheet(
                f"background: {RED}; color: {BG}; font-weight: bold; "
                f"padding: 4px; border-radius: 4px; font-size: 10pt;"
            )
