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
       회차 비교·회차 변경 이력(사용자 기입)·부록 Die×Repeat 편차 표·LLM 분석 요청)
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
        │   └─ "3D Surface X / Y" (서브탭)
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

## 8. 성능 및 비동기 처리

| 작업 | 처리 방식 | 이유 |
|------|-----------|------|
| 폴더 스캔 + 데이터 로드 | `QThread (ScanWorker)` | UI 블로킹 방지 — CSV 파일 수백 개 |
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
