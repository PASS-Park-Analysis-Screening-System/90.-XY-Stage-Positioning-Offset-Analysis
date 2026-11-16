# XY Stage Positioning Offset Analysis — 작업 안내 (AI 세션용)

PASS Tool `xy_stage_offset`이다. XY 스테이지 위치 결정 시험(Vision Pattern, In-Die Align, LLC Translation, Global Align)의 Die × Repeat 편차를 분석하고 판정한다. 화면은 PySide6로 만들었다.
답변과 문서는 **한국어**로 쓴다.

## 먼저 읽을 것
1. Tool PRD: Core 저장소 `doc/handbook/tools/xy_stage_offset/PRD.md`. 목적, 판정, 버전 이력, 현재 상태, 결정 기록이 있다
2. 버전 현황: Core `doc/handbook/STATUS.md`. 서버 dev·stable 버전과 릴리스 커밋
3. 운영·배포 규칙: Core `doc/handbook/OPERATIONS_PRD.md`, `BUILD_AND_RELEASE.md`, skill `pass-release`
4. 이 저장소 문서
   - `docs/ARCHITECTURE.md`: 구조
   - `docs/EXPERT_MODE.md`: 숨김 전문 분석 창. 내부 문서다
   - `docs/DATA_ANALYSIS_GUIDE.md`: 분석 방법

(Core 저장소는 개발 루트 `${DEV_ROOT}/01. Park Analyzer`)

## 명령
| 목적 | 명령 |
|---|---|
| 소스 실행 (화면 확인) | `src` 폴더에서 `python main.py` |
| 관련 테스트 | `python -m pytest -n 4 tests/<파일>` (병렬 4 이하) |
| 전체 테스트 | `python -m pytest -n 4` (빌드 전에 1회) |
| 빌드 | Core의 `tools/release/build_tool.py xy_stage_offset --dev-path <깨끗한 worktree>`. Core `module.json`의 build 설정을 쓴다 |
| 빌드 검사 | `python tools/verify_build.py <exe> --module-json <Core modules/xy_stage_offset/module.json>` |

## 지켜야 할 것
- **버전 단일 출처는 `src/core/version.py`이다.** Core `module.json`의 `version`과 같아야 한다. 올리기 전에 서버 카탈로그의 현재 버전을 확인한다(1.1.4 번호 겹침 사례).
- **개인 경로**
  - 소스 실행의 사용자 값은 `src/core/settings.local.json`(git·번들 제외)에, 빌드본은 레지스트리 `HKCU\Software\XYStageOffset`에 저장한다.
  - `src/core/settings.json`에 경로를 넣거나 커밋하지 않는다. 빌드 전후 검사(`tools/build_config.py`, `tools/verify_build.py`)가 막는다.
- **새 리소스 폴더**를 추가하면 세 곳을 같이 고친다. 빠지면 빌드본에서 기능이 조용히 실패한다.
  - `tools/build_config.py`의 `DATAS`와 `REQUIRED_IN_BUNDLE`
  - Core `module.json`의 `build.add_data`
- **사례 페이지**(`src/resources/cases/`)에 넣지 않는 것: 고객사 이름, 장비 일련번호, 공유 폴더 주소·경로, PC 경로. `tests/test_troubleshoot_report.py`가 검사한다.
- **전문 분석 창은 작업자에게 숨긴다.** 작업자 가이드와 changelog(release notes)에 진입 방법을 적지 않는다.
- **현장 원본 공유 폴더는 읽기 전용**이다. 앱과 스크립트가 쓰기·이름 변경·삭제를 하지 않는다.
- 작업은 `feature/<이름>` 브랜치와 worktree(`_wt_<이름>`)에서 한다. `main`은 배포본이다.
- 화면을 바꾸면 빌드 전에 소스 실행으로 리더 확인을 받는다.

## 끝낼 때
- 릴리스했다면 Tool PRD의 버전 이력과 현재 상태를 갱신하고, Core에서 `python tools/handbook/gen_status.py`를 실행한다.
- 사용자 결정이 나오면 PRD 결정 기록에 날짜와 함께 남긴다.
