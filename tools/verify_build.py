"""
verify_build.py — 빌드된 onefile EXE에 필수 리소스가 **기대 경로로** 들어갔는지 검사.

왜 필요한가:
    --add-data의 대상 경로가 틀려도 PyInstaller는 성공으로 끝난다. 실행 중에야
    리소스를 못 찾는데, 그마저 조용히 대체 UI로 넘어가 아무도 모른다.
    실제로 배포된 EXE가 assets를 src\\assets\\ 로 담고 있어 체크리스트 가이드
    이미지가 뜨지 않았고, 아카이브를 열어보기 전까지 발견되지 않았다.

    이 스크립트는 그 조용한 실패를 빌드 직후의 시끄러운 실패로 바꾼다.

사용:
    python tools/verify_build.py                  # dist/<EXE_NAME>.exe 검사
    python tools/verify_build.py <exe 경로>       # 특정 파일 검사
    python tools/verify_build.py <exe 경로> --module-json <module.json 경로>
                                                  # Core module.json 버전도 대조

종료 코드: 0 = 정상, 1 = 리소스 누락/경로 불일치/번들 설정에 개인 값/버전 불일치, 2 = 검사 불가
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_config as cfg


def _norm(p):
    return p.replace('\\', '/').lstrip('./').lower()


def read_bundle(exe_path):
    """onefile EXE 아카이브의 항목 이름 목록."""
    from PyInstaller.archive.readers import CArchiveReader
    return list(CArchiveReader(exe_path).toc)


def read_bundled_settings(exe_path):
    """EXE 안의 core/settings.json 내용."""
    from PyInstaller.archive.readers import CArchiveReader
    reader = CArchiveReader(exe_path)
    wanted = _norm(os.path.join('core', 'settings.json'))
    name = next(n for n in reader.toc if _norm(n) == wanted)
    return json.loads(reader.extract(name))


def verify(exe_path):
    if not os.path.isfile(exe_path):
        print(f'[FAIL] EXE를 찾을 수 없습니다: {exe_path}')
        return 2

    try:
        names = read_bundle(exe_path)
    except Exception as e:
        print(f'[SKIP] 아카이브를 읽을 수 없습니다 ({type(e).__name__}: {e})')
        print('       PyInstaller가 설치된 Python으로 실행하세요.')
        return 2

    index = {_norm(n): n for n in names}
    print(f'검사 대상: {exe_path}')
    print(f'번들 항목 {len(names)}개\n')

    failed = []
    for required in cfg.REQUIRED_IN_BUNDLE:
        key = _norm(required)
        if key in index:
            print(f'  [OK]   {required}')
            continue

        # 파일은 있는데 경로만 다른 경우를 찾아 원인을 바로 보여준다
        base = os.path.basename(key)
        elsewhere = [n for k, n in index.items() if k.endswith('/' + base) or k == base]
        if elsewhere:
            print(f'  [FAIL] {required}')
            print(f'         → 번들에는 {elsewhere[0]} 로 들어가 있습니다.')
            print(f'         → tools/build_config.py 의 DATAS 대상 경로를 확인하세요.')
        else:
            print(f'  [FAIL] {required}  (번들에 없음)')
        failed.append(required)

    print()
    if failed:
        print(f'실패 {len(failed)}건 — 이 EXE는 실행 중 리소스를 찾지 못합니다.')
        return 1
    print(f'필수 리소스 {len(cfg.REQUIRED_IN_BUNDLE)}건 모두 정상입니다.')

    try:
        private = cfg.settings_privacy_errors(read_bundled_settings(exe_path))
    except Exception as e:
        print(f'[FAIL] 번들 설정을 읽을 수 없습니다 ({type(e).__name__}: {e})')
        return 2
    if private:
        print('[FAIL] 번들 설정에 개인 값이 있습니다. 이 EXE를 배포하지 마세요.')
        for line in private:
            print(f'         → {line}')
        return 1
    print('번들 설정에 개인 값이 없습니다.')
    return 0


def main():
    args = sys.argv[1:]
    module_json = None
    if '--module-json' in args:
        at = args.index('--module-json')
        module_json = args[at + 1]
        del args[at:at + 2]
    exe_path = args[0] if args else os.path.join(cfg.ROOT, 'dist', cfg.EXE_NAME + '.exe')

    code = verify(exe_path)
    if module_json:
        mismatch = cfg.module_version_error(module_json)
        if mismatch:
            print(f'[FAIL] {mismatch}')
            return code or 1
        print(f'module.json 버전이 앱 버전 {cfg.app_version()}과 같습니다.')
    return code


if __name__ == '__main__':
    sys.exit(main())
