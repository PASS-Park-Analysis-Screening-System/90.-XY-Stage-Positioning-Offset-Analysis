"""번들 설정에 개인·고객 경로가 들어가지 않게 막는 장치의 회귀 테스트."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from core import settings

PROJECT = Path(__file__).resolve().parents[1]


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, PROJECT / 'tools' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cfg = _tool('build_config')
USER_VALUES = {'last_folder': 'D:/data', 'recent_folders': ['D:/data'],
               'window_geometry': '800x600+1+2', 'wafer_size': 200}


@pytest.fixture
def source_mode(tmp_path, monkeypatch):
    bundled = tmp_path / 'settings.json'
    bundled.write_text(json.dumps({'last_folder': '', 'recent_folders': [], 'wafer_size': 300,
                                   'spec_deviation': {'A': {'spec_range': 1.0}}}), encoding='utf-8')
    local = tmp_path / 'settings.local.json'
    monkeypatch.setattr(settings, 'IS_FROZEN', False)
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(bundled))
    monkeypatch.setattr(settings, 'LOCAL_SETTINGS_FILE', str(local))
    return bundled, local


def test_committed_settings_have_no_private_values():
    """실패하면 작업 트리의 settings.json이 번들에 들어갈 수 없는 상태다."""
    assert cfg.settings_source_errors() == []


def test_source_run_save_does_not_touch_bundle_source(source_mode):
    bundled, local = source_mode
    before = hashlib.sha256(bundled.read_bytes()).hexdigest()

    loaded = settings.load_settings()
    loaded.update(USER_VALUES)
    settings.save_settings(loaded)

    assert hashlib.sha256(bundled.read_bytes()).hexdigest() == before
    assert json.loads(local.read_text(encoding='utf-8')) == USER_VALUES


def test_source_run_restores_user_values_and_keeps_specs_from_bundle(source_mode):
    _, local = source_mode
    local.write_text(json.dumps({**USER_VALUES, 'spec_deviation': {'A': {'spec_range': 99}}}),
                     encoding='utf-8')

    loaded = settings.load_settings()

    assert loaded['last_folder'] == 'D:/data'
    assert loaded['recent_folders'] == ['D:/data']
    assert loaded['wafer_size'] == 200
    assert loaded['spec_deviation'] == {'A': {'spec_range': 1.0}}


@pytest.mark.parametrize('content', [
    b'{broken', b'[1, 2]',
    '{"last_folder": "D:/측정"}'.encode('cp949'),
])
def test_damaged_local_file_falls_back_to_bundle(source_mode, content):
    _, local = source_mode
    local.write_bytes(content)

    loaded = settings.load_settings()

    assert loaded['wafer_size'] == 300
    assert loaded['last_folder'] == ''


@pytest.mark.parametrize('bad', [
    {'recent_folders': 'D:/x'}, {'recent_folders': None}, {'recent_folders': [1]},
    {'wafer_size': '200'}, {'wafer_size': True}, {'last_folder': 3},
])
def test_wrong_type_in_local_file_is_ignored(source_mode, bad):
    _, local = source_mode
    local.write_text(json.dumps(bad), encoding='utf-8')

    loaded = settings.load_settings()

    assert loaded['recent_folders'] == []
    assert loaded['wafer_size'] == 300
    assert settings.add_recent_folder(loaded, 'D:/y')['recent_folders'] == ['D:/y']


def test_local_file_with_bom_is_read(source_mode):
    _, local = source_mode
    local.write_bytes(json.dumps({'wafer_size': 200}).encode('utf-8-sig'))

    assert settings.load_settings()['wafer_size'] == 200


def test_frozen_app_ignores_paths_that_slipped_into_bundle(tmp_path, monkeypatch):
    bundled = tmp_path / 'settings.json'
    bundled.write_text(json.dumps({'last_folder': 'Z:/customer', 'recent_folders': ['Z:/customer'],
                                   'wafer_size': 300}), encoding='utf-8')
    monkeypatch.setattr(settings, 'IS_FROZEN', True)
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(bundled))
    monkeypatch.setattr(settings, '_read_registry_settings', lambda: {})

    loaded = settings.load_settings()

    assert loaded['last_folder'] == ''
    assert loaded['recent_folders'] == []
    assert loaded['wafer_size'] == 300


def test_frozen_app_still_uses_registry_paths(tmp_path, monkeypatch):
    bundled = tmp_path / 'settings.json'
    bundled.write_text(json.dumps({'last_folder': '', 'recent_folders': []}), encoding='utf-8')
    monkeypatch.setattr(settings, 'IS_FROZEN', True)
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(bundled))
    monkeypatch.setattr(settings, '_read_registry_settings',
                        lambda: {'last_folder': 'D:/mine', 'recent_folders': ['D:/mine']})

    loaded = settings.load_settings()

    assert loaded['last_folder'] == 'D:/mine'
    assert loaded['recent_folders'] == ['D:/mine']


@pytest.mark.parametrize('payload,where', [
    ({'last_folder': 'relative-name'}, 'last_folder'),
    ({'recent_folders': ['anything']}, 'recent_folders'),
    ({'extra': 'C:/Users/someone/data'}, 'extra'),
    ({'extra': {'nested': ['Z:\\MC\\customer']}}, 'extra.nested[0]'),
    ({'extra': '\\\\10.60.20.11\\share'}, 'extra'),
    ({'extra': '//10.60.20.11/share'}, 'extra'),
    ({'extra': 'see D:\\customer\\a'}, 'extra'),
    ({'extra': 'copy of \\\\10.60.20.11\\share'}, 'extra'),
    ({'extra': 'smb://10.60.20.11/share'}, 'extra'),
    ({'extra': '/home/someone/data'}, 'extra'),
    ({'D:/customer/recipe': 1}, '<키>'),
    ({'spec': {'C:\\Users\\someone': 1}}, 'spec.<키>'),
])
def test_privacy_check_finds_private_values(payload, where):
    errors = cfg.settings_privacy_errors(payload)

    assert any(e.startswith(where) for e in errors)
    # 검사 결과가 로그에 경로를 옮겨 적지 않는다
    assert not any('customer' in e or 'someone' in e or '10.60' in e for e in errors)


def test_privacy_check_accepts_clean_values():
    assert cfg.settings_privacy_errors({
        'last_folder': '', 'recent_folders': [], 'window_geometry': '1920x1009+0+23',
        'export_delimiter': '\t', 'spec_deviation': {'In-Die Align': {'spec_range': 2.0}},
    }) == []


def test_spec_runs_the_check_before_building():
    text = (PROJECT / 'XYStageOffset.spec').read_text(encoding='utf-8')

    assert text.index('settings_source_errors') < text.index('a = Analysis(')


def test_local_settings_stay_out_of_git_and_bundle():
    assert 'src/core/settings.local.json' in (PROJECT / '.gitignore').read_text(encoding='utf-8')
    assert not any('settings.local' in src for src, _ in cfg.DATAS)


def test_verify_build_fails_exe_with_private_settings(tmp_path, monkeypatch):
    verify_build = _tool('verify_build')
    exe = tmp_path / 'app.exe'
    exe.write_bytes(b'')
    monkeypatch.setattr(verify_build, 'read_bundle', lambda _: [
        'assets/checklist_guide.png', 'core/settings.json',
        'resources/troubleshoot/report_template.html', 'resources/cases/index.html'])

    monkeypatch.setattr(verify_build, 'read_bundled_settings', lambda _: {'last_folder': 'Z:/x'})
    assert verify_build.verify(str(exe)) == 1

    monkeypatch.setattr(verify_build, 'read_bundled_settings', lambda _: {'last_folder': ''})
    assert verify_build.verify(str(exe)) == 0


def test_verify_build_reads_onedir_internal_folder(tmp_path):
    verify_build = _tool('verify_build')
    exe = tmp_path / 'app.exe'
    exe.write_bytes(b'')
    internal = tmp_path / '_internal'
    for required in _tool('build_config').REQUIRED_IN_BUNDLE:     # assets, settings, resources/...
        (internal / required).parent.mkdir(parents=True, exist_ok=True)
        (internal / required).write_bytes(b'x')
    settings_file = internal / 'core' / 'settings.json'

    settings_file.write_text(json.dumps({'last_folder': ''}), encoding='utf-8')
    assert verify_build.verify(str(exe)) == 0

    settings_file.write_text(json.dumps({'last_folder': 'Z:/x'}), encoding='utf-8')
    assert verify_build.verify(str(exe)) == 1

    (internal / 'assets' / 'checklist_guide.png').unlink()
    assert verify_build.verify(str(exe)) == 1


def test_migrate_preserves_values_without_touching_source(tmp_path):
    migrate = _tool('migrate_local_settings').migrate
    source = tmp_path / 'settings.json'
    target = tmp_path / 'settings.local.json'
    source.write_text(json.dumps({**USER_VALUES, 'spec_deviation': {}}), encoding='utf-8')
    before = source.read_bytes()

    assert migrate(str(source), str(target)) == 'written'
    assert source.read_bytes() == before
    assert json.loads(target.read_text(encoding='utf-8')) == USER_VALUES


def test_migrate_never_overwrites_existing_local_file(tmp_path):
    migrate = _tool('migrate_local_settings').migrate
    source = tmp_path / 'settings.json'
    target = tmp_path / 'settings.local.json'
    source.write_text(json.dumps(USER_VALUES), encoding='utf-8')
    target.write_text('{"last_folder": "keep"}', encoding='utf-8')

    assert migrate(str(source), str(target)) == 'exists'
    assert json.loads(target.read_text(encoding='utf-8')) == {'last_folder': 'keep'}


def test_migrate_with_clean_source_writes_nothing(tmp_path):
    migrate = _tool('migrate_local_settings').migrate
    source = tmp_path / 'settings.json'
    target = tmp_path / 'settings.local.json'
    source.write_text(json.dumps({'last_folder': '', 'recent_folders': []}), encoding='utf-8')

    assert migrate(str(source), str(target)) == 'nothing'
    assert not target.exists()
