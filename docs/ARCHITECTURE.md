# 🏗️ 아키텍처 참조 문서

> XY Stage Positioning Offset Analysis Tool — v10.0 (Mixin 기반 모듈 아키텍처)  
> 최종 업데이트: 2026-03-09

---

## 1. 프로젝트 구조

```
97. XY Stage Positioning Offset Analysis/
├── src/
│   ├── main.py               # GUI 메인 앱 진입점 (~200 줄)
│   ├── core/                 # 비즈니스 로직 및 엔진
│   │   ├── statistics.py     # 범용 통계 처리 (Cpk, 등)
│   │   ├── die_analysis.py   # Wafer/Die 전용 좌표 연산 엔진
│   │   ├── csv_loader.py     # Lot 파일 탐색 및 CSV 파서
│   │   ├── recipe_scanner.py # Recipe 자동 탐지 구조화
│   │   ├── tiff_loader.py    # PSPylib 전용 TIFF 바이너리 파서
│   │   ├── exporter.py       # CSV, Excel Export
│   │   ├── pdf_generator.py  # PDF 리포트 생성
│   │   ├── llm_report.py     # LLM 원인 분석용 Markdown (report_schema: xy-stage-llm-report/1)
│   │   ├── positioning_log.py # 비전 보정 로그(SamplePositioning·TipPositioning) ↔ HZ1_O 대조
│   │   ├── xy_diagnosis.py   # 트러블슈팅 진단 (스캐너 오프셋·회전·what-if·drift, pass.xy.diagnosis/1)
│   │   └── settings.py       # 사용자 설정 관리
│   ├── charts/               # 시각화 모듈
│   │   ├── basic.py          # 범용 차트 (Matplotlib)
│   │   ├── wafer.py          # Wafer Contour, Vector Map
│   │   ├── comparison.py     # Recipe 비교 차트
│   │   ├── interactive.py    # PyQtGraph 2D 위젯 (Trend, Scatter 등)
│   │   ├── interactive_widgets.py # CrossHair 등 커스텀 위젯 클래스
│   │   └── surface3d.py      # 3D 표면 차트
│   └── ui/                   # UI 컴포넌트 관리
│       ├── controllers/      # 기능 단위 Mixin (10종 결합하여 App 구성)
│       ├── widgets/          # 공통 사용 위젯 (StatCard, SystemLogger 등)
│       ├── dialogs/          # 팝업 대화상자 선언
│       └── theme.py          # CSS 및 색상 상수
├── docs/                     # 문서 폴더 (가이드 및 설계)
```

---

## 2. 모듈 의존 관계

```
main.py (DataAnalyzerApp + 여러 Mixin 상속)
├── ui.controllers.*    (각종 이벤트 및 UI 조작 위임)
├── core.csv_loader     (batch_load_lots 등 데이터 스캔)
├── core.recipe_scanner (scan_recipes, compute_trend)
├── core.statistics     (compute_statistics, compute_cpk 등 범용)
├── core.die_analysis   (compute_deviation_matrix, filter_by_method 등 Die 전용)
├── charts.*            (모든 차트 렌더링 호출)
├── core.exporter       (Excel/CSV 내보내기)
├── core.pdf_generator  (PDF 내보내기)
├── core.llm_report     (AI 분석용 Markdown, Qt 없음)
├── core.xy_diagnosis   (트러블슈팅 진단, Qt 없음 — Diagnosis 탭·LLM 리포트·회차 비교 θ)
├── core.tiff_loader    (TIFF 열기)
└── core.settings       (환경 설정 연동)
```

---

## 3. 데이터 흐름

```
[1] 폴더 선택
    └─ path_edit에 경로 입력
    └─ QThread: ScanWorker 비동기 실행

[2] 폴더 스캔 (ScanWorker)
    └─ recipe_scanner.scan_recipes(folder)
       → recipes[]: [{name, short_name, path, round_paths}, ...]
    └─ 폴더명 검증: normalize_spec_names(recipes, spec_deviation 키)
       → 일치하면 short_name을 정확한 Spec 키로 맞춤
       → 불일치 시 WorkflowFolderDialog(Workflow별 폴더 지정)
         → recipes_from_mapping(mapping) → 지정한 Recipe로 로드 (취소 시 로드 안 함)

[3] 데이터 로드 (ScanWorker)
    └─ 라운드(회차)가 2개 이상이면 load_all_recipes가 회차마다 load_recipe_data
       → 결과 = 기본 회차 결과 + run_results(회차별 전체) + default_run
         (default_round_name: 1st 있으면 1st, 숫자·날짜 회차는 마지막, 그 밖은 첫 라운드)
       → 화면: summarize_runs로 회차 비교 표·Excel Run Comparison, 회차 콤보로 run_view 전환
    └─ csv_loader.batch_load_lots(recipe_path)
       → raw_data[]: [{lot_name, site_id, method, value, is_outlier, valid}, ...]

[4] 분석 (_on_scan_complete)
    └─ analyzer.filter_by_method(data, 'X') → x_data
    └─ analyzer.filter_by_method(data, 'Y') → y_data
    └─ analyzer.compute_statistics(x_data) → {mean, stdev, min, max, cv_percent, count}
    └─ analyzer.compute_deviation_matrix(data, 'X') → {matrix, overall_range, overall_stddev}
    └─ analyzer.compute_cpk(mean, stdev, lsl, usl) → float
    └─ PASS/FAIL: overall_range ≤ spec_range AND overall_stddev ≤ spec_stddev

[5] UI 업데이트
    ├─ StatCard.update_stats()    — X/Y 카드 수치 갱신
    ├─ _update_summary_table()    — Summary 테이블 갱신
    ├─ _update_die_avg_tables()   — Die 히트맵 갱신
    ├─ _update_deviation_tables() — Raw Deviation 매트릭스 갱신
    └─ _display_charts()          — Matplotlib + pyqtgraph 차트 렌더링

[6] Export (선택)
    ├─ exporter.export_excel_report() → .xlsx
    ├─ exporter.export_combined_csv() → .csv
    ├─ pdf_generator.generate_pdf_report() → .pdf
    └─ llm_report.build_llm_report() → .md (front matter + 분석 조건·결과 요약·원인 단서(축별 평면 계수·what-if·평면 잔차)·
       회차 비교·회차 변경 이력(사용자 기입)·부록 선택 회차 Die×Repeat 편차 표·LLM 분석 요청)
       + llm_report.build_raw_csv() → <이름>_raw.csv (모든 Workflow·회차 셀 값, 긴 형식, UTF-8 BOM)
```

---

## 4. UI 레이아웃 구조

```
DataAnalyzerApp (QMainWindow)
│
├─ TopBar (QHBoxLayout)
│   ├─ path_edit (QLineEdit)
│   ├─ btn_browse "Open"
│   └─ btn_scan "🔄 Scan & Analysis"
│
├─ Workflow Nav (QHBoxLayout)
│   └─ step_buttons[]: QPushButton (Recipe별 — PASS🟢/FAIL🔴 표시)
│
└─ QSplitter (Horizontal, 5:5)
    ├─ LEFT PANEL
    │   ├─ StatCard (X)  — Mean, Range, StdDev, Cpk, PASS/FAIL
    │   ├─ StatCard (Y)  — 동일 구조
    │   ├─ btn_spec_config "⚙️ Spec 설정"
    │   ├─ QTabWidget
    │   │   ├─ "시스템 로그" — SystemLog (QTextEdit)
    │   │   └─ "데이터 테이블" — QTabWidget
    │   │       ├─ "Summary"     — CopyableTable
    │   │       ├─ "Die별 평균"  — CopyableTable (X/Y 서브탭)
    │   │       ├─ "Raw Deviation" — CopyableTable (X/Y 서브탭)
    │   │       └─ "원본 데이터" — CopyableTable
    │   └─ Die Filter Panel
    │       ├─ toggle 버튼 (접기/펼치기)
    │       ├─ 전체 선택 / 안정화 Die 제외
    │       └─ Die 체크박스 목록 (Die 1~22)
    │
    └─ RIGHT PANEL (QTabWidget — chart_category_tabs)
        ├─ "Basic Analysis"
        │   ├─ "Contour X / Y"  (Repeat별 Contour 버튼 포함)
        │   ├─ "Die Position Map"
        │   └─ "Vector Map"  (Colorbar + Show Values 토글)
        ├─ "Interactive"
        │   ├─ "XY Scatter" + 사이드 범례 + Log Scale 토글
        │   ├─ "Lot Trend"  + Lot 필터 체크박스
        │   ├─ "Distribution X / Y" (서브탭)
        │   └─ "TIFF"
        ├─ "Advanced"
        │   ├─ "Pareto"
        │   ├─ "Correlation"
        │   ├─ "3D Surface X / Y" (서브탭)
        │   ├─ "Positioning Log"  (비전 보정 로그 대조)
        │   └─ "Diagnosis (트러블슈팅)" (요약 칩, 스캐너 오프셋·what-if 표, drift 차트, 부품 판정, 다음 조치, 진단 JSON 저장)
        ├─ "Comparison"
        │   ├─ "Boxplot"
        │   ├─ "Trend"
        │   └─ "Heatmap"
        └─ "Export"
            └─ "Export"
                ├─ Analysis Guide 버튼 → GuideDialog
                ├─ Excel Export
                ├─ CSV Export
                └─ PDF Report
```

---

## 5. 주요 클래스 및 컴포넌트

| 클래스/컴포넌트 | 위치 | 역할 |
|----------------|------|------|
| `DataAnalyzerApp` | `main.py` | 앱 메인 창 (QMainWindow + 10개 Mixin 상속) |
| `GuideDialog` | `ui/dialogs/guide_dialog.py` | 분석 가이드 도움말 팝업 (QDialog) |
| `SpecConfigDialog` | `ui/dialogs/spec_config_dialog.py` | Spec 설정 확인 팝업 |
| `RepeatContourDialog` | `ui/dialogs/repeat_contour_dialog.py` | Repeat별 Contour 비교 팝업 |
| `StatCard` | `ui/widgets/stat_card.py` | X/Y 통계 카드 위젯 (QFrame) |
| `CopyableTable` | `ui/widgets/copyable_table.py` | Ctrl+C 복사 지원 테이블 |
| `ScanWorker` | `ui/workers/data_loader_thread.py` | 비동기 스캔 스레드 (QThread) |
| `ChartWidget` | `ui/widgets/chart_widget.py` | Matplotlib/pyqtgraph 임베드 컨테이너 |
| Controllers | `ui/controllers/` | 10개 Mixin (UiBuilder, Chart, Step, Scan, Table, Card, DieFiler, LotFilter, XyLegend, Tiff, Export) |

---

## 6. 데이터 필드 규칙

### 6.1 `raw_data` 레코드 구조

```python
{
    "lot_name":   str,   # Lot 폴더 이름 (예: "Lot001")
    "site_id":    str,   # Die 위치 ID (예: "Die_1")
    "method":     str,   # "X" 또는 "Y" — 축 구분 (필드명 주의!)
    "value":      float, # HZ1_O 측정값 (nm)
    "is_outlier": bool,  # X, Y 축별 IQR 기반 이상치 여부
    "valid":      bool,  # 유효 데이터 여부
}
```

> ⚠️ **주의**: `axis` 키는 사용하지 않음. 반드시 `method` 키로 X/Y 필터링.
> ```python
> # 올바른 필터링
> filter_by_method(data, 'X')   # method.upper() == 'X'
> ```

### 6.2 `recipe` 구조

```python
{
    "name":       str,   # 원본 폴더 이름 (예: "1. Vision Pattern Recognize")
    "short_name": str,   # 정규화된 이름 (예: "Vision Pattern")
    "path":       str,   # 절대 경로
    "round_paths": dict, # {"1st": path1, "2nd": path2, ...}
}
```

---

## 7. Spec 검증 로직

```python
# 폴더명 검증 (scan 시점) — 대소문자·앞 번호("4. ") 무시, 일치하면 short_name = Spec 키
mismatched = normalize_spec_names(recipes, list(spec_deviation))
if mismatched:
    # Workflow별 폴더 지정 대화상자. 체크한 Workflow만 분석, 지정 내역은 workflow_folder_map에 저장
    dlg = WorkflowFolderDialog(...)
    recipes, problems = recipes_from_mapping(dlg.selected_mapping(), list(spec_deviation))

# PASS/FAIL 판정 (분석 시점)
pass_x = (dev_x.overall_range <= spec_range) and (dev_x.overall_stddev <= spec_stddev)
pass_y = (dev_y.overall_range <= spec_range) and (dev_y.overall_stddev <= spec_stddev)
overall = pass_x and pass_y
```

---

### 7.1 비전 보정 로그 대조 (`core/positioning_log.py`)

Lot 폴더 `Debug/SamplePositioning/<Sample>_SamplePositioning.txt`(Die마다 한 행: Stage X/Y, Coarse/Fine
Match·Move·Optical Shift, Tip X/Y, Tip Pos X/Y)와 `Debug/TipPositioning/<Sample>_TipPositioning.txt`
(Tip Positioning마다 한 행, Tip X/Y 칸이 비면 Fine Match X/Y)를 탭 구분으로 읽는다 (열 이름의 단위·대소문자
무시, 같은 이름이 되는 열은 µm 열 우선, 남는 열·빈 줄 허용, UTF-16 BOM 다음 CSV와 같은 순서의 인코딩).
로그 파일이 여럿이면 CSV 이름 앞부분과 같은 것을 쓰고, 맞는 것이 없으면 불일치. 로그 Slot과 CSV 메타 Slot No가
둘 다 있으면 같아야 한다. 폴더·파일이 없으면 '로그 없음', 그 밖의 읽기 오류(네트워크·잠김)는 한 번 더 읽고
'읽지 못함'으로 따로 알리며 담아 두지 않는다. 행은 측정 순서로 Die에 짝짓되 Point 0 행(샘플 정렬 기준점)은
빼고, 명령 좌표에 회전·평행이동을 맞춘 뒤 X/Y UL 좌표와 300 µm 안에서 가장 가까운 Die가 같은지 확인한다
(맞춘 회전이 1°를 넘으면 대칭 배치로 잘못 맞은 것으로 보고 불일치). 단계(도착 오차, 거친·정밀 보정, 정밀 잔차,
Optical Shift, Tip 위치, 최종 착지 = HZ1_O 편차)마다 Die 좌표 평면 피팅(Sx, θx, θy, Sy와 t; 좌표 퍼짐 5 mm
미만·공선이면 그 계수는 —), Range, HZ1_O와의 상관(값 그대로 r과 계수별 부분 잔차 r)을 Lot별·Die별 Lot 평균으로
계산한다. 최종 착지의 유의한 성분(|t| ≥ 3, Die 8개 이상)을, 같은 계수가 |t| ≥ 3, 크기 ≥ 0.5배, 부분 잔차
|r| ≥ 0.5이고 부호(단계)·부호(최종) = 부호(r)인 단계(Die 8개 이상) 중 가장 앞 단계에 돌린다(같은·반대 부호를
적는다). 어느 단계에도 없으면 '비전 정렬 이후(카메라→Tip 착지)'. 데이터 로드는 로그를 읽지 않는다.
Advanced → Positioning Log 탭이 보일 때 그 회차를 읽어 회차 결과의 `positioning_log`에 담고, AI 분석용 리포트의
'비전 보정 로그 대조' 절은 담긴 분석을 쓰거나 내보내기 스레드에서 읽는다.

### 7.2 트러블슈팅 진단 (`core/xy_diagnosis.py`)

선택 회차 하나의 측정값과 Lot Log로 원인 후보를 규칙으로 판정해 `pass.xy.diagnosis/1` dict를 만든다
(`XYDiagnosis.to_dict()` / `to_json()`, 공유 설계 `.omc/plans/xy-diagnosis-integration.md`).
- **읽기** (`read_run_logs`, 작업 스레드): Lot마다 `Log/<Sample>_Log.csv`(폴더 목록 1번·파일 1개)와 측정 CSV 앞 20줄
  (Start/End Time, Recipe ID, Sample ID). Log의 ImageJob `Set scan params`(Scan Direction, X/Y Offset = 측정 라인에
  건 스캐너 오프셋), 그 전 마지막 `Sample pattern align - fine`(X/Y Offset - Fine, µm), `Apply reference scan result`
  (X/Y Offset - Reference·Total, nm). 레시피 = 적용 − 기준 보정 − 미세 보정. 기준 스캔이 Lot에 없으면 시각 순서로 앞
  Lot의 값을 이어 쓰고, 그래도 없으면 레시피·기준 보정은 null(`recipe_plus_ref`만). 폴더·파일 없음은 `no_log`, 그 밖의
  읽기 오류는 한 번 더 읽고 `error`(담지 않음). 읽은 것은 회차 결과의 `xy_logs`에 담는다 (`store_logs`).
- **계산** (`build_diagnosis`, 파일을 읽지 않음, 10 Lot에 ~30 ms): 축별 Die 평균 평면(`positioning_log.plane_fit`,
  θx = ∂dx/∂y, θy = ∂dy/∂x), κ_eff = |θ| / |구동 축 스캐너 오프셋|, what-if(H − κ·Δ·y, 선형), drift(Lot 평균 대 시작
  시각, Lot 안 측정 순서 기울기 — 유의한 평면 모양만 뺌), transient(Die4 이후 평면 대비 Die1·2·3), Die 평균 편차 장,
  Positioning Log 분석이 있으면 그 판정, 부품 판정(`PART_RULES`)과 다음 조치. 스캐너 부품의 '이상'은 θ 유의(Die 8개 이상) +
  구동 ≥ 5 µm + Positioning Log가 비전 정렬 이후로 볼 때만이다(분석 전이면 많아야 '주의'). what-if 후보는 지금 레시피와
  같은 쪽, 권장은 '지금 행이 모든 Lot PASS면 현재 유지, 아니면 꼭짓점 여유 5 µm 이상 PASS 후보 중 |값| 최소'. 회전 잠정 규격은
  `settings.rotation_spec` (코드 기본값 `DEFAULT_ROTATION_SPEC`을 번들 값이 항목마다 덮고, 숫자로 바꾸며 warn ≤ max).
- **화면** (`ui/controllers/diagnosis_controller.py`): Diagnosis 탭이 보일 때만 그 회차의 Log를 읽고(세대 번호로 늦은 결과를
  버림, 시그널로 화면 스레드에 넘김, 창을 닫으면 ScanSession 중지), 같은 때 positioning_controller의 Positioning Log 읽기도
  시작한다(끝나면 `_refresh_diagnosis`). 진단은 탭이 보일 때 화면 스레드에서 계산한다. 지금 보이는 진단 dict는 창의
  `_xy_diagnosis`에 둔다 (3D 보고서·스캐너 회전 시험 분석이 붙는 자리, 없으면 None). 회전 시험 대화상자는
  `set_rotation_test(결과)`를 부른다: 사본을 `_rotation_test_result`(장비의 것, 회차를 바꿔도 유지)에 두고 진단을 다시
  계산해 `rotation_test` 칸·요약 칩에 넣는다. 폐루프·Y 축 시험의 warn·fail은 `xy_scanner_y`의 직접 증거다. 결과는 받을 때의
  장비(`source.tool_id`: Log의 Tool ID, 없으면 연 폴더)에 묶여 다른 장비의 진단·JSON·LLM에는 붙지 않는다.
- **내보내기**: AI 분석용 리포트 '트러블슈팅 진단 (Diagnosis)' 절은 담긴 Log를 쓰거나 내보내기 스레드에서 읽는다
  (Workflow별 예외 격리). 회차 비교 표·Excel Run Comparison의 θx·θy는 `axis_shape`로 CSV 값만 쓴다.

## 8. 성능 및 비동기 처리

| 작업 | 처리 방식 | 이유 |
|------|-----------|------|
| 폴더 확인 (Scan·시작 시 자동 스캔) | `QThread (FolderScanThread)` | 네트워크 폴더에서 '응답 없음' 방지. 진행 문구·중지(Esc) |
| Workflow별 대화상자 폴더 상태 | 작업 스레드 1개 (`ThreadPoolExecutor`) | 대화상자는 바로 열리고 행마다 '확인 중…' |
| 데이터 로드 | `QThread (DataLoaderThread)` + Lot 스레드 8개 | 네트워크 지연을 겹친다. 결과 순서는 Lot 순서 |
| 폴더 확인 결과 재사용 | `ScanSession` (사용자 동작 1회) | 스캔→대화상자→로드가 같은 폴더를 다시 읽지 않는다 |
| TIFF 찾기·읽기 | `QThread (TiffLoadThread)` | 더블클릭 때 폴더 목록·TIFF 읽기 |
| 진행 표시 | `BusyOverlay` + 상태 표시줄 `QProgressBar` | 폴더 확인 n/m, Lot n/m(전체는 폴더 확인 결과로 미리 앎), 초당 10번까지 |
| 회차 요약 (`summarize_runs`) | `DataLoaderThread` | 화면 갱신 시간을 줄인다. Spec이 바뀌었으면 화면 스레드가 다시 계산 |
| 비전 보정 로그 대조 (`positioning_log`) | Positioning Log 탭이 보일 때 파이썬 스레드 1개(+ Lot 스레드 8개), 내보내기는 그 스레드에서 순서대로 | Lot마다 Debug 폴더 목록 2~3번·작은 파일 2~3개라 데이터 로드에서는 읽지 않는다 |
| 트러블슈팅 진단 Log (`xy_diagnosis`) | Diagnosis 탭이 보일 때 파이썬 스레드 1개(+ Lot 스레드 8개), 진단 계산은 탭이 보일 때 화면 스레드, 내보내기는 그 스레드에서 순서대로 | Lot마다 Log 폴더 목록 1번·Log 1개·CSV 앞부분이라 데이터 로드에서는 읽지 않는다 |
| Comparison 탭 차트 | 처음 보일 때 그림 | 가려진 탭은 미뤘다가 보일 때 한 번 (같은 결과면 다시 안 그림) |
| PDF 생성 | `threading.Thread` | Matplotlib PDF 생성 시간 소요 |
| 차트 렌더링 | 메인 스레드 | Qt 위젯은 메인 스레드에서만 접근 가능 |
| 결과 UI 업데이트 | `QTimer.singleShot(0, ...)` | 스레드 → 메인 스레드 안전한 UI 업데이트 |

---

## 9. 개발 히스토리 요약

| 버전 | 주요 변경 사항 |
|------|---------------|
| v1~5 | 데이터 로딩, 분석 엔진, 시각화, tkinter GUI 초기 개발 |
| v6 | Multi-Recipe Dashboard |
| v7 | Guided Workflow UI + Cpk/Spec 판정 |
| v8 | 레이아웃 개편, System Log, Vector Map, Affine Transform |
| v8b~d | 히트맵, 드래그 복사, Contour 팝업, 텍스트 대비 개선 |
| **v9** | **PySide6 마이그레이션 완료** |
| v9.1 | pyqtgraph 인터랙티브 차트 (Lot Trend, 분포, 3D) |
| v9.2 | X/Y 분리 아키텍처 (독립 축별 드리프트 감지) |
| v9.3 | Spec 대비 카드 + Spec 다이얼로그 + 하드코딩 제거 |
| v9.4 | Die 필터 토글 + XY Scatter 범례 패널 + Log Scale |
| v9.5 | 폴더명 엄격 검증 + 테이블 가운데 정렬 + 분석 가이드 Dialog |
| **v10.0** | **Mixin 기반 모듈 아키텍처** — core/charts/ui 3계층 분리, 9개 버그 수정 |
| **v10.1** | Vector Map Colorbar + "Show Values" 토글 추가 |
