import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import io
import platform

from core import drop_non_finite

# ── 한국어 폰트 설정 ──
if platform.system() == 'Windows':
    plt.rcParams['font.family'] = 'Malgun Gothic'
elif platform.system() == 'Darwin':
    plt.rcParams['font.family'] = 'AppleGothic'
else:
    plt.rcParams['font.family'] = 'NanumGothic'
plt.rcParams['axes.unicode_minus'] = False

RECIPE_COLORS = ['#2196F3', '#4CAF50', '#FF9800', '#E91E63',
                 '#9C27B0', '#00BCD4', '#795548', '#607D8B']

def _fig_to_png(fig: Figure) -> bytes:
    """Figure → PNG bytes 변환 유틸. app.py(JSON-RPC)에서 사용.

    GUI에서는 FigureCanvasQTAgg(fig)를 직접 사용하므로 이 함수를 호출하지 않습니다.
    이 함수는 Figure를 close합니다.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def plot_trend_chart(trend_data: list, title: str = 'Lot Trend',
                     y_label: str = 'HZ1_O (nm)',
                     show_range: bool = True) -> Figure:
    """Lot별 측정값 트렌드 차트"""
    fig, ax = plt.subplots(figsize=(12, 6))

    indices = [t['lot_index'] for t in trend_data]
    means = [t['mean'] for t in trend_data]
    stdevs = [t['stdev'] for t in trend_data]
    labels = [t['lot_name'] for t in trend_data]

    ax.plot(indices, means, 'o-', color='#2196F3', linewidth=2,
            markersize=6, label='Mean', zorder=3)

    upper = [m + s for m, s in zip(means, stdevs)]
    lower = [m - s for m, s in zip(means, stdevs)]
    ax.fill_between(indices, lower, upper, alpha=0.15, color='#2196F3',
                    label='±1σ')

    if show_range:
        mins = [t['min'] for t in trend_data]
        maxs = [t['max'] for t in trend_data]
        ax.plot(indices, mins, 'v:', color='#FF5722', alpha=0.5,
                markersize=4, label='Min')
        ax.plot(indices, maxs, '^:', color='#4CAF50', alpha=0.5,
                markersize=4, label='Max')

    overall_mean = sum(means) / len(means)
    ax.axhline(overall_mean, color='gray', linestyle='--', alpha=0.5,
               label=f'Overall Mean: {overall_mean:.1f}')

    ax.set_xlabel('Lot Index')
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.set_xticks(indices)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax.legend(loc='best', fontsize=8)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig



def _extract_site_data(data: list, metric_key: str, method: str):
    from core.statistics import is_usable
    """히트맵/컨투어 공통 — Site별 평균값 추출"""
    site_vals = {}
    for r in data:
        if r.get('method', '').upper() != method.upper():
            continue
        sx, sy = r.get('site_x', 0), r.get('site_y', 0)
        if not is_usable(r, metric_key):
            continue
        val = r[metric_key]
        key = (sx, sy)
        if key not in site_vals:
            site_vals[key] = {'values': [], 'site_id': r.get('site_id', '')}
        site_vals[key]['values'].append(val)

    xs = [k[0] for k in site_vals.keys()]
    ys = [k[1] for k in site_vals.keys()]
    means = [sum(v['values']) / len(v['values']) for v in site_vals.values()]

    return xs, ys, means, site_vals



def plot_site_heatmap(data: list, metric_key: str = 'value',
                      method: str = 'X',
                      title: str = 'Site Heatmap',
                      mode: str = 'scatter') -> Figure:
    """Site 좌표 기반 히트맵/컨투어맵

    Args:
        mode: 'scatter' (산점도) 또는 'contour' (등고선)
    """
    fig, ax = plt.subplots(figsize=(10, 8))

    xs, ys, means, site_vals = _extract_site_data(data, metric_key, method)

    if not xs:
        ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                transform=ax.transAxes, fontsize=14)
        return fig

    if mode == 'contour' and len(xs) >= 4:
        # 등고선 보간
        from scipy.interpolate import griddata

        xi = np.linspace(min(xs) - 1, max(xs) + 1, 100)
        yi = np.linspace(min(ys) - 1, max(ys) + 1, 100)
        Xi, Yi = np.meshgrid(xi, yi)

        Zi = griddata((xs, ys), means, (Xi, Yi), method='cubic')

        cf = ax.contourf(Xi, Yi, Zi, levels=20, cmap='RdYlGn_r', alpha=0.8)
        plt.colorbar(cf, ax=ax, label=f'Mean {metric_key}')

        # 등고선 라인
        cs = ax.contour(Xi, Yi, Zi, levels=10, colors='gray',
                        linewidths=0.5, alpha=0.5)
        ax.clabel(cs, inline=True, fontsize=6, fmt='%.0f')

        # 원래 데이터 포인트도 표시
        ax.scatter(xs, ys, c=means, s=80, cmap='RdYlGn_r',
                   edgecolors='black', linewidths=0.8, zorder=3)

        mode_label = 'Contour'
    else:
        # 산점도 (기존)
        scatter = ax.scatter(xs, ys, c=means, s=200, cmap='RdYlGn_r',
                             edgecolors='black', linewidths=0.5, zorder=3)
        plt.colorbar(scatter, ax=ax, label=f'Mean {metric_key}')
        mode_label = 'Scatter'

    # 값 라벨
    for (x, y), val_info in site_vals.items():
        mean_val = sum(val_info['values']) / len(val_info['values'])
        ax.annotate(f'{mean_val:.0f}', (x, y), textcoords="offset points",
                    xytext=(0, 12), ha='center', fontsize=7)

    ax.set_xlabel('Site X')
    ax.set_ylabel('Site Y')
    ax.set_title(f'{title} [{mode_label}] (Method: {method})')
    ax.grid(True, alpha=0.3)
    ax.set_aspect('equal')

    fig.tight_layout()
    return fig



def plot_boxplot(data: list, metric_key: str = 'value',
                 group_by: str = 'lot_name',
                 title: str = 'Distribution by Lot') -> Figure:
    """그룹별 박스플롯"""
    fig, ax = plt.subplots(figsize=(12, 6))

    groups = {}
    for r in data:
        key = r.get(group_by, 'Unknown')
        val = r.get(metric_key)
        if isinstance(val, (int, float)):
            if key not in groups:
                groups[key] = []
            groups[key].append(val)

    sorted_keys = sorted(groups.keys())
    box_data = [groups[k] for k in sorted_keys]

    bp = ax.boxplot(box_data, labels=sorted_keys, patch_artist=True,
                    boxprops=dict(facecolor='#E3F2FD', edgecolor='#1565C0'),
                    medianprops=dict(color='#D32F2F', linewidth=2),
                    flierprops=dict(marker='o', markerfacecolor='#FF5722',
                                    markersize=4))

    ax.set_xlabel(group_by.replace('_', ' ').title())
    ax.set_ylabel(metric_key)
    ax.set_title(title)
    ax.tick_params(axis='x', rotation=45)
    ax.grid(True, alpha=0.3, axis='y')

    fig.tight_layout()
    return fig



def plot_histogram(data: list, metric_key: str = 'value',
                   bins: int = 50,
                   title: str = 'Distribution') -> Figure:
    """측정값 히스토그램 + 정규분포 곡선"""
    fig, ax = plt.subplots(figsize=(10, 6))

    # 비유한 값(NaN/inf)이 하나라도 섞이면 ax.hist의 범위 자동 감지가 실패한다.
    values = drop_non_finite(
        [r.get(metric_key, 0) for r in data
         if isinstance(r.get(metric_key), (int, float))], title)

    if not values:
        return fig

    ax.hist(values, bins=bins, color='#42A5F5', edgecolor='white',
            alpha=0.8, density=True, label='Data')

    mean = sum(values) / len(values)
    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
    if std > 0:
        x = np.linspace(min(values), max(values), 200)
        y = (1 / (std * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x - mean) / std) ** 2)
        ax.plot(x, y, 'r-', linewidth=2, label=f'Normal (μ={mean:.1f}, σ={std:.1f})')

    ax.axvline(mean, color='#D32F2F', linestyle='--', alpha=0.7)
    ax.set_xlabel(metric_key)
    ax.set_ylabel('Density')
    ax.set_title(title)
    ax.legend()
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig



