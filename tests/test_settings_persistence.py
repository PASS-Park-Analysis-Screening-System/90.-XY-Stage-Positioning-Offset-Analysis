"""onefile 내장 스펙 및 Windows 레지스트리 사용자 설정 회귀 테스트."""

import json
import os
from pathlib import Path

from core import settings


class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    REG_SZ = 1
    REG_DWORD = 4

    def __init__(self):
        self.values = {}

    def CreateKey(self, root, path):
        assert root is self.HKEY_CURRENT_USER
        assert path == r'Software\XYStageOffset'
        return _FakeKey()

    def OpenKey(self, root, path):
        assert root is self.HKEY_CURRENT_USER
        assert path == r'Software\XYStageOffset'
        if not self.values:
            raise FileNotFoundError(path)
        return _FakeKey()

    def SetValueEx(self, key, name, reserved, value_type, value):
        self.values[name] = (value, value_type)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding='utf-8')


def _load_build_config():
    """tools/build_config.py — 빌드 설정의 단일 출처."""
    import importlib.util

    project_dir = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'build_config', project_dir / 'tools' / 'build_config.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_config_embeds_settings_in_core_directory():
    """settings.json은 _MEIPASS/core/settings.json 으로 들어가야 한다.

    (아래 test_frozen_resource_path_matches_packaged_core_directory가 검증하는
    런타임 조회 경로와 짝을 이룬다)
    """
    cfg = _load_build_config()
    datas = {src.replace(os.sep, '/'): dest for src, dest in cfg.DATAS}

    assert datas['src/core/settings.json'] == 'core'


def test_build_config_bundles_assets_at_runtime_lookup_path():
    """assets 대상은 'assets' 여야 한다.

    ui_builder_mixin이 __file__ 기준 ../../assets 로 찾으므로 런타임 경로는
    _MEIPASS/assets 다. 이전에는 대상이 src/assets 여서 배포된 EXE에서
    가이드 이미지가 뜨지 않았다. 같은 실수를 다시 하지 않도록 고정한다.
    """
    cfg = _load_build_config()
    datas = {src.replace(os.sep, '/'): dest for src, dest in cfg.DATAS}

    assert datas['src/assets'] == 'assets'
    assert 'assets/checklist_guide.png' in [
        p.replace(os.sep, '/') for p in cfg.REQUIRED_IN_BUNDLE]


def test_build_config_sources_exist_in_repo():
    """번들 원본이 실제로 저장소에 있어야 빌드가 성공한다."""
    cfg = _load_build_config()
    assert cfg.missing_sources() == []


def test_spec_is_onefile_and_windowed():
    project_dir = Path(__file__).resolve().parents[1]
    spec_text = (project_dir / 'XYStageOffset.spec').read_text(encoding='utf-8')

    # onefile = 모든 binaries/datas를 EXE에 넣고 COLLECT 단계를 두지 않는다
    assert 'COLLECT(' not in spec_text
    assert 'a.binaries' in spec_text and 'a.datas' in spec_text
    assert 'console=False' in spec_text


def test_build_script_runs_spec_and_cleans_stray_settings():
    project_dir = Path(__file__).resolve().parents[1]
    build_script = (project_dir / 'build.bat').read_text(encoding='utf-8')

    assert 'XYStageOffset.spec' in build_script
    assert 'del /q "%PROJECT_DIR%dist\\settings.json"' in build_script
    # 번들 리소스 검증을 건너뛰면 조용히 깨진 EXE가 배포된다
    assert 'verify_build.py' in build_script


def test_frozen_resource_path_matches_packaged_core_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'IS_FROZEN', True)
    monkeypatch.setattr(settings.sys, '_MEIPASS', str(tmp_path), raising=False)

    resolved = settings._resolve_bundled_settings_file()

    assert resolved == str(tmp_path / 'core' / 'settings.json')


def test_source_mode_reads_embedded_settings_json(monkeypatch):
    monkeypatch.setattr(settings, 'IS_FROZEN', False)
    loaded = settings.load_settings()

    assert list(loaded['spec_deviation']) == [
        'Vision Pattern', 'In-Die Align', 'LLC Translation', 'Global Align'
    ]
    assert loaded['spec_deviation']['Vision Pattern'] == {
        'spec_range': 1.0, 'spec_stddev': 0.2
    }


def test_frozen_mode_uses_embedded_specs_and_registry_user_settings(tmp_path, monkeypatch):
    bundled = tmp_path / 'bundled-settings.json'
    legacy_external = tmp_path / 'settings.json'
    expected_specs = {
        'Vision Pattern': {'spec_range': 1.0, 'spec_stddev': 0.2},
        'Global Align': {'spec_range': 6.0, 'spec_stddev': 1.2},
    }
    _write_json(bundled, {
        'spec_deviation': expected_specs,
        'spec_limits': {'Vision Pattern': {'X': {'lsl': -5000, 'usl': 5000}}},
        'last_folder': '',
        'wafer_size': 300,
    })
    _write_json(legacy_external, {
        'spec_deviation': {},
        'last_folder': 'E:/must-not-be-read',
    })
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(bundled))
    monkeypatch.setattr(settings, 'IS_FROZEN', True)
    monkeypatch.setattr(settings, '_read_registry_settings', lambda: {
        'last_folder': 'D:/measurement-data',
        'wafer_size': 200,
        'spec_deviation': {'Vision Pattern': {'spec_range': 99.0}},
    })

    loaded = settings.load_settings()

    assert loaded['spec_deviation'] == expected_specs
    assert loaded['spec_limits']['Vision Pattern']['X']['usl'] == 5000
    assert loaded['last_folder'] == 'D:/measurement-data'
    assert loaded['wafer_size'] == 200


def test_frozen_save_writes_only_allowed_registry_values(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, 'IS_FROZEN', True)
    monkeypatch.setattr(settings, '_write_registry_settings', captured.update)

    settings.save_settings({
        'spec_limits': {'Vision Pattern': {}},
        'spec_deviation': {'Vision Pattern': {'spec_range': 1.0}},
        'standard_recipe_names': ['Vision Pattern'],
        'last_folder': 'D:/measurement-data',
        'window_geometry': '1600x900+0+0',
        'recent_folders': ['D:/measurement-data'],
        'wafer_size': 200,
        'outlier_method': 'iqr',
    })

    assert captured == {
        'last_folder': 'D:/measurement-data',
        'window_geometry': '1600x900+0+0',
        'recent_folders': ['D:/measurement-data'],
        'wafer_size': 200,
    }
    assert not (tmp_path / 'settings.json').exists()


def test_registry_roundtrip_serializes_list_and_integer(monkeypatch):
    fake_registry = _FakeWinreg()
    monkeypatch.setattr(settings, 'winreg', fake_registry)
    expected = {
        'last_folder': 'D:/measurement-data',
        'window_geometry': '1600x900+0+0',
        'recent_folders': ['D:/measurement-data', 'E:/archive'],
        'wafer_size': 200,
    }

    settings._write_registry_settings(expected)
    loaded = settings._read_registry_settings()

    assert loaded == expected
    assert fake_registry.values['recent_folders'][1] == fake_registry.REG_SZ
    assert fake_registry.values['wafer_size'][1] == fake_registry.REG_DWORD


def test_source_save_writes_json_file(tmp_path, monkeypatch):
    target = tmp_path / 'settings.json'
    expected = {
        'spec_deviation': {'Vision Pattern': {'spec_range': 1.0}},
        'wafer_size': 200,
    }
    monkeypatch.setattr(settings, 'IS_FROZEN', False)
    monkeypatch.setattr(settings, 'BUNDLED_SETTINGS_FILE', str(target))

    settings.save_settings(expected)

    assert json.loads(target.read_text(encoding='utf-8')) == expected
