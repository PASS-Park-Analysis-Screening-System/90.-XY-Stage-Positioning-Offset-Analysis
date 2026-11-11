"""
color_helpers.py — 히트맵/테이블 셀 색상 유틸리티
"""

from PySide6.QtGui import QColor


def _heatmap_diverging(ratio: float) -> QColor:
    """양극 색상: -1(파랑) ~ 0(흰) ~ +1(빨강)"""
    ratio = max(-1.0, min(1.0, ratio))
    if ratio >= 0:
        r, g, b = 255, int(255*(1-ratio)), int(235*(1-ratio))
    else:
        r, g, b = int(255*(1+ratio)), int(230*(1+ratio)), 255
    return QColor(r, g, b)


def _heatmap_single(ratio: float) -> QColor:
    """단색 그라데이션: 0(밝음) ~ 1(Steel Blue)"""
    ratio = max(0.0, min(1.0, ratio))
    r = int(240 - (240-58)*ratio)
    g = int(244 - (244-122)*ratio)
    b = int(248 - (248-189)*ratio)
    return QColor(r, g, b)


def _contrast_fg(bg: QColor) -> QColor:
    """배경색 명도에 따라 검은색/흰색 글자 선택"""
    lum = 0.299*bg.red() + 0.587*bg.green() + 0.114*bg.blue()
    return QColor('#1e1e2e') if lum > 140 else QColor('#ffffff')


def verdict_color(result, reference: bool = False):
    """판정 색 (단일 규칙): PASS 초록, 참고 PASS 주황, FAIL·참고 FAIL 빨강. 판정 없음은 None.

    카드·Step 버튼·Summary·회차 비교 표가 같은 규칙을 쓴다 (1 Repeat 참고 판정).
    """
    from ui.theme import GREEN, ORANGE, RED
    if result is None:
        return None
    if not result:
        return RED
    return ORANGE if reference else GREEN
