# -*- mode: python ; coding: utf-8 -*-
"""
XYStageOffset.spec — PyInstaller 정본 빌드 정의 (onefile / windowed)

빌드:  build.bat  또는  python -m PyInstaller --noconfirm XYStageOffset.spec
검증:  python tools/verify_build.py

번들 리소스·hidden import 값은 tools/build_config.py 하나에만 존재한다.
이 spec은 그 값을 읽어 쓰기만 하므로, 설정을 바꿀 때 이 파일을 고치지 않는다.
경로는 SPECPATH 기준 상대경로로 해석되므로 다른 PC에서도 그대로 동작한다.
"""

import os
import sys

sys.path.insert(0, os.path.join(SPECPATH, 'tools'))
import build_config as cfg

from PyInstaller.utils.hooks import collect_all

datas = [(os.path.join(SPECPATH, src), dest) for src, dest in cfg.DATAS]
binaries = []
hiddenimports = list(cfg.HIDDEN_IMPORTS)

# --collect-all 대응. 설치돼 있지 않으면 건너뛴다
# (pspylib이 없어도 앱은 기동하며 TIFF 보기만 비활성된다)
for pkg in cfg.COLLECT_ALL:
    try:
        p_datas, p_binaries, p_hidden = collect_all(pkg)
    except Exception as e:
        print(f'[build_config] --collect-all {pkg} 건너뜀: {e}')
        continue
    datas += p_datas
    binaries += p_binaries
    hiddenimports += p_hidden

missing = cfg.missing_sources()
if missing:
    raise SystemExit(f'[build_config] 번들 원본을 찾을 수 없습니다: {missing}')


a = Analysis(
    [os.path.join(SPECPATH, cfg.ENTRY_SCRIPT)],
    pathex=[os.path.join(SPECPATH, 'src')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=cfg.EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
