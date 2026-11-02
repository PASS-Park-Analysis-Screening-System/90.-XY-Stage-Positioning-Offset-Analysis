"""첫 리핏 자동 제외 회귀 테스트.

측정은 22 Die × 11 Repeat이고 분석은 21 Die × 10 Repeat이다. 엔지니어에 따라
Repeat 1 폴더를 빼고 10개만 넣기도, 11개를 모두 넣기도 한다.
어느 쪽이든 같은 10개 리핏으로 분석해야 한다.
"""
from core.csv_loader import batch_load
from core.recipe_scanner import load_recipe_data


def _write_lot(root, name, repeat_no, value=100.0):
    lot = root / name
    lot.mkdir(parents=True)
    meta = f'Repeat No,{repeat_no}\n' if repeat_no is not None else ''
    for axis in ('X', 'Y'):
        (lot / f'Sample10_{axis}_UL.csv').write_text(
            f'Lot ID,Lot3\n{meta}\n'
            'Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
            'HZ1_O (nm),HZ1_O_Valid\n'
            f'0001_X000_Y000,0,0,1,1,1,{axis},COMPLETED,TRUE,{value},TRUE\n'
            f'0002_X000_Y000,0,0,1,1,1,{axis},COMPLETED,TRUE,{value},TRUE\n',
            encoding='utf-8')


def _write_set(root, first_repeat, last_repeat):
    for rep in range(first_repeat, last_repeat + 1):
        _write_lot(root, f'Sample10{rep:02d}', rep, value=100.0 + rep)


def _lots(rows):
    return sorted({r['lot_name'] for r in rows})


def test_eleven_folders_drop_repeat_one(tmp_path):
    _write_set(tmp_path, 1, 11)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert _lots(rows) == [f'Sample10{r:02d}' for r in range(2, 12)]
    assert info['first_repeat_excluded'] == 'Sample1001'


def test_ten_folders_without_repeat_one_stay_whole(tmp_path):
    """엔지니어가 Repeat 1을 미리 뺀 경우: 추가로 빼면 9개가 된다."""
    _write_set(tmp_path, 2, 11)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert len(_lots(rows)) == 10
    assert 'first_repeat_excluded' not in info


def test_ten_folders_starting_at_repeat_one_stay_whole(tmp_path):
    """폴더가 10개뿐이면 Repeat 1이 있어도 빼지 않는다 (Sample1501~1510 형태)."""
    _write_set(tmp_path, 1, 10)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert len(_lots(rows)) == 10
    assert 'first_repeat_excluded' not in info


def test_both_entry_styles_give_identical_rows(tmp_path):
    """같은 측정이면 폴더를 11개 넣든 10개 넣든 결과가 같아야 한다."""
    _write_set(tmp_path / 'eleven', 1, 11)
    _write_set(tmp_path / 'ten', 2, 11)

    def values(rows):
        return [(r['lot_name'], r['site_id'], r['method'], r['value']) for r in rows]

    assert (values(batch_load(str(tmp_path / 'eleven')))
            == values(batch_load(str(tmp_path / 'ten'))))


def test_eleven_folders_without_repeat_one_are_kept_and_flagged(tmp_path):
    """첫 폴더가 Repeat 1이라는 근거가 없으면 빼지 않고 사용자에게 알린다."""
    _write_set(tmp_path, 2, 12)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert len(_lots(rows)) == 11
    assert 'first_repeat_excluded' not in info
    assert info['first_repeat_unverified'] == 11


def test_eleven_folders_with_missing_repeat_no_are_kept_and_flagged(tmp_path):
    for rep in range(1, 12):
        _write_lot(tmp_path, f'Lot{rep:03d}', None)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert len(_lots(rows)) == 11
    assert info['first_repeat_unverified'] == 11


def test_first_folder_is_taken_by_number_not_listing_order(tmp_path):
    """이름 정렬로는 Sample102가 Sample99보다 앞이지만, 번호순 첫 폴더는 Sample99다."""
    _write_lot(tmp_path, 'Sample99', 1)
    for number, rep in zip(range(100, 110), range(2, 12)):
        _write_lot(tmp_path, f'Sample{number}', rep)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert info['first_repeat_excluded'] == 'Sample99'
    assert 'Sample99' not in _lots(rows)
    assert len(_lots(rows)) == 10


def test_explicit_lot_range_is_respected(tmp_path):
    """호출자가 범위를 직접 지정하면 그 선택을 바꾸지 않는다."""
    _write_set(tmp_path, 1, 11)

    rows = batch_load(str(tmp_path), lot_range=(1, 11))

    assert len(_lots(rows)) == 11


def test_load_recipe_data_reports_exclusion_per_recipe(tmp_path):
    """레시피마다 따로 판단한다: 11개 넣은 레시피와 10개 넣은 레시피가 섞여도 된다."""
    _write_set(tmp_path / 'a', 1, 11)
    _write_set(tmp_path / 'b', 2, 11)

    def load(name):
        path = str(tmp_path / name)
        return load_recipe_data({'name': name, 'short_name': name,
                                 'rounds': [{'name': '1st', 'path': path}]})

    eleven, ten = load('a'), load('b')

    assert eleven['first_repeat_excluded'] == 'Sample1001'
    assert ten['first_repeat_excluded'] is None
    assert len(_lots(eleven['raw_data'])) == len(_lots(ten['raw_data'])) == 10
    assert eleven['statistics'] == ten['statistics']


# ── 측정 순서 ─────────────────────────────────────────────────────────────

def _write_ordered_lot(root, name, repeat_no, minute=None):
    lot = root / name
    lot.mkdir(parents=True)
    start = f'Start Time,2026.06.11 10:{minute:02d}:00\n' if minute is not None else ''
    for axis in ('X', 'Y'):
        (lot / f'Lot6_{axis}_UL.csv').write_text(
            f'Lot ID,Lot6\nRepeat No,{repeat_no}\n{start}\n'
            'Site ID,Site X,Site Y,Point No,X (um),Y (um),Method ID,State,Valid,'
            'HZ1_O (nm),HZ1_O_Valid\n'
            f'0001_X000_Y000,0,0,1,1,1,{axis},COMPLETED,TRUE,1.0,TRUE\n'
            f'0002_X000_Y000,0,0,1,1,1,{axis},COMPLETED,TRUE,1.0,TRUE\n',
            encoding='utf-8')


def _lot6_names(count):
    return ['Lot6'] + [f'Lot6 ({n})' for n in range(2, count + 1)]


def _order(rows):
    seen = []
    for r in rows:
        if r['lot_name'] not in seen:
            seen.append(r['lot_name'])
    return seen


def test_copy_suffix_names_keep_measurement_order_by_start_time(tmp_path):
    """Lot6, Lot6 (2) ... 형식: 마지막 숫자로 정렬하면 Lot6이 여섯째로 밀린다."""
    names = _lot6_names(10)
    for i, name in enumerate(names):
        _write_ordered_lot(tmp_path, name, i + 2, minute=i)

    assert _order(batch_load(str(tmp_path))) == names


def test_copy_suffix_names_keep_order_without_start_time(tmp_path):
    names = _lot6_names(10)
    for i, name in enumerate(names):
        _write_ordered_lot(tmp_path, name, i + 2)

    assert _order(batch_load(str(tmp_path))) == names


def test_eleven_copy_suffix_folders_drop_the_real_first_repeat(tmp_path):
    names = _lot6_names(11)
    for i, name in enumerate(names):
        _write_ordered_lot(tmp_path, name, i + 1, minute=i)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert info['first_repeat_excluded'] == 'Lot6'
    assert _order(rows) == names[1:]


def test_eleven_copy_suffix_folders_without_start_time_drop_first_repeat(tmp_path):
    names = _lot6_names(11)
    for i, name in enumerate(names):
        _write_ordered_lot(tmp_path, name, i + 1)
    info = {}

    batch_load(str(tmp_path), info=info)

    assert info['first_repeat_excluded'] == 'Lot6'


def test_start_time_overrides_misleading_folder_names(tmp_path):
    _write_ordered_lot(tmp_path, 'Sample2', 3, minute=2)
    _write_ordered_lot(tmp_path, 'Sample9', 2, minute=1)

    assert _order(batch_load(str(tmp_path))) == ['Sample9', 'Sample2']


def test_missing_start_time_in_one_folder_falls_back_to_repeat_no(tmp_path):
    _write_ordered_lot(tmp_path, 'Sample2', 3, minute=2)
    _write_ordered_lot(tmp_path, 'Sample9', 2)

    assert _order(batch_load(str(tmp_path))) == ['Sample9', 'Sample2']


def _real_shape_names():
    """실측 LLC 폴더 형식: Lot6 (2) ~ Lot6 (10)이 Repeat 2~10, 괄호 없는 Lot6이 Repeat 11."""
    return [f'Lot6 ({n})' for n in range(2, 11)] + ['Lot6']


def test_real_shape_bare_name_is_last_by_start_time(tmp_path):
    names = _real_shape_names()
    for i, name in enumerate(names):
        _write_ordered_lot(tmp_path, name, i + 2, minute=i)

    assert _order(batch_load(str(tmp_path))) == names


def test_real_shape_bare_name_is_last_by_repeat_no(tmp_path):
    names = _real_shape_names()
    for i, name in enumerate(names):
        _write_ordered_lot(tmp_path, name, i + 2)

    assert _order(batch_load(str(tmp_path))) == names


def test_real_shape_ten_folders_are_not_dropped(tmp_path):
    names = _real_shape_names()
    for i, name in enumerate(names):
        _write_ordered_lot(tmp_path, name, i + 2, minute=i)
    info = {}

    rows = batch_load(str(tmp_path), info=info)

    assert len(_order(rows)) == 10
    assert 'first_repeat_excluded' not in info
