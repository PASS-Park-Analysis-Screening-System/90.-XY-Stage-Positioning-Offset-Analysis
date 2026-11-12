"""
diagnosis.py — 트러블슈팅 진단 탭의 drift 차트

Lot 평균(µm)을 Lot 시작 시각(첫 Lot 기준 경과 시간)에 대해 축별로 그린다.
시작 시각을 모르면 Lot 순서로 그린다.
"""
from datetime import datetime

from matplotlib.figure import Figure

_AXIS_STYLE = {'X': ('#2196F3', 'o'), 'Y': ('#D32F2F', 's')}


def _hours(lot_means):
    try:
        times = [datetime.fromisoformat(e['start']) for e in lot_means]
    except (TypeError, ValueError, KeyError):
        return None
    return [(t - times[0]).total_seconds() / 3600.0 for t in times]


def plot_drift(diagnosis: dict) -> Figure:
    """축별 Lot 평균 대 경과 시간. 제목에 Lot 평균 폭과 첫 Lot 안 기울기를 적는다.

    pyplot을 쓰지 않는다 (작업 스레드에서 만들어도 안전하다).
    """
    drift = (diagnosis or {}).get('drift') or {}
    lot_means = drift.get('lot_means') or []
    fig = Figure(figsize=(6, 4))
    axes = fig.subplots(2, 1, sharex=True)
    hours = _hours(lot_means) if lot_means else None
    xs = hours if hours is not None else list(range(1, len(lot_means) + 1))
    first = {w['axis']: w for w in drift.get('within_lot') or [] if w.get('lot') == drift.get('first_lot')}
    for ax, axis in zip(axes, ('X', 'Y')):
        color, marker = _AXIS_STYLE[axis]
        points = [(x, e.get(axis.lower())) for x, e in zip(xs, lot_means) if e.get(axis.lower()) is not None]
        if points:
            ax.plot([p[0] for p in points], [p[1] for p in points], marker + '-', color=color,
                    markersize=5, linewidth=1.0)
        span = (drift.get('span') or {}).get(axis)
        slope = (first.get(axis) or {}).get('slope_um_per_die')
        title = f'{axis} Lot 평균 (µm)'
        if span is not None:
            title += f' 폭 {span:.3f}'
        if slope is not None:
            title += f' · 첫 Lot {slope:+.4f}/Die'
        ax.set_title(title, fontsize=8)
        ax.grid(True, alpha=0.3)
        if not points:
            ax.text(0.5, 0.5, '데이터 없음', ha='center', va='center', transform=ax.transAxes,
                    color='#888888')
    axes[-1].set_xlabel('첫 Lot 시작 뒤 경과 (h)' if hours is not None else 'Lot 순서')
    fig.tight_layout()
    return fig
