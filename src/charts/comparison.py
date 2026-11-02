import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import io
import platform

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

from charts.basic import plot_boxplot, plot_trend_chart, _fig_to_png, _extract_site_data
from core import (compute_group_statistics, compute_trend,
                  filter_by_method, filter_valid_only, drop_non_finite,
                  filter_stabilization_die)

def plot_recipe_comparison_boxplot(recipe_results: list) -> Figure:
    """Recipe별 박스플롯 비교 (X/Y 분리, 2행)"""
    n = len(recipe_results)
    if n == 0:
        fig, ax = plt.subplots()
        return fig

    fig, axes = plt.subplots(2, n, figsize=(4 * n, 8), sharey='row')
    if n == 1:
        axes = axes.reshape(2, 1)

    for row, axis_name in enumerate(['X', 'Y']):
        for i, result in enumerate(recipe_results):
            ax = axes[row][i]
            data = filter_stabilization_die(result.get('raw_data', []))
            color = RECIPE_COLORS[i % len(RECIPE_COLORS)]

            # 측정 실패 행(Valid=FALSE)은 값이 NaN으로 기록된다. 거르지 않으면
            # np.mean/np.std가 nan이 되어 박스가 통째로 비어 보인다.
            values = drop_non_finite(
                [r.get('value', 0)
                 for r in filter_valid_only(filter_by_method(data, axis_name))
                 if isinstance(r.get('value'), (int, float))],
                f'{result.get("short_name", "?")} {axis_name} Boxplot')

            if values:
                bp = ax.boxplot([values], patch_artist=True,
                                boxprops=dict(facecolor=color + '40',
                                              edgecolor=color),
                                medianprops=dict(color='#D32F2F', linewidth=2),
                                flierprops=dict(marker='o', markerfacecolor='#FF5722',
                                                markersize=3))

            stats_vals = np.array(values) if values else np.array([0])
            ax.set_title(f'{result.get("short_name", "?")} ({axis_name})\n'
                         f'μ={np.mean(stats_vals):.0f}  σ={np.std(stats_vals):.0f}',
                         fontsize=9)
            ax.grid(True, alpha=0.3, axis='y')

        axes[row][0].set_ylabel(f'{axis_name} Offset (nm)')

    fig.suptitle('Recipe Comparison (X / Y)', fontsize=12, fontweight='bold')
    fig.tight_layout()
    return fig



def plot_recipe_comparison_trend(recipe_results: list) -> Figure:
    """Recipe별 트렌드 오버레이 (X/Y 분리, 2행)"""
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    for row, axis_name in enumerate(['X', 'Y']):
        ax = axes[row]
        for i, result in enumerate(recipe_results):
            # trend_x/trend_y 우선, 없으면 기존 trend 사용
            trend = result.get(f'trend_{axis_name.lower()}', [])
            if not trend:
                continue

            color = RECIPE_COLORS[i % len(RECIPE_COLORS)]
            indices = [t['lot_index'] for t in trend]
            means = [t['mean'] for t in trend]

            ax.plot(indices, means, 'o-', color=color, linewidth=2,
                    markersize=5, label=result.get('short_name', f'Recipe {i+1}'))

        ax.set_ylabel(f'{axis_name} Offset (nm)')
        ax.set_title(f'Recipe Trend Comparison — {axis_name}')
        ax.legend(loc='best', fontsize=8)
        ax.grid(True, alpha=0.3)

    axes[1].set_xlabel('Lot Index')
    fig.tight_layout()
    return fig



def plot_recipe_comparison_heatmap(recipe_results: list,
                                   method: str = 'X',
                                   mode: str = 'scatter') -> Figure:
    """Recipe별 히트맵/컨투어 2×2 그리드"""
    n = len(recipe_results)
    cols = min(n, 2)
    rows = max(1, (n + 1) // 2)

    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 5 * rows))
    if n == 1:
        axes = np.array([[axes]])
    elif rows == 1:
        axes = axes.reshape(1, -1)

    for i, result in enumerate(recipe_results):
        r, c = divmod(i, cols)
        ax = axes[r][c]

        data = filter_valid_only(filter_stabilization_die(result.get('raw_data', [])))
        xs, ys, means, site_vals = _extract_site_data(data, 'value', method)

        if not xs:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    transform=ax.transAxes)
            ax.set_title(result.get('short_name', ''))
            continue

        if mode == 'contour' and len(xs) >= 4:
            try:
                from scipy.interpolate import griddata
                xi = np.linspace(min(xs) - 1, max(xs) + 1, 80)
                yi = np.linspace(min(ys) - 1, max(ys) + 1, 80)
                Xi, Yi = np.meshgrid(xi, yi)
                Zi = griddata((xs, ys), means, (Xi, Yi), method='cubic')
                cf = ax.contourf(Xi, Yi, Zi, levels=15, cmap='RdYlGn_r', alpha=0.8)
                fig.colorbar(cf, ax=ax, shrink=0.8)
                ax.scatter(xs, ys, c='black', s=15, zorder=3)
            except Exception:
                ax.scatter(xs, ys, c=means, s=100, cmap='RdYlGn_r',
                           edgecolors='black', linewidths=0.5)
        else:
            scatter = ax.scatter(xs, ys, c=means, s=100, cmap='RdYlGn_r',
                                 edgecolors='black', linewidths=0.5)
            fig.colorbar(scatter, ax=ax, shrink=0.8)

        ax.set_title(result.get('short_name', ''), fontsize=10)
        ax.set_aspect('equal')
        ax.grid(True, alpha=0.3)

    # 빈 subplot 숨기기
    for j in range(n, rows * cols):
        r, c = divmod(j, cols)
        axes[r][c].set_visible(False)

    fig.suptitle(f'Recipe Heatmap Comparison (Method: {method})',
                 fontsize=12, fontweight='bold')
    fig.tight_layout()
    return fig


# ──────────────────────────────────────────────
# Wafer-level Visualizations (ported from xy_stage_offset)
# ──────────────────────────────────────────────

from core import DIE_POSITIONS, get_die_position



