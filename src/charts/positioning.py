"""
positioning.py — 비전 보정 로그 대조 차트 (Positioning Log 탭)

Die Y(mm)에 대해 단계별 X 오차(도착 오차, 정밀 보정, 최종 착지)를 겹쳐 그린다.
최종 착지에만 기울기가 있으면 그 오차는 비전 정렬 이후에 생긴 것이다.
"""
from matplotlib.figure import Figure

from core.positioning_log import STAGE_LABEL, stage_series

_STAGE_STYLE = {
    'coarse_match': ('#2196F3', 'o'),
    'fine_move': ('#FF9800', 's'),
    'final': ('#D32F2F', 'D'),
}


def plot_positioning_stages(analysis: dict, axis: str = 'X') -> Figure:
    """단계별 오차(단계 평균을 뺀 µm) 대 Die Y(mm). 단계 표의 ∂/∂y 계수를 범례에 적는다.

    pyplot을 쓰지 않는다 (작업 스레드에서 만들어도 안전하다).
    """
    fig = Figure(figsize=(8, 5))
    ax = fig.add_subplot(111)
    series = stage_series(analysis, axis, tuple(_STAGE_STYLE))
    table = {(r['stage'], r['axis']): r for r in ((analysis or {}).get('mean') or {}).get('stages', [])}
    name = 'θx' if axis == 'X' else 'Sy'
    for stage, points in series.items():
        color, marker = _STAGE_STYLE[stage]
        ys = [p[0] for p in points]
        vs = [p[1] for p in points]
        coef = ((table.get((stage, axis)) or {}).get('coef') or {}).get(name)
        label = STAGE_LABEL[stage]
        if coef:
            t = coef['t']
            label += f" — {name} {coef['value']:+.2f}" + ('' if t is None else f' (t {t:+.1f})')
        ax.plot(ys, vs, marker, color=color, linestyle='none', markersize=6, label=label, zorder=3)
        n = len(ys)
        if n >= 2:
            my, mv = sum(ys) / n, sum(vs) / n
            sxx = sum((y - my) ** 2 for y in ys)
            if sxx > 0:
                slope = sum((y - my) * (v - mv) for y, v in zip(ys, vs)) / sxx
                lo, hi = min(ys), max(ys)
                ax.plot([lo, hi], [mv + slope * (lo - my), mv + slope * (hi - my)], '-',
                        color=color, linewidth=1.2, alpha=0.7)
    ax.axhline(0, color='#888888', linewidth=0.8)
    ax.set_xlabel('Die Y (mm)')
    ax.set_ylabel(f'{axis} 오차 - 단계 평균 (µm)')   # U+2212는 맑은 고딕에 없다
    ax.set_title(f'{axis} 오차 단계별 대 Die Y (Lot 평균, 선: Y에 대한 직선)', fontsize=10)
    ax.grid(True, alpha=0.3)
    if series:
        ax.legend(loc='best', fontsize=8)
    else:
        ax.text(0.5, 0.5, '로그 없음 — Lot 폴더 Debug/SamplePositioning 확인',
                ha='center', va='center', transform=ax.transAxes, color='#888888')
    fig.tight_layout()
    return fig
