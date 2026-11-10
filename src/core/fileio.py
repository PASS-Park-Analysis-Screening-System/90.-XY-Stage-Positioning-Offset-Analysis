"""
fileio.py — 내보내기 파일 쓰기 (화면·명령줄 공용, Qt를 쓰지 않는다)
"""

import os
import tempfile


def write_atomic(target, text, encoding, newline):
    """같은 폴더의 고유한 임시 파일에 쓰고 옮긴다. 실패해도 불완전한 파일이 대상 경로에 남지 않는다."""
    fd, temp_path = tempfile.mkstemp(
        prefix=os.path.basename(target) + '.', suffix='.part',
        dir=os.path.dirname(os.path.abspath(target)))
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline=newline) as f:
            f.write(text)
        os.replace(temp_path, target)
    finally:
        # 정리 실패가 원래 예외를 가리지 않게 한다
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
