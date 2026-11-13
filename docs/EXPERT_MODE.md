# 전문 분석 창 (XY 전문 분석 — QC 엔지니어) — 내부 문서

> 대상: PASS 담당·QC 엔지니어. **작업자(오퍼레이터) 교육 자료·Analysis Guide에는 옮기지 않는다.**
> 결정: 2026-11-13 (Advanced 카테고리와 원인 분석 도구를 작업자 화면에서 숨김)

## 1. 왜 나눴나

작업자는 체크리스트에 값을 적고 PASS/FAIL을 확인하면 된다. Pareto·Correlation·3D Surface·Positioning Log·Diagnosis 같은
심화 분석과 LLM용 Markdown·이미지 이동량·3D 보고서·회전 시험 같은 도구는 원인 조사용이라 작업자 화면에서는 혼란만 준다.
그래서 메인 창(작업자 화면)에는 **Basic Analysis, Interactive, Comparison, Remark**만 두고, 나머지는 별도 창에 둔다.

| 화면 | 내용 |
|------|------|
| 메인 창 → Remark → Export | Excel Export, Quality Report, CSV Export, PDF Report (4개만) |
| 메인 창 → Remark → Help | 프로그램 정보, Analysis Guide (전문 분석 기능은 적지 않음), 체크리스트 기입 방법 |
| 전문 분석 창 → Advanced | Pareto, Correlation, 3D Surface, Positioning Log, Diagnosis (트러블슈팅) |
| 전문 분석 창 → 도구 | AI 분석용 Markdown, 이미지 2장 이동량 분석, 3D 트러블슈팅 보고서, 사례 라이브러리, 스캐너 회전 시험 분석 |
| 전문 분석 창 → 도움말 | 이 창 안내, X/Y Correlation 해석, AI 분석용 Markdown, 비전 보정 로그 대조, 트러블슈팅 진단, 도구 |

## 2. 여는 방법 (숨은 입구)

1. 메인 창 **오른쪽 아래 상태 표시줄의 버전 글자**(예: `v1.1.3`)를 찾는다.
2. 그 글자를 **3초 안에 5번** 왼쪽 클릭한다. (더블클릭처럼 빠르게 눌러도 한 번씩 센다. 오른쪽 클릭은 세지 않는다.)
3. 'XY 전문 분석 (QC 엔지니어)' 창이 열린다.

- 비밀번호는 없다. 메뉴·툴팁·마우스 커서 변화·System Log 기록 등 **작업자에게 보이는 표시는 없다.**
- 4번만 누르거나, 5번을 3초보다 길게 나눠 누르면 열리지 않는다.
- 이미 열려 있으면(뒤에 있거나 최소화돼 있어도) 새 창을 만들지 않고 **그 창을 앞으로 가져온다** (창은 하나).
- 작업자 앞에서 열지 말고, 작업자에게 방법을 알리지 않는다.

## 3. 쓰는 법

- **데이터**: 늘 메인 창에서 고른 Step·회차·Die 필터의 결과를 보인다. 메인 창에서 Step·회차·Die 필터를 바꾸면 이 창도 같은
  경로로 바로 바뀐다. 스캔도 메인 창에서 한다.
- **Log 읽기**: Positioning Log·Diagnosis 탭의 Lot Log(네트워크 폴더)는 **이 창이 열려 있고(최소화 아님) 그 탭이 앞에 있을 때만**
  읽는다. 창을 닫거나 최소화해 두면 메인 창에서 Step을 바꿔도 새 읽기·진단 계산·차트 그리기를 시작하지 않고, 다시 열면 이어서 한다.
- **닫기**: 창의 닫기(X)는 숨기기만 한다. 읽은 Log·진단·도구 창의 입력값은 그대로 남아 다시 열면 이어서 볼 수 있다.
  메인 창을 닫으면 함께 닫힌다.
- **도구**: 결과 창·파일 대화상자·메시지는 이 창 위에 뜬다. Diagnosis 탭 위쪽의 '3D 보고서 열기·스캐너 회전 시험·사례 라이브러리'
  버튼과 도구 탭의 같은 이름 버튼은 같은 창을 연다.
- **위치·크기**: 닫을 때(또는 앱을 끝낼 때) 사용자 설정 `expert_window_geometry`에 기억하고, 다음에 처음 열 때 화면에 맞춰 복원한다.
  소스 실행은 `src/core/settings.local.json`, 빌드본은 레지스트리 `HKCU\Software\XYStageOffset`. 번들 `settings.json`에는 넣지 않는다.

## 4. 구현 (개발자용)

자세한 내용은 `docs/ARCHITECTURE.md` 7.3절.

| 위치 | 역할 |
|------|------|
| `src/ui/controllers/expert_controller.py` (`ExpertMixin`) | 창 만들기, 클릭 세기(`UNLOCK_CLICKS` 5, `UNLOCK_SECONDS` 3.0), 한 창 열기, 보임 판단 `_advanced_tab_visible`, flush 연결, 위치 기억 |
| `src/ui/dialogs/expert_window.py` (`ExpertWindow`) | 창(QMainWindow, 메인 창 소유). `became_visible` 시그널, 닫으면 숨김, 도구가 부모에서 찾는 값을 메인 창에서 넘김 |
| `src/ui/dialogs/expert_guide.py` | '도움말' 탭 내용 (`EXPERT_GUIDE`) |
| `src/ui/controllers/ui_builder_mixin.py` | Advanced 탭을 `chart_category_tabs`에 넣지 않고 전문 분석 창에 담음, Export 4개, 버전 글자 이벤트 필터 |
| `src/main.py` | `eventFilter` → `_on_version_label_event`, `closeEvent`가 먼저 전문 분석 창을 닫음, `_restore_geometry(창, 키)` |
| `tests/test_expert_window.py` | 입구·한 창·데이터 따라가기·지연 읽기·도구 버튼·닫기·위치 저장 회귀 테스트 |

테스트에서 Advanced 탭을 열 때는 `tests/conftest.py`의 `show_expert_tab(window, 탭 이름)`을 쓴다
(전문 분석 창을 열고 그 탭을 앞에 둔다).
