"""
print_build_config.py — 외부 빌드 매니저(PASS)에 입력할 값을 출력한다.

빌드 매니저가 자체 필드(Hidden Imports / Add Data / requirements)를 요구할 때,
그 값을 사람이 소스를 읽어 유추하지 않도록 저장소가 직접 알려주기 위한 것이다.
예컨대 assets의 대상이 'assets'여야 하는 근거는 ui_builder_mixin.py의 __file__
상대경로에 있어서, UI만 보고는 알 수 없다.

사용:  python tools/print_build_config.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_config as cfg


def main():
    add_data = ', '.join(f'{src.replace(os.sep, "/")};{dest}'
                         for src, dest in cfg.DATAS)

    print('PASS 빌드 매니저 - 모듈 편집 입력값')
    print('=' * 60)
    print(f'  빌드 진입점    : {cfg.ENTRY_SCRIPT.replace(os.sep, "/")}')
    print(f'  빌드 이름      : {cfg.EXE_NAME}')
    print(f'  옵션           : --onefile, --windowed')
    print(f'  Hidden Imports : {", ".join(cfg.HIDDEN_IMPORTS)}')
    print(f'  Add Data       : {add_data}')
    print(f'  requirements   : requirements.txt')
    print('=' * 60)
    print()
    print('참고: build.bat / XYStageOffset.spec 로 빌드하면 위 값이 자동 적용되므로')
    print('      매니저 필드는 비워 두어도 된다. 필드 입력이 필요한 경우에만 사용할 것.')
    print()
    print(f'--collect-all  : {", ".join(cfg.COLLECT_ALL)}')
    print('      (매니저에 해당 필드가 없으면 Hidden Imports로 대체하거나')
    print('       build.bat 실행 방식을 쓸 것)')

    missing = cfg.missing_sources()
    if missing:
        print()
        print(f'[경고] 번들 원본이 저장소에 없습니다: {missing}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
