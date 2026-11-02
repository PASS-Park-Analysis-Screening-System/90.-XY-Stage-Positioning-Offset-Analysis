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
sys.path.insert(0, os.path.join(ROOT, 'src'))
from core.settings import _USER_SETTING_KEYS  # noqa: E402


def migrate(source: str, target: str) -> str:
    """결과를 'nothing', 'exists', 'written' 중 하나로 돌려준다."""
    with open(source, encoding='utf-8') as f:
        settings = json.load(f)
    if not (settings.get('last_folder') or settings.get('recent_folders')):
        return 'nothing'
    if os.path.exists(target):
        return 'exists'
    user_settings = {key: settings[key] for key in sorted(_USER_SETTING_KEYS) if key in settings}
    with open(target, 'x', encoding='utf-8') as f:
        json.dump(user_settings, f, indent=2, ensure_ascii=False)
    return 'written'


def main():
    source = os.path.join(ROOT, 'src', 'core', 'settings.json')
    target = os.path.join(ROOT, 'src', 'core', 'settings.local.json')
    result = migrate(source, target)
    if result == 'exists':
        print(f'{target} 가 이미 있어 중단합니다.')
        print('소스 실행이 이미 저장했을 수 있습니다. 필요한 경로가 그 파일에 있으면')
        print('git restore src/core/settings.json 을 실행하세요.')
        return 1
    if result == 'nothing':
        print('last_folder, recent_folders에 옮길 값이 없습니다. '
              '빌드가 다른 키 때문에 막히면 그 값은 직접 지우세요.')
        return 0
    print(f'보존했습니다: {target}')
    print('확인 후 실행하세요: git restore src/core/settings.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
