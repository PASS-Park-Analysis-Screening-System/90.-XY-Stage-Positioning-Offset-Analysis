"""
migrate_local_settings.py — settings.json에 남은 사용자 환경설정을 settings.local.json으로 옮겨 적는다.

소스 실행은 이제 settings.json을 바꾸지 않는다. 이전 버전이 남긴 경로가 작업 트리에
있으면 이 스크립트로 보존한 뒤, 직접 `git restore src/core/settings.json` 을 실행한다.
이 스크립트는 settings.json을 바꾸거나 지우지 않고, 이미 있는 local 파일을 덮어쓰지 않는다.

사용:  python tools/migrate_local_settings.py
종료 코드: 0 = 옮겼거나 옮길 값이 없음, 1 = local 파일이 이미 있어 중단
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_KEYS = ('last_folder', 'window_geometry', 'recent_folders', 'wafer_size')


def migrate(source: str, target: str) -> str:
    """결과를 'nothing', 'exists', 'written' 중 하나로 돌려준다."""
    with open(source, encoding='utf-8') as f:
        settings = json.load(f)
    if not (settings.get('last_folder') or settings.get('recent_folders')):
        return 'nothing'
    if os.path.exists(target):
        return 'exists'
    user_settings = {key: settings[key] for key in USER_KEYS if key in settings}
    with open(target, 'x', encoding='utf-8') as f:
        json.dump(user_settings, f, indent=2, ensure_ascii=False)
    return 'written'


def main():
    source = os.path.join(ROOT, 'src', 'core', 'settings.json')
    target = os.path.join(ROOT, 'src', 'core', 'settings.local.json')
    result = migrate(source, target)
    if result == 'exists':
        print(f'{target} 가 이미 있어 중단합니다. 두 파일을 비교해 직접 정리하세요.')
        return 1
    if result == 'nothing':
        print('settings.json에 옮길 경로가 없습니다.')
        return 0
    print(f'보존했습니다: {target}')
    print('확인 후 실행하세요: git restore src/core/settings.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
