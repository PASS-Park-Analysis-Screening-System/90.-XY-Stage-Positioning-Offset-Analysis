# 2026-11 XY 스캐너 회전 사례 (공개용 사본)

XY 위치 맞춤(Vision Pattern) 시험의 X Spec Out을 조사한 HTML 페이지 4개입니다. 앱의
전문 분석 창 → 도구 → "사례 라이브러리"로 열리는 목록(`../index.html`)에서 링크합니다.

| 파일 | 내용 |
|---|---|
| `xy-explainer-3d.html` | 장비 3D로 보는 원인과 트러블슈팅 (처음 보는 분께 권장) |
| `xy-where-is-the-problem.html` | 어디가 문제인가 — 그림으로 짚어 보기 |
| `xy-root-cause-troubleshooting.html` | 원인과 트러블슈팅 (v2) |
| `xy-root-cause-final.html` | 근본 원인 분석 — 최종 보고 (상세) |

페이지끼리의 링크는 같은 폴더 안의 상대 경로라 폴더째 옮겨도 동작합니다.

## 원본 위치

원본은 PASS 작업 폴더의 `.omc/artifacts/` 아래 같은 이름의 파일입니다 (Git 저장소 밖, 비공개).
원본을 고치면 이 폴더로 다시 복사한 뒤 아래 정리를 다시 하고 `tests/test_troubleshoot_report.py`의
개인정보 검사를 돌립니다.

## 사본에서 바꾼 것

- 고객사 이름 → "고객사 장비"
- 장비 일련번호 삭제
- 분석 PC의 절대 경로 → 상대 경로 `.omc/research/xy-rawall/`
- 공유 폴더 서버 경로는 원본에도 없었음 (있으면 "공유 폴더"로 바꿈)

수치·장비 모델(Park NX-Wafer 300 mm)·그림은 원본과 같습니다.
