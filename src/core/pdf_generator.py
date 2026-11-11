"""
pdf_generator.py — PDF 리포트 자동 생성

matplotlib.backends.backend_pdf.PdfPages 를 활용하여
분석 결과 요약 및 차트를 포함하는 다페이지 리포트 생성
"""

import os
import datetime
import textwrap
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from charts.basic import _extract_site_data
from core import filter_by_method, filter_stabilization_die, filter_valid_only
from core.checklist import basis_warnings, build_checklist, cpk_text, result_text
from core.recipe_scanner import is_run_style, run_label


NOTE_LINES_PER_PAGE = 34


def _basis_notes(recipe_results: list) -> list:
    """Recipe별로 실제 제외 내역과 기준 차이를 적는다 (제외했다고 단정하지 않는다)."""
    notes = []
    for result in recipe_results or []:
        name = result.get('short_name') or result.get('recipe', '')
        if result.get('first_repeat_excluded'):
            notes.append(f"{name}: first repeat folder {result['first_repeat_excluded']} excluded")
        notes.extend(f'{name}: {msg}' for msg in basis_warnings(result))
    return notes


def _short_path(path: str, limit: int) -> str:
    """긴 네트워크 경로가 페이지 밖으로 잘리지 않게 가운데를 줄인다."""
    if len(path) <= limit:
        return path
    head = (limit - 5) // 3
    return f"{path[:head]} ... {path[-(limit - 5 - head):]}"


def _text(value, fmt: str) -> str:
    return '—' if value is None else format(value, fmt)


def _add_title_page(pdf: PdfPages, root_folder: str, comparison: list,
                    checklist: list = None, notes: list = None):
    """첫 페이지: 보고서 메타데이터, 체크리스트, Summary 테이블"""
    # pyplot을 쓰지 않는다: 작업 스레드에서 돌고, 메인 스레드가 plt.close('all')을 호출한다
    fig = Figure(figsize=(11, 8.5))
    ax = fig.add_subplot(111)
    ax.axis('off')

    # 타이틀
    ax.text(0.5, 0.9, "XY Stage Positioning Offset Analysis Report", 
            ha='center', va='center', fontsize=24, fontweight='bold')
    
    # 메타데이터
    dt_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ax.text(0.1, 0.8, f"Date: {dt_str}", fontsize=12)
    path_text = _short_path(root_folder, 110)
    ax.text(0.1, 0.75, f"Data Path: {path_text}", fontsize=8)

    # Checklist Table — 화면 Summary·Step 카드 기본값과 같은 값
    if checklist:
        ax.text(0.5, 0.71, "Checklist (analysis basis: Site 0001 excluded, see Die x Repeat)",
                ha='center', fontsize=12, fontweight='bold')
        rows = [[r['recipe'], r['axis'], f"{r['die_count']} x {r['repeat_count']}",
                 _text(r['avg'], '.3f'), _text(r['range'], '.3f'), _text(r['stddev'], '.3f'),
                 cpk_text(r['cpk'], r['reference']),
                 result_text(r['result'], r['reference'])] for r in checklist]
        table = ax.table(cellText=rows,
                         colLabels=['Recipe', 'Axis', 'Die x Repeat', 'Avg (nm)',
                                    'Dev Range (um)', 'Dev StdDev (um)', 'Cpk', 'Result'],
                         bbox=[0.0, 0.33, 1.0, 0.36], cellLoc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(9)

    # Summary Table
    if comparison:
        col_labels = ['Recipe', 'Round', 'N', 'Mean', 'Stdev', 'Min', 'Max', 'CV%', 'Outliers']
        cell_text = []
        for c in comparison:
            cell_text.append([
                c.get('recipe', ''), c.get('round', ''),
                str(c.get('data_count', 0)),
                f"{c.get('mean', 0):.1f}", f"{c.get('stdev', 0):.1f}",
                f"{c.get('min', 0):.1f}", f"{c.get('max', 0):.1f}",
                f"{c.get('cv_percent', 0):.1f}", str(c.get('outliers', 0))
            ])
        
        table = ax.table(cellText=cell_text, colLabels=col_labels,
                         bbox=[0.0, 0.0, 1.0, 0.26], cellLoc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(9)

        ax.text(0.5, 0.28, "Overall Summary (X and Y combined, analysis basis)",
                ha='center', fontsize=12, fontweight='bold')

    if notes:
        ax.text(0.0, -0.03, f"Analysis basis notes: {len(notes)} (see next page)",
                va='top', fontsize=9, color='#C62828')

    pdf.savefig(fig)


def _add_notes_pages(pdf: PdfPages, notes: list):
    """기준 관련 내역을 잘리지 않게 필요한 쪽수만큼 싣는다."""
    lines = [line for note in notes for line in textwrap.wrap(note, 120)]
    for start in range(0, len(lines), NOTE_LINES_PER_PAGE):
        fig = Figure(figsize=(11, 8.5))
        ax = fig.add_subplot(111)
        ax.axis('off')
        ax.set_title('Analysis basis notes', fontsize=14, fontweight='bold')
        ax.text(0.0, 1.0, '\n'.join(lines[start:start + NOTE_LINES_PER_PAGE]),
                va='top', fontsize=9, linespacing=1.4, color='#C62828')
        pdf.savefig(fig)


def _add_recipe_page(pdf: PdfPages, recipe_data: dict, spec_limits: dict):
    """각 Recipe별 통합 결과 페이지 (화면에서 고른 라운드·회차 기준)"""
    fig = Figure(figsize=(11, 8.5))
    
    recipe_name = recipe_data.get('short_name', 'Unknown Recipe')
    # 라운드 폴더 없이 Recipe 바로 아래에 Lot이 있으면 라운드 이름이 '(root)'다
    round_name = recipe_data.get('round', '')
    round_text = f" ({round_name} Round)" if round_name and round_name != '(root)' else ''
    # 회차 폴더(1..14, 날짜)면 어느 회차인지 적는다. 1st/2nd 구조는 1.1.3 제목 그대로
    runs = recipe_data.get('run_results') or []
    if run_label(recipe_data) and is_run_style([r.get('round', '') for r in runs]):
        round_text = f" — {run_label(recipe_data)}"
    fig.suptitle(f"Step: {recipe_name}{round_text}", fontsize=16, fontweight='bold', y=0.96)
    # Workflow별로 고른 폴더는 Step마다 출처가 다르다
    source = recipe_data.get('source_path')
    if source:
        fig.text(0.5, 0.925, f"Source: {_short_path(source, 130)}", ha='center', fontsize=8)

    # 통계·트렌드와 같은 분석 기준으로 등고선을 그린다 (안정화 Die 제외, 측정 실패 제외)
    data = filter_valid_only(filter_stabilization_die(recipe_data.get('raw_data', [])))
    if not data:
        ax = fig.add_subplot(111)
        ax.axis('off')
        ax.text(0.5, 0.5, "No Data", ha='center')
        pdf.savefig(fig)
        return

    # Layout: 
    # [ Top Left: Stats ]     [ Top Right: Trend ]
    # [ Bottom Left: Contour X ] [ Bottom Right: Contour Y ]
    
    # --- 1. Stats Table ---
    ax_stat = fig.add_subplot(221)
    ax_stat.axis('off')
    
    stats = recipe_data.get('statistics', {})
    rep = recipe_data.get('repeatability', {}).get('overall', {})
    
    stat_text = (
        f"Data Count: {stats.get('count', 0)}\n"
        f"Mean: {stats.get('mean', 0):.2f}\n"
        f"Stdev: {stats.get('stdev', 0):.2f}\n"
        f"Range: {stats.get('range', 0):.2f}\n"
        f"CV%: {rep.get('cv_percent', 0):.2f}%\n"
        f"Outliers: {recipe_data.get('outlier_count', 0)}"
    )
    ax_stat.text(0.1, 0.9, stat_text, va='top', fontsize=12, bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.2))
    ax_stat.set_title("Statistics", fontsize=12)

    # --- 2. Trend ---
    ax_trend = fig.add_subplot(222)
    trend_data = recipe_data.get('trend', [])
    if trend_data:
        indices = [t['lot_index'] for t in trend_data]
        means = [t['mean'] for t in trend_data]
        labels = [t['lot_name'] for t in trend_data]
        
        ax_trend.plot(indices, means, 'o-', color='#2196F3', markersize=4)
        ax_trend.set_xticks(indices)
        ax_trend.set_xticklabels(labels, rotation=45, ha='right', fontsize=6)
        ax_trend.set_title("Lot Trend", fontsize=12)
        ax_trend.grid(True, alpha=0.3)
    
    # --- 3. Contour X ---
    ax_cx = fig.add_subplot(223)
    d_x = filter_by_method(data, 'X')
    if d_x:
        xs, ys, means, _ = _extract_site_data(d_x, 'value', 'X')
        if len(xs) >= 4:
            try:
                from scipy.interpolate import griddata
                import numpy as np
                xi = np.linspace(min(xs) - 1, max(xs) + 1, 80)
                yi = np.linspace(min(ys) - 1, max(ys) + 1, 80)
                Xi, Yi = np.meshgrid(xi, yi)
                Zi = griddata((xs, ys), means, (Xi, Yi), method='cubic')
                cf = ax_cx.contourf(Xi, Yi, Zi, levels=15, cmap='RdYlGn_r', alpha=0.8)
                fig.colorbar(cf, ax=ax_cx, shrink=0.8)
                ax_cx.scatter(xs, ys, c='black', s=10)
            except Exception:
                ax_cx.scatter(xs, ys, c=means, cmap='RdYlGn_r')
    ax_cx.set_title("Contour X", fontsize=12)
    ax_cx.set_aspect('equal')
    
    # --- 4. Contour Y ---
    ax_cy = fig.add_subplot(224)
    d_y = filter_by_method(data, 'Y')
    if d_y:
        xs, ys, means, _ = _extract_site_data(d_y, 'value', 'Y')
        if len(xs) >= 4:
            try:
                from scipy.interpolate import griddata
                import numpy as np
                xi = np.linspace(min(xs) - 1, max(xs) + 1, 80)
                yi = np.linspace(min(ys) - 1, max(ys) + 1, 80)
                Xi, Yi = np.meshgrid(xi, yi)
                Zi = griddata((xs, ys), means, (Xi, Yi), method='cubic')
                cf = ax_cy.contourf(Xi, Yi, Zi, levels=15, cmap='RdYlGn_r', alpha=0.8)
                fig.colorbar(cf, ax=ax_cy, shrink=0.8)
                ax_cy.scatter(xs, ys, c='black', s=10)
            except Exception:
                ax_cy.scatter(xs, ys, c=means, cmap='RdYlGn_r')
    ax_cy.set_title("Contour Y", fontsize=12)
    ax_cy.set_aspect('equal')

    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
    pdf.savefig(fig)


def generate_pdf_report(export_path: str, root_folder: str, recipe_results: list, comparison: list,
                        spec_limits: dict, spec_deviation: dict = None):
    """지정된 경로로 다페이지 PDF 리포트 생성"""
    checklist = build_checklist(recipe_results, spec_limits, spec_deviation)
    with PdfPages(export_path) as pdf:
        notes = _basis_notes(recipe_results)
        if any(r.get('source_path') for r in recipe_results or []):
            # 앞 문구는 남기고 경로만 줄인다 (표지 한 줄 110자)
            root_folder = f"Per-workflow folders (common: {_short_path(root_folder, 79)})"
        _add_title_page(pdf, root_folder, comparison, checklist, notes)
        _add_notes_pages(pdf, notes)
        
        for result in recipe_results:
            _add_recipe_page(pdf, result, spec_limits)
            
    return True
