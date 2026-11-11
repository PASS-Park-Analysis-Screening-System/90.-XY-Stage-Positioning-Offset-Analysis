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

from core import DIE_POSITIONS, get_die_position
from core.die_analysis import DIE_PITCH_MM
import math
from matplotlib.patches import Circle
from matplotlib.colors import Normalize
from scipy.interpolate import griddata
from matplotlib.patheffects import withStroke
from scipy.spatial import cKDTree

def _hsl_to_rgb(h, s, l):
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:    r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return (r + m, g + m, b + m)



def _color_from_die(die_idx, total=21):
    hue = (die_idx % total) * (360.0 / total)
    return _hsl_to_rgb(hue, 0.6, 0.5)



def _color_from_die_hex(die_idx, total=21):
    """Die 인덱스 → '#RRGGBB' hex 문자열 (pyqtgraph/Qt 호환)."""
    r, g, b = _color_from_die(die_idx, total)
    return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'



def plot_wafer_contour(die_stats: list, title: str = 'Wafer Contour',
                       vmin: float = 0.0, vmax: float = 1.0,
                       wafer_radius_um: float = 150_000,
                       dynamic_positions: dict = None) -> Figure:
    """Die 좌표계 기반 원형 Wafer Contour Map (µm 단위).

    Args:
        die_stats: [{'die': 'Die1', 'avg': ...}, ...]
        wafer_radius_um: 웨이퍼 반경 (µm) — 200mm=100000, 300mm=150000
        dynamic_positions: 데이터 기반 동적 Die 좌표 dict
    """
    from scipy.interpolate import griddata
    from matplotlib.patches import Circle
    from matplotlib.colors import Normalize

    positions, values, die_labels = [], [], []
    for ds in die_stats:
        pos = get_die_position(ds['die'], dynamic_positions)
        if pos is not None:
            # mm → µm 변환
            positions.append((pos[0] * 1000, pos[1] * 1000))
            values.append(abs(ds['avg']))
            die_labels.append(ds['die'])

    fig, ax = plt.subplots(figsize=(7, 7))
    fig.patch.set_facecolor('#1e1e2e')
    ax.set_facecolor('#262637')

    if len(positions) < 3:
        ax.text(0.5, 0.5, 'Insufficient Die data', ha='center', va='center',
                transform=ax.transAxes, fontsize=14, color='#cdd6f4')
        return fig

    xs = np.array([p[0] for p in positions], dtype=float)
    ys = np.array([p[1] for p in positions], dtype=float)
    zs = np.array(values, dtype=float)

    # ── 원형 보정: 외곽 Die 기반 경계에 가상 포인트 추가 ──
    # 측정 영역만 커버 (웨이퍼 엣지까지 확장하지 않음)
    from scipy.spatial import cKDTree
    data_r = float(np.sqrt(xs**2 + ys**2).max()) + 5000  # 외곽 Die + 5mm 여유
    n_boundary = 48
    angles = np.linspace(0, 2 * np.pi, n_boundary, endpoint=False)
    bx = data_r * np.cos(angles)
    by = data_r * np.sin(angles)
    tree = cKDTree(np.column_stack([xs, ys]))
    _, nearest_idx = tree.query(np.column_stack([bx, by]))
    bz = zs[nearest_idx]
    xs_ext = np.concatenate([xs, bx])
    ys_ext = np.concatenate([ys, by])
    zs_ext = np.concatenate([zs, bz])

    # 고해상도 그리드 (400×400)
    grid_res = 400
    pad = data_r * 1.05
    x_range = np.linspace(-pad, pad, grid_res)
    y_range = np.linspace(-pad, pad, grid_res)
    xi, yi = np.meshgrid(x_range, y_range)

    zi = griddata((xs_ext, ys_ext), zs_ext, (xi, yi), method='cubic')

    # 원형 마스킹 (측정 영역만)
    dist = np.sqrt(xi**2 + yi**2)
    zi[dist > data_r] = np.nan

    # Contour 렌더링 (30 레벨, 부드러운 그라데이션)
    auto_vmin = float(np.nanmin(zs)) if vmin == 0.0 and vmax == 1.0 else vmin
    auto_vmax = float(np.nanmax(zs)) if vmin == 0.0 and vmax == 1.0 else vmax
    if auto_vmin == auto_vmax:
        auto_vmax = auto_vmin + 0.1
    levels = np.linspace(auto_vmin, auto_vmax, 30)
    norm = Normalize(vmin=auto_vmin, vmax=auto_vmax)

    cf = ax.contourf(xi, yi, zi, levels=levels, cmap='RdYlGn_r', norm=norm, extend='both')

    # 원형 clip_path — 깔끔한 원형 클리핑 (matplotlib 버전 호환)
    clip_circle = Circle((0, 0), wafer_radius_um, transform=ax.transData)
    try:
        # matplotlib >= 3.8
        for artist in cf.artists if hasattr(cf, 'artists') else cf.collections:
            artist.set_clip_path(clip_circle)
    except (AttributeError, TypeError):
        pass  # NaN 마스킹만으로 충분

    # 웨이퍼 경계 원 (점선)
    wafer_outline = Circle((0, 0), wafer_radius_um, fill=False,
                            edgecolor='#888', linewidth=1.5,
                            linestyle='--', alpha=0.7)
    ax.add_patch(wafer_outline)

    # Die 점 (작은 원)
    ax.scatter(xs, ys, c='#1e1e2e', s=25, zorder=5,
               edgecolors='white', linewidths=0.6, alpha=0.8)

    # Die 값 annotation (흰색 + 검정 외곽선 + 반투명 배경으로 시인성 극대화)
    from matplotlib.patheffects import withStroke
    outline = withStroke(linewidth=4, foreground='black')
    for i, (x, y) in enumerate(zip(xs, ys)):
        val = zs[i]
        ax.text(x, y + wafer_radius_um * 0.04, f'{val:.2f}',
                ha='center', va='bottom', fontsize=7.5, fontweight='bold',
                color='white', zorder=6,
                path_effects=[outline],
                bbox=dict(boxstyle='round,pad=0.12', fc='black',
                          ec='none', alpha=0.45))

    # Colorbar
    cbar = fig.colorbar(cf, ax=ax, shrink=0.75, pad=0.04)
    cbar.set_label('Deviation (µm)', color='#aaa')
    cbar.ax.yaxis.set_tick_params(color='#777')
    for label in cbar.ax.get_yticklabels():
        label.set_color('#aaa')

    # 축 스타일
    ax.set_xlim(-wafer_radius_um * 1.08, wafer_radius_um * 1.08)
    ax.set_ylim(-wafer_radius_um * 1.08, wafer_radius_um * 1.08)
    ax.set_aspect('equal')
    ax.set_xlabel('X (µm)', color='#aaa')
    ax.set_ylabel('Y (µm)', color='#aaa')
    ax.set_title(title, fontsize=11, color='#89b4fa')
    ax.tick_params(colors='#777')
    for s in ax.spines.values():
        s.set_color('#363650')
    fig.tight_layout()
    return fig



def plot_xy_scatter(x_dev_result: dict, y_dev_result: dict,
                    title: str = 'XY Scatter') -> Figure:
    """X/Y Deviation을 Die별 색상으로 구분한 산점도.

    Args:
        x_dev_result, y_dev_result: compute_deviation_matrix() 결과
    """
    fig, ax = plt.subplots(figsize=(7, 6))

    die_labels = x_dev_result.get('die_labels', [])
    repeat_labels = x_dev_result.get('repeat_labels', [])
    x_matrix = x_dev_result.get('matrix', {})
    y_matrix = y_dev_result.get('matrix', {})

    for dl in die_labels:
        idx = int(dl.replace('Die', '')) - 1  # 1-based label → 0-based index
        color = _color_from_die(idx)
        xvals, yvals = [], []
        for rl in repeat_labels:
            xv = x_matrix.get(rl, {}).get(dl)
            yv = y_matrix.get(rl, {}).get(dl)
            if xv is not None and yv is not None:
                xvals.append(xv)
                yvals.append(yv)
        if xvals:
            ax.scatter(xvals, yvals, c=[color], s=20, label=dl, alpha=0.8, edgecolors='none')

    ax.set_xlabel('X Offset (µm)')
    ax.set_ylabel('Y Offset (µm)')
    ax.set_xlim(-5, 5)
    ax.set_ylim(-5, 5)
    ax.set_xticks(range(-5, 6))
    ax.set_yticks(range(-5, 6))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=6, ncol=3, loc='upper right', framealpha=0.7)
    ax.set_title(title, fontsize=11)
    fig.tight_layout()
    return fig




def _standard_positions_mm() -> list:
    """측정 좌표가 없을 때의 표준 배치 [(die_idx, (x_mm, y_mm)), ...].

    DIE_POSITIONS는 Site 격자 칸 단위다. mm로 바꾸지 않고 µm 축(±150000)에 그리면
    21개가 모두 (0, 0) 근처 6000 µm 안에 겹쳐 보였다. 표의 첫 칸은 Site 0002(die_idx 1)다.
    """
    return [(i + 1, (x * DIE_PITCH_MM, y * DIE_PITCH_MM)) for i, (x, y) in enumerate(DIE_POSITIONS)]


def plot_die_position_map(dynamic_positions: dict = None,
                          wafer_radius_um: float = 150_000) -> Figure:
    """21 Die 좌표 배치 참조도.

    Args:
        dynamic_positions: {0: (x_mm, y_mm), ...} — 데이터에서 추출된 실제 좌표
        wafer_radius_um: 웨이퍼 반경 (µm) — 축 범위 결정용
    """
    fig, ax = plt.subplots(figsize=(8, 7))
    fig.patch.set_facecolor('#262637')
    ax.set_facecolor('#1e1e2e')

    # 좌표 소스 결정
    if dynamic_positions:
        die_indices = sorted(dynamic_positions.keys())
        positions = [(i, dynamic_positions[i]) for i in die_indices]
    else:
        positions = _standard_positions_mm()

    # mm → µm 변환하여 표시
    positions_um = [(idx, (x * 1000, y * 1000)) for idx, (x, y) in positions]

    # Die 마커 + 번호
    sc_list = []
    legend_handles = []
    for die_idx, (x, y) in positions_um:
        c = _color_from_die(die_idx)
        sc = ax.scatter(x, y, c=[c], s=800, zorder=5, edgecolors='white', linewidths=0.8,
                        label=f'Die {die_idx + 1}')
        ax.annotate(str(die_idx + 1), (x, y), ha='center', va='center',
                    fontsize=11, fontweight='bold', color='white', zorder=6)
        sc_list.append((die_idx, x, y, sc))
        legend_handles.append(sc)

    # 측정 순서 화살표
    for i in range(len(positions_um) - 1):
        _, (x0, y0) = positions_um[i]
        _, (x1, y1) = positions_um[i + 1]
        ax.annotate('', xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle='->', color='#585b70',
                                    lw=0.8, connectionstyle='arc3,rad=0.15'),
                    zorder=2)

    # 호버 툴팁
    hover_annot = ax.annotate('', xy=(0, 0), xytext=(15, 15),
                               textcoords='offset points',
                               bbox=dict(boxstyle='round,pad=0.4',
                                         fc='#313244', ec='#89b4fa', alpha=0.95),
                               color='white', fontsize=9,
                               arrowprops=dict(arrowstyle='->', color='#89b4fa'),
                               zorder=10)
    hover_annot.set_visible(False)

    def on_hover(event):
        if event.inaxes != ax:
            hover_annot.set_visible(False)
            fig.canvas.draw_idle()
            return
        found = False
        for die_idx, dx, dy, sc in sc_list:
            contains, _ = sc.contains(event)
            if contains:
                hover_annot.xy = (dx, dy)
                hover_annot.set_text(
                    f"Die {die_idx + 1}\n"
                    f"X: {dx:,.0f} µm\n"
                    f"Y: {dy:,.0f} µm")
                hover_annot.set_visible(True)
                found = True
                break
        if not found:
            hover_annot.set_visible(False)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect('motion_notify_event', on_hover)

    # 웨이퍼 경계 원 (점선, 반투명)
    from matplotlib.patches import Circle
    wafer_circle = Circle((0, 0), wafer_radius_um, fill=False,
                           edgecolor='#888', linewidth=1.5,
                           linestyle='--', alpha=0.5)
    ax.add_patch(wafer_circle)

    # 축 범위 — 웨이퍼 반경 기준
    ax.set_xlim(-wafer_radius_um * 1.05, wafer_radius_um * 1.05)
    ax.set_ylim(-wafer_radius_um * 1.05, wafer_radius_um * 1.05)
    ax.set_aspect('equal')
    ax.set_xlabel('X (µm)', color='#aaa')
    ax.set_ylabel('Y (µm)', color='#aaa')
    ax.set_title(f'Die Position Map ({len(positions_um)} Dies)' if dynamic_positions
                 else f'Die Position Map (표준 배치 {len(positions_um)} Dies — 측정 좌표 없음)',
                 color='#89b4fa', fontsize=12)
    ax.tick_params(colors='#777')
    for s in ax.spines.values():
        s.set_color('#363650')
    ax.grid(True, alpha=0.15, color='#555')

    # Die 범례 (우측)
    leg = ax.legend(handles=legend_handles,
                    loc='center left', bbox_to_anchor=(1.02, 0.5),
                    fontsize=9, frameon=True, framealpha=0.7,
                    facecolor='#313244', edgecolor='#45475a',
                    labelcolor='#cdd6f4', ncol=1,
                    markerscale=0.6, handletextpad=0.5,
                    borderpad=0.6, labelspacing=1.2,
                    handleheight=1.0)
    leg.set_title('Die', prop={'size': 10, 'weight': 'bold'})
    leg.get_title().set_color('#89b4fa')

    fig.tight_layout()
    return fig




def plot_die_position_map_mini(dynamic_positions: dict = None,
                                wafer_radius_um: float = 150_000,
                                excluded_dies: set = None) -> tuple:
    """Die Position Map 미니 버전 — Die 필터 확장 패널용.

    - 범례, 축 라벨, 측정 순서 화살표 제거 (공간 절약)
    - picker=True → 클릭 이벤트 감지 가능
    - excluded_dies 내의 Die는 회색 + 반투명으로 표시

    Returns:
        (fig, die_scatter_map) — die_scatter_map: {die_idx: scatter_artist}
    """
    fig, ax = plt.subplots(figsize=(3.5, 3.2))
    fig.patch.set_facecolor('#262637')
    ax.set_facecolor('#1e1e2e')

    if excluded_dies is None:
        excluded_dies = set()

    # 좌표 소스 결정
    if dynamic_positions:
        die_indices = sorted(dynamic_positions.keys())
        positions = [(i, dynamic_positions[i]) for i in die_indices]
    else:
        positions = _standard_positions_mm()

    # mm → µm
    positions_um = [(idx, (x * 1000, y * 1000)) for idx, (x, y) in positions]

    # 웨이퍼 경계 원
    from matplotlib.patches import Circle
    wafer_circle = Circle((0, 0), wafer_radius_um, fill=False,
                           edgecolor='#555', linewidth=1, linestyle='--', alpha=0.4)
    ax.add_patch(wafer_circle)

    # Die 마커 + 번호
    die_scatter_map = {}  # {die_idx: scatter_artist}
    for die_idx, (x, y) in positions_um:
        is_excluded = die_idx in excluded_dies
        if is_excluded:
            c = '#555555'
            alpha = 0.35
            edge_color = '#444'
            text_color = '#666'
        else:
            c = _color_from_die(die_idx)
            alpha = 1.0
            edge_color = 'white'
            text_color = 'white'

        sc = ax.scatter(x, y, c=[c], s=280, zorder=5, alpha=alpha,
                        edgecolors=edge_color, linewidths=0.5,
                        picker=True, pickradius=8)
        ax.annotate(str(die_idx + 1), (x, y), ha='center', va='center',
                    fontsize=7, fontweight='bold', color=text_color,
                    alpha=alpha, zorder=6)
        die_scatter_map[die_idx] = sc

    # 축 범위 — 웨이퍼 반경 기준 (간소화)
    ax.set_xlim(-wafer_radius_um * 1.08, wafer_radius_um * 1.08)
    ax.set_ylim(-wafer_radius_um * 1.08, wafer_radius_um * 1.08)
    ax.set_aspect('equal')
    ax.set_title(f'Die Position ({len(positions_um)})' if dynamic_positions
                 else f'Die Position (표준 배치 {len(positions_um)})',
                 color='#89b4fa', fontsize=9, pad=4)
    ax.tick_params(colors='#555', labelsize=6)
    for s in ax.spines.values():
        s.set_color('#363650')
    ax.grid(True, alpha=0.1, color='#444')

    fig.tight_layout(pad=0.5)
    return fig, die_scatter_map



def plot_tiff_profile(data_2d, info: dict,
                      title: str = None) -> Figure:
    """TIFF Raw Height 프로파일 시각화"""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    z_unit = info.get('z_unit', '')
    channel = info.get('channel_name', '')
    mode = info.get('head_mode', '')
    w = info.get('width', 1)
    h = info.get('height', 1)

    if title is None:
        title = f'{channel} [{mode}]'

    if h == 1 or w == 1:
        profile = data_2d.flatten()
        scan_size = info.get('scan_size_width', 0) if w > 1 else info.get('scan_size_height', 0)

        if scan_size and scan_size > 0:
            x_axis = np.linspace(0, scan_size, len(profile))
            x_label = 'Position (μm)'
        else:
            x_axis = np.arange(len(profile))
            x_label = 'Pixel'

        axes[0].plot(x_axis, profile, color='#1565C0', linewidth=0.5)
        axes[0].set_xlabel(x_label)
        axes[0].set_ylabel(f'Z ({z_unit})')
        axes[0].set_title(f'{title} — Profile')
        axes[0].grid(True, alpha=0.3)

        axes[1].hist(profile, bins=80, color='#42A5F5', edgecolor='white', alpha=0.8)
        axes[1].set_xlabel(f'Z ({z_unit})')
        axes[1].set_ylabel('Count')
        axes[1].set_title('Distribution')
        axes[1].grid(True, alpha=0.3)
    else:
        im = axes[0].imshow(data_2d, origin='lower', cmap='viridis', aspect='auto')
        plt.colorbar(im, ax=axes[0], label=f'Z ({z_unit})')
        axes[0].set_title(f'{title} — 2D')
        axes[0].set_xlabel('X (px)')
        axes[0].set_ylabel('Y (px)')

        mid_row = h // 2
        axes[1].plot(data_2d[mid_row, :], color='#1565C0', linewidth=0.5)
        axes[1].set_xlabel('X (px)')
        axes[1].set_ylabel(f'Z ({z_unit})')
        axes[1].set_title(f'H-Profile (row={mid_row})')
        axes[1].grid(True, alpha=0.3)

    fig.suptitle(title, fontsize=12, fontweight='bold')
    fig.tight_layout()
    return fig


# ──────────────────────────────────────────────
# Multi-Recipe 비교 차트
# ──────────────────────────────────────────────


def plot_vector_map(x_die_stats: list, y_die_stats: list,
                    title: str = 'Vector Map (Quiver)',
                    wafer_radius_um: float = 150_000,
                    dynamic_positions: dict = None,
                    scale_pct: int = 10,
                    show_values: bool = False):
    """Die X/Y 편차를 Quiver(화살표)로 시각화.

    Args:
        x_die_stats, y_die_stats: compute_deviation_matrix() 결과의 die_stats
            die 필드는 'Die1', 'Die2', ... (1-based 표기)
        scale_pct: 화살표 배율 (% of wafer radius)
        show_values: True이면 각 화살표 옆에 magnitude(µm) 값 레이블 표시
    """
    from core import get_die_position
    import matplotlib.cm as cm
    import matplotlib.colorbar

    # dpi는 캔버스 부착 시 화면 논리 DPI로 동기화됨(sync_canvas_dpi, pattern d).
    # 여기서 고정 dpi를 박지 않는다 — 디스플레이마다 물리 크기가 달라지는 원인.
    fig, ax = plt.subplots(figsize=(7, 7))
    fig.patch.set_facecolor('#1e1e2e')
    ax.set_facecolor('#1e1e2e')

    # die_stats의 die 필드는 이미 "Die1" 형식 (1-based) — 직접 key로 사용
    x_map = {d['die']: d['avg'] for d in x_die_stats}
    y_map = {d['die']: d['avg'] for d in y_die_stats}

    common = sorted(set(x_map.keys()) & set(y_map.keys()))
    if not common:
        ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                transform=ax.transAxes, color='#888', fontsize=14)
        return fig

    xs, ys, us, vs, die_labels = [], [], [], [], []
    for die_label in common:
        pos = get_die_position(die_label, dynamic_positions)
        if pos is None:
            continue
        xs.append(pos[0])
        ys.append(pos[1])
        us.append(x_map[die_label])
        vs.append(y_map[die_label])
        die_labels.append(die_label)

    if not xs:
        ax.text(0.5, 0.5, 'No position data\n(Die positions not configured)',
                ha='center', va='center',
                transform=ax.transAxes, color='#888', fontsize=12)
        return fig

    xs = np.array(xs, dtype=float)
    ys = np.array(ys, dtype=float)
    us = np.array(us, dtype=float)
    vs = np.array(vs, dtype=float)

    magnitudes = np.sqrt(us**2 + vs**2)

    # 최대 벡터 길이와 데이터 범위로 스케일 결정
    max_vec = max(magnitudes.max(), 1e-9)
    data_r = np.sqrt(xs**2 + ys**2).max() if len(xs) > 0 else wafer_radius_um / 1000
    arrow_scale_factor = (scale_pct / 100.0) * data_r / max_vec

    # Die positions (background dots)
    ax.scatter(xs, ys, c='none', s=20, zorder=3,
               edgecolors='#585b70', linewidths=0.5)

    # 컬러맵 + Normalize
    cmap = plt.cm.RdYlGn_r
    mag_max = magnitudes.max() if magnitudes.max() > 0 else 1.0
    norm = Normalize(vmin=0, vmax=mag_max)
    colors = cmap(norm(magnitudes))

    # 화살표
    for i in range(len(xs)):
        ax.annotate('', xy=(xs[i] + us[i] * arrow_scale_factor,
                            ys[i] + vs[i] * arrow_scale_factor),
                     xytext=(xs[i], ys[i]),
                     arrowprops=dict(arrowstyle='->', color=colors[i],
                                     lw=1.5, mutation_scale=12),
                     zorder=5)

        # ── Show Values 모드: magnitude 레이블 ──
        if show_values:
            mag = magnitudes[i]
            # 레이블 위치: 화살표 시작점(Die 위치) 바로 위
            ax.text(xs[i], ys[i], f'{mag:.2f}µm',
                    fontsize=6.5, color='#e0e0e0',
                    ha='center', va='bottom', zorder=7,
                    bbox=dict(boxstyle='round,pad=0.15',
                              fc='#1e1e2e', ec='none', alpha=0.7))


    # Colorbar (ScalarMappable로 생성)
    sm = cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.75, pad=0.03, aspect=25)
    cbar.set_label('Deviation Magnitude (µm)', color='#a6adc8', fontsize=8)
    cbar.ax.yaxis.set_tick_params(color='#585b70', labelcolor='#a6adc8',
                                   labelsize=7)
    cbar.outline.set_edgecolor('#45475a')

    # Wafer circle
    wr_mm = wafer_radius_um / 1000
    circle = Circle((0, 0), wr_mm, fill=False, edgecolor='#585b70',
                     linewidth=1.5, linestyle='--')
    ax.add_patch(circle)

    lim = wr_mm * 1.12
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect('equal')
    ax.set_title(title, fontsize=12, fontweight='bold', color='#cdd6f4', pad=10)
    ax.set_xlabel('X (mm)', fontsize=9, color='#a6adc8')
    ax.set_ylabel('Y (mm)', fontsize=9, color='#a6adc8')
    ax.tick_params(colors='#666', labelsize=8)
    for s in ax.spines.values():
        s.set_color('#363650')
        s.set_linewidth(0.5)
    ax.grid(True, color='#313244', alpha=0.3, linewidth=0.5)

    # Scale 텍스트
    ax.text(0.02, 0.98, f'Scale: {scale_pct}%', transform=ax.transAxes,
            fontsize=8, color='#a6adc8', va='top')

    fig.tight_layout()
    return fig
