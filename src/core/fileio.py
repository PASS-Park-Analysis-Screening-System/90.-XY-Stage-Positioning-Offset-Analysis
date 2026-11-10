"""
fileio.py — 내보내기 파일 쓰기 (화면·명령줄 공용, Qt를 쓰지 않는다)
"""

import os


def write_atomic(target, text, encoding, newline):
    """임시 파일(target.part)에 쓰고 옮긴다. 실패해도 불완전한 파일이 대상 경로에 남지 않는다."""
    temp_path = target + '.part'
    try:
        with open(temp_path, 'w', encoding=encoding, newline=newline) as f:
            f.write(text)
        os.replace(temp_path, target)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
