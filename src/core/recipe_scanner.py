"""
recipe_scanner.py — Multi-Recipe 자동 탐지 및 비교

데이터 폴더 구조:
    data/
    ├── 1. Vision Pattern Recognize/
    │   ├── 1st/  ← Lot 폴더들
    │   └── 2nd/
    ├── 2. In-Die Align/
    │   ├── 1st/
    │   └── 2nd/
    └── ...
"""

import os
import re
import math
import logging
import traceback
import collections
from core.csv_loader import scan_lot_folders, batch_load, get_scan_summary
from core import (compute_statistics, compute_trend,
                      detect_outliers_by_axis, compute_repeatability,
                      compute_group_statistics, filter_by_method,
                      filter_stabilization_die)


def build_recipe(path: str, name: str = None):
    """폴더 하나를 Recipe dict로 만든다. Lot 데이터가 없으면 None.

    폴더 바로 아래에 Lot이 있으면 라운드 '(root)' 하나, 아니면 하위 폴더(1st/2nd 등)
    중 Lot이 있는 것을 라운드로 삼는다. index는 폴더명 앞 숫자(없으면 0)이며,
    여러 Recipe를 묶는 쪽에서 1..N으로 다시 매긴다.
    """
    if not path or not os.path.isdir(path):
        return None
    name = name if name is not None else os.path.basename(os.path.normpath(path))

    # 1차: Recipe 폴더 바로 아래에 Lot 폴더가 있는지 확인 (플랫 구조)
    #      서버 데이터: Recipe/Lot102, Recipe/Lot103, ...
    rounds = []
    direct_lots = scan_lot_folders(path)
    if direct_lots:
        rounds.append({
            'name': '(root)',
            'path': path,
            'lot_count': len(direct_lots),
        })
    else:
        # 2차: 하위에 1st/2nd 라운드 폴더가 있는지 확인
        sub_items = sorted(os.listdir(path))
        for sub in sub_items:
            sub_path = os.path.join(path, sub)
            if not os.path.isdir(sub_path):
                continue
            lots = scan_lot_folders(sub_path)
            if lots:
                rounds.append({
                    'name': sub,
                    'path': sub_path,
                    'lot_count': len(lots),
                })

    if not rounds:
        return None

    idx_match = re.match(r'^(\d+)', name)

    # 짧은 이름 생성
    short = re.sub(r'^\d+\.\s*', '', name).strip()
    if len(short) > 20:
        short = short[:18] + '…'

    return {
        'name': name,
        'path': path,
        'index': int(idx_match.group(1)) if idx_match else 0,
        'short_name': short,
        'rounds': rounds,
    }


def scan_recipes(root_path: str) -> list:
    """루트 폴더에서 Recipe 하위 구조 자동 탐지

    Args:
        root_path: 최상위 데이터 폴더 (예: ".../data")

    Returns:
        [{'name': '1. Vision Pattern Recognize', 'path': '...',
          'index': 1, 'short_name': 'Vision Pattern',
          'rounds': [{'name': '1st', 'path': '...', 'lot_count': 11},
                     {'name': '2nd', ...}]}, ...]
    """
    if not os.path.isdir(root_path):
        return []

    recipes = []

    for name in sorted(os.listdir(root_path)):
        recipe_path = os.path.join(root_path, name)
        if not os.path.isdir(recipe_path):
            continue

        recipe = build_recipe(recipe_path, name)
        if recipe:
            # 인덱스 추출 (폴더명 앞의 숫자). 번호 없는 폴더는 큰 센티넬로 뒤에 배치
            # (발견 순서 유지). 최종 index는 정렬 후 1..N으로 재부여하므로 여기서의
            # 중복/충돌은 표시 번호에 영향을 주지 않는다.
            if not re.match(r'^\d+', name):
                recipe['index'] = 10 ** 6 + len(recipes)
            recipes.append(recipe)

    recipes.sort(key=lambda x: x['index'])
    # 정렬 후 1..N 연속 번호로 재부여 → Step 라벨 유일성·연속성 보장
    for i, r in enumerate(recipes):
        r['index'] = i + 1
    return recipes


def strip_step_number(name: str) -> str:
    """'1. Vision Pattern' → 'Vision Pattern' (앞의 번호와 공백 제거)."""
    return re.sub(r'^\d+\.\s*', '', name or '').strip()


def match_spec_key(name: str, spec_keys) -> str:
    """폴더명이 Spec 이름과 같으면(대소문자 무시, 앞 번호 무시) 정확한 Spec 키, 아니면 None."""
    candidates = {(name or '').strip().lower(), strip_step_number(name).lower()}
    for key in spec_keys:
        if key.strip().lower() in candidates:
            return key
    return None


def normalize_spec_names(recipes: list, spec_keys) -> list:
    """자동 스캔 Recipe의 short_name을 정확한 Spec 키로 맞추고, 맞지 않는 폴더명을 돌려준다.

    판정·Cpk는 short_name으로 Spec을 정확히 찾는다. 'vision pattern' 폴더는 이름 검사를
    통과해도 'Vision Pattern' 키를 찾지 못해 판정이 비었다.
    """
    mismatched = []
    for r in recipes:
        key = match_spec_key(r['name'], spec_keys)
        if key is None:
            key = match_spec_key(r.get('short_name', ''), spec_keys)
        if key is None:
            mismatched.append(r['name'])
        else:
            r['short_name'] = key
    return mismatched


def recipes_from_mapping(mapping, spec_order=None):
    """Workflow(Spec 이름)별로 고른 폴더로 Recipe 목록을 만든다.

    Args:
        mapping: [(spec_name, path), ...]
        spec_order: Spec 키 순서 (spec_deviation 키 순서). 없으면 mapping 순서

    Returns:
        (recipes, problems) — problems는 [(spec_name, path, 이유), ...]
    """
    order = list(spec_order or [])
    recipes, problems, seen = [], [], set()
    for spec_name, path in mapping:
        if spec_name in seen:
            continue
        seen.add(spec_name)
        if not path or not os.path.isdir(path):
            problems.append((spec_name, path, '폴더 없음'))
            continue
        try:
            recipe = build_recipe(path, spec_name)
        except OSError as e:
            problems.append((spec_name, path, f'폴더를 읽을 수 없음 ({e.strerror or e})'))
            continue
        if recipe is None:
            problems.append((spec_name, path, 'Lot 데이터 없음'))
            continue
        recipe.update({
            'name': spec_name,
            'short_name': spec_name,
            'source_path': path,
            'source_label': os.path.basename(os.path.normpath(path)),
        })
        recipes.append(recipe)

    def _rank(recipe):
        name = recipe['name']
        return order.index(name) if name in order else len(order)
    recipes.sort(key=_rank)
    for i, r in enumerate(recipes):
        r['index'] = i + 1
    return recipes, problems


def summarize_failed_measurements(data: list) -> dict:
    """측정 실패 행(Valid=FALSE 또는 값이 비유한)을 집계한다.

    장비는 정렬/패턴인식에 실패한 측정을 HZ1_O=NaN, Valid=FALSE로 기록한다.
    이 행들은 통계·차트에서 제외되므로, 사용자가 '어느 데이터가 실제로 문제인지'
    구별할 수 있도록 Lot·Die별로 요약해 돌려준다.

    Returns:
        {'count': N, 'by_lot': [(lot, n), ...], 'by_site': [(site_id, n), ...],
         'text': '사람이 읽을 요약 한 줄'}  — 실패가 없으면 {'count': 0}
    """
    bad = [r for r in data
           if not r.get('valid', True)
           or not (isinstance(r.get('value'), (int, float))
                   and math.isfinite(r['value']))]
    if not bad:
        return {'count': 0}

    by_lot = collections.Counter(r.get('lot_name', '?') for r in bad)
    by_site = collections.Counter(r.get('site_id', '?') for r in bad)

    def _top(counter, n=5):
        head = ', '.join(f'{k} {v}건' for k, v in counter.most_common(n))
        if len(counter) > n:
            head += f' 외 {len(counter) - n}개'
        return head

    return {
        'count': len(bad),
        'by_lot': by_lot.most_common(),
        'by_site': by_site.most_common(),
        'text': (f'측정 실패 {len(bad)}건 — 분석/차트에서 제외됨 '
                 f'| Lot: {_top(by_lot)} | Die: {_top(by_site, 3)}'),
    }


def load_recipe_data(recipe: dict, round_name: str = '1st',
                     lot_range=None, axis='both') -> dict:
    """단일 Recipe의 데이터 로드 + 분석

    Returns:
        {'recipe': ..., 'round': ..., 'raw_data': [...],
         'statistics': {...}, 'trend': [...], 'repeatability': {...},
         'group_stats': [...], 'outlier_count': N,
         'load_errors': [건너뛴 Lot 메시지, ...]}
    """
    # 해당 라운드 찾기
    round_info = None
    for r in recipe.get('rounds', []):
        if r['name'] == round_name:
            round_info = r
            break

    if not round_info:
        # 첫 번째 라운드 사용
        round_info = recipe['rounds'][0] if recipe.get('rounds') else None

    if not round_info:
        return {'recipe': recipe['name'], 'round': round_name,
                'raw_data': [], 'error': 'No data found', 'load_errors': []}

    load_errors = []
    data_warnings = []
    load_info = {}
    data = batch_load(round_info['path'], lot_range=lot_range, axis=axis,
                      errors=load_errors, warnings=data_warnings,
                      info=load_info)
    if not data:
        return {'recipe': recipe['name'], 'round': round_info['name'],
                'raw_data': [], 'error': 'No data loaded',
                'load_errors': load_errors, 'data_warnings': data_warnings,
                'failed_measurements': {'count': 0}}

    # raw_data는 22 Die 원본을 유지한다. 통계·트렌드·이상치는 분석 시트와 같은
    # 21 Die로 계산한다. 안정화 Die는 판정 대상이 아니므로 이상치로 표시하지 않는다.
    analysis = filter_stabilization_die(data)
    for r in data:
        r['is_outlier'] = False
    detect_outliers_by_axis(analysis, method='iqr')

    return {
        'recipe': recipe['name'],
        'short_name': recipe.get('short_name', recipe['name']),
        'round': round_info['name'],
        'round_path': round_info['path'],
        'raw_data': data,
        'load_errors': load_errors,
        'data_warnings': data_warnings,
        'channel_dropped': load_info.get('channel_dropped', 0),
        'first_repeat_excluded': load_info.get('first_repeat_excluded'),
        'first_repeat_unverified': load_info.get('first_repeat_unverified', 0),
        'no_measurement_folders': load_info.get('no_measurement_folders', []),
        'failed_measurements': summarize_failed_measurements(data),
        'statistics': compute_statistics(analysis),
        'trend': compute_trend(analysis),
        'trend_x': compute_trend(filter_by_method(analysis, 'X')),
        'trend_y': compute_trend(filter_by_method(analysis, 'Y')),
        'repeatability': compute_repeatability(analysis),
        'group_stats': compute_group_statistics(analysis, 'lot_name'),
        'outlier_count': sum(1 for r in analysis if r.get('is_outlier')),
    }


def load_all_recipes(root_path: str, round_name: str = '1st',
                     axis: str = 'both',
                     progress_cb=None, recipes=None) -> list:
    """모든 Recipe 일괄 로드

    Args:
        progress_cb: callback(recipe_index, total, recipe_name)
        recipes: 이미 정한 Recipe 목록 (Workflow별 폴더 선택). 주면 root_path를 스캔하지 않는다

    Returns:
        [load_recipe_data() 결과, ...]
    """
    if recipes is None:
        recipes = scan_recipes(root_path)
    results = []

    for i, recipe in enumerate(recipes):
        if progress_cb:
            progress_cb(i + 1, len(recipes), recipe['name'])
        # Recipe 단위 격리 — 한 Recipe의 실패가 나머지 Recipe 분석까지 무효화하지 않게 한다.
        try:
            result = load_recipe_data(recipe, round_name=round_name, axis=axis)
        except Exception as e:
            logging.warning("Recipe '%s' 로드 실패:\n%s",
                            recipe['name'], traceback.format_exc())
            result = {
                'recipe': recipe['name'],
                'short_name': recipe.get('short_name', recipe['name']),
                'round': round_name,
                'raw_data': [],
                'error': f'{type(e).__name__}: {e}',
                'load_errors': [],
                'data_warnings': [],
                'failed_measurements': {'count': 0},
            }
        # Workflow별로 고른 폴더는 Recipe마다 출처가 달라 내보내기에 따로 적는다
        if recipe.get('source_path'):
            result['source_path'] = recipe['source_path']
        results.append(result)

    return results


def compare_recipes(results: list) -> list:
    """Recipe간 핵심 지표 비교 테이블

    Returns:
        [{'recipe': 'Vision Pattern', 'lots': 11,
          'mean': ..., 'stdev': ..., 'min': ..., 'max': ...,
          'cv_percent': ..., 'outliers': 24}, ...]
    """
    comparison = []
    for r in results:
        stats = r.get('statistics', {})
        rep = r.get('repeatability', {})
        overall = rep.get('overall', {})

        comparison.append({
            'recipe': r.get('short_name', r.get('recipe', '')),
            'round': r.get('round', ''),
            'data_count': stats.get('count', 0),
            'mean': stats.get('mean', 0),
            'stdev': stats.get('stdev', 0),
            'min': stats.get('min', 0),
            'max': stats.get('max', 0),
            'range': stats.get('range', 0),
            'cv_percent': overall.get('cv_percent', 0),
            'outliers': r.get('outlier_count', 0),
        })

    return comparison
