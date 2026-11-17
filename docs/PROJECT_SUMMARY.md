# 📊 XY Stage Positioning Offset Analysis Tool

> **프로젝트**: XY Stage Offset 분석 도구 (PySide6)  
> **버전**: v10.0 (Mixin 기반 모듈 아키텍처)  
> **최종 업데이트**: 2026-03-09  
> **플랫폼**: Windows / Python 3.11+  

---

## 1. 프로젝트 개요

반도체/MEMS 장비의 XY Stage가 웨이퍼 상의 각 Die 위치로 이동할 때 발생하는 **위치 오프셋(Offset)**을 체계적으로 분석하는 데스크톱 도구입니다.

### 핵심 가치
- **다단계 Recipe 비교**: Vision Pattern, In-Die Align, LLC Translation, Global Align 등 여러 보정 단계를 한 번에 로드하여 비교
- **계통 오차 분리**: Affine Transform을 통해 Stage H/W의 Translation, Scaling, Rotation 오차를 수학적으로 추출
- **X/Y 독립 분석**: X축과 Y축 오프셋을 분리하여 축별 드리프트 감지
- **Die 단위 시각화**: Wafer Contour Map, Vector Map, XY Scatter 등으로 공간적 패턴을 즉시 파악
- **Spec 기반 PASS/FAIL 판정**: `settings.json`의 `spec_deviation` 기준으로 자동 판정

---

## 2. 기술 스택

### 2.1 핵심 스택

| 항목 | 기술 | 비고 |
|---|---|---|
| **언어** | Python 3.11 | |
| **GUI 프레임워크** | PySide6 (Qt 6) | LGPL v3 |
| **테마** | Catppuccin Mocha (다크) | QSS 기반 |
| **차트 (정적)** | Matplotlib 3.x (`QtAgg` 백엔드) | Contour, Vector, Boxplot 등 |
| **차트 (인터랙티브)** | pyqtgraph (OpenGL 가속) | Lot Trend, Distribution, 3D Surface |
| **데이터 처리** | pandas, numpy | |
| **보간** | scipy.interpolate (`griddata`, cubic) | Contour Map |
| **선형대수** | numpy.linalg (`lstsq`) | Affine Transform |
| **TIFF 파싱** | struct 모듈 | PSPylib 바이너리 포맷 |
| **설정 관리** | JSON (`settings.json`) | |
| **리포트** | openpyxl (Excel), Matplotlib PDF | |

### 2.2 외부 의존성 (pip)
```
PySide6
numpy
pandas
scipy
matplotlib
openpyxl
pyqtgraph
PyOpenGL
```

---

## 3. 디자인 시스템 — Catppuccin Mocha 테마

### 3.1 색상 팔레트

모든 프로젝트에서 통일적으로 사용하는 **Catppuccin Mocha** 테마입니다.

```python
# Color Constants (Catppuccin Mocha)
BG      = '#1e1e2e'   # Base      — 메인 배경
BG2     = '#282a3a'   # Mantle    — 카드/패널 배경  (실제 #282a36 변형)
BG3     = '#313244'   # Surface0  — 구분선, 비활성 탭
FG      = '#cdd6f4'   # Text      — 기본 텍스트
FG2     = '#a6adc8'   # Subtext0  — 보조 텍스트
ACCENT  = '#89b4fa'   # Blue      — 강조, 활성 탭, 링크
GREEN   = '#a6e3a1'   # Green     — PASS, 성공
RED     = '#f38ba8'   # Red       — FAIL, 오류, 경고
ORANGE  = '#fab387'   # Peach     — 주의, 보조 강조
PURPLE  = '#cba6f7'   # Mauve     — 특수 강조
```

### 3.2 QSS 스타일 패턴

```css
/* 탭 스타일 */
QTabBar::tab { background: BG3; color: FG2; padding: 8px 16px; border-radius: 4px 4px 0 0; }
QTabBar::tab:selected { background: BG; color: ACCENT; font-weight: bold; }

/* 버튼 스타일 */
QPushButton { background: BG3; color: FG; padding: 6px 12px; border-radius: 4px; }
QPushButton:hover { background: #45475a; }

/* 테이블 스타일 */
QHeaderView::section { background: BG3; color: FG2; padding: 4px; border: none; }
QTableWidget { gridline-color: BG3; selection-background-color: #45475a; }

/* 카드 스타일 (QFrame) */
#statcard { background: BG2; border: 1px solid #585b70; border-radius: 6px; padding: 8px; }
#statcard QLabel { background: transparent; }
```

### 3.3 차트 스타일 가이드

| 요소 | 색상 | 용도 |
|---|---|---|
| X축 데이터 라인 | `#89b4fa` (ACCENT) | Lot Trend X, 분포 X |
| Y축 데이터 라인 | `#f38ba8` (RED) | Lot Trend Y, 분포 Y |
| Spec 한계선 | `#cba6f7` (PURPLE), dashed | ±half_range |
| Overall Mean 기준선 | `#a6adc8` (FG2), dashed | 전체 평균 |
| 1σ 영역 | 반투명 fill | Mean ± 1σ 범위 |
| 차트 배경 | `#1e1e2e` (BG) | |
| 그리드 | `#313244` (BG3), 0.3 alpha | |

---

## 4. 프로젝트 구조

```
97. XY Stage Positioning Offset Analysis/
├── src/
│   ├── main.py                          ← 앱 진입점, Mixin 조립 (~200행)
│   ├── core/                            ← 분석 엔진 (UI 무관)
│   │   ├── statistics.py                ← 통계 계산 (Cpk, IQR, 상관관계)
│   │   ├── die_analysis.py              ← Die 편차 행렬, Affine Transform
│   │   ├── csv_loader.py                ← CSV 파싱, DLP xcopy 폴백
│   │   ├── recipe_scanner.py            ← Recipe 자동 탐지 + Trend 계산
│   │   ├── tiff_loader.py               ← PSPylib TIFF 바이너리 파서
│   │   ├── exporter.py                  ← Excel/CSV 내보내기
│   │   ├── pdf_generator.py             ← PDF 보고서 생성
│   │   └── settings.py                  ← 설정 load/save
│   ├── charts/                          ← 차트 생성기
│   │   ├── wafer.py                     ← Contour, Vector Map, Die Position
│   │   ├── basic.py                     ← Boxplot, Trend (Matplotlib)
│   │   ├── comparison.py                ← Recipe 비교 차트
│   │   ├── interactive.py               ← pyqtgraph 2D 차트
│   │   ├── interactive_widgets.py       ← CrossHair, HoverScatter, TiffViewer
│   │   └── surface3d.py                 ← 3D Surface (OpenGL)
│   └── ui/
│       ├── theme.py                     ← Catppuccin Mocha 색상 상수
│       ├── color_helpers.py             ← 히트맵 색상 유틸
│       ├── controllers/                 ← Mixin 기반 UI 제어 (10종)
│       │   ├── ui_builder_mixin.py      ← 전체 레이아웃 구성
│       │   ├── chart_controller.py      ← 차트 갱신
│       │   ├── step_controller.py       ← 분석 결과 표시
│       │   ├── scan_controller.py       ← 폴더 스캔·로드
│       │   ├── table_controller.py      ← 데이터 테이블 갱신
│       │   ├── card_controller.py       ← StatCard 갱신
│       │   ├── die_filter_controller.py ← Die 필터
│       │   ├── lot_filter_controller.py ← Lot 필터
│       │   ├── xy_legend_controller.py  ← XY 범례
│       │   ├── tiff_controller.py       ← TIFF 뷰어
│       │   └── export_controller.py     ← 내보내기
│       ├── widgets/                     ← 재사용 UI 부품
│       │   ├── chart_widget.py          ← Matplotlib/pyqtgraph 컨테이너
│       │   ├── stat_card.py             ← KPI 통계 카드
│       │   ├── copyable_table.py        ← Ctrl+C 복사 테이블
│       │   ├── flow_layout.py           ← 자동 줄바꿈 레이아웃
│       │   └── system_logger.py         ← 로그 패널
│       ├── dialogs/
│       │   ├── guide_dialog.py          ← Analysis Guide 팝업
│       │   ├── spec_config_dialog.py    ← Spec 설정 팝업
│       │   └── repeat_contour_dialog.py ← Repeat 비교 팝업
│       └── workers/
│           └── data_loader_thread.py    ← 백그라운드 파일 로더
├── data/                                ← 측정 데이터 (Recipe별 폴더)
└── docs/                                ← 문서
    ├── PROJECT_SUMMARY.md               ← 이 문서
    ├── ARCHITECTURE.md                  ← 모듈 의존 관계, UI 레이아웃 트리
    ├── TECH_STACK.md                    ← 기술 스택 정의
    ├── DATA_ANALYSIS_GUIDE.md           ← 엔지니어용 분석 가이드
    ├── SUMMARY_TABLE_GUIDE.md           ← Summary 테이블 컬럼 가이드
    └── final_migration_report.md        ← v10.0 마이그레이션 완료 보고서
```

---

## 5. 모듈 상세

### 5.1 `core/` — 분석 엔진

| 모듈 | 주요 함수 / 역할 |
|---|---|
| `csv_loader.py` | CSV/TXT 배치 로드, DLP xcopy 폴백, 인코딩 자동 감지 |
| `recipe_scanner.py` | Recipe 구조 자동 탐지, `compute_trend()` — Lot별 트렌드 |
| `statistics.py` | `compute_statistics`, `compute_cpk`, `detect_outliers` |
| `die_analysis.py` | `compute_deviation_matrix`, `compute_affine_transform`, `filter_by_method` |
| `tiff_loader.py` | PSPylib 바이너리 TIFF 파서 |
| `exporter.py` | CSV/Excel 내보내기 |
| `pdf_generator.py` | PDF 보고서 생성 |
| `settings.py` | `load_settings` / `save_settings` |

### 5.2 `charts/` — 차트 생성기

| 모듈 | 차트 유형 |
|---|---|
| `wafer.py` | Contour Map, Vector Map (Colorbar + Show Values 토글), Die Position Map |
| `basic.py` | Boxplot, Trend (Matplotlib) |
| `comparison.py` | Recipe 비교 차트 |
| `interactive.py` | Lot Trend, XY Scatter, Distribution, Pareto, Correlation (pyqtgraph) |
| `interactive_widgets.py` | CrossHair, HoverScatter, TiffViewer 위젯 |
| `surface3d.py` | 3D Surface (OpenGL) |

### 5.3 `ui/` — 프레젠테이션 계층

- **`ui/theme.py`**: Catppuccin Mocha 색상 상수 (`BG`, `ACCENT`, `GREEN`, …)
- **`ui/widgets/`**: `StatCard`, `CopyableTable`, `ChartWidget`, `FlowLayout`, `SystemLogger`
- **`ui/dialogs/`**: `GuideDialog`, `SpecConfigDialog`, `RepeatContourDialog`
- **`ui/workers/`**: `ScanWorker` (QThread 기반 비동기 로드)
- **`ui/controllers/`**: 10개 Mixin — `UiBuilderMixin`, `ChartController`, `StepController`, `ScanController`, `TableController`, `CardController`, `DieFilterController`, `LotFilterController`, `XyLegendController`, `TiffController`, `ExportController`

### 5.4 `main.py` — Mixin 조립 진입점

**레이아웃 구조 (QSplitter 5:5):**
```
┌───────────────────────────────────────────────────────────────┐
│ [📁 폴더] [Open] [🔄 Scan & Analysis]        [Wafer: 300mm]  │
├───────────────────────────────────────────────────────────────┤
│ Workflow: [Step 1 ▶ Step 2 ▶ Step 3 ▶ Step 4]                │
├────────────────────────┬──────────────────────────────────────┤
│ Step: 1. Vision Pat.   │ 기본 분석 | 인터랙티브 | 고급 분석 │
│                        │ 비교 | Export                       │
│ ┌─X Offset────────────┐│                                      │
│ │ Avg: 2736.439       ││       (차트 영역)                    │
│ │ Range: 1.313 /1.0 ▲ ││  Contour X/Y | XY Scatter |         │
│ │ StdDev: 0.253/0.2 ▲ ││  Vector Map | Die Position |        │
│ │ Cpk: 2.98           ││  Lot Trend | 분포 | 3D Surface |     │
│ │ ❌ FAIL              ││  TIFF                                │
│ └─────────────────────┘│                                      │
│ ┌─Y Offset────────────┐│                                      │
│ │ (동일 구조)          ││                                      │
│ └─────────────────────┘│                                      │
│ ⚙️ Spec 설정           │                                      │
│────────────────────────│                                      │
│ 📝 시스템 로그          │                                      │
│ 🗄️ 데이터 테이블        │                                      │
│  ├ Summary             │                                      │
│  ├ Die별 평균 (X/Y)     │                                      │
│  ├ Raw Deviation       │                                      │
│  └ 원본 데이터         │                                      │
│────────────────────────│                                      │
│ Die 필터 (토글+체크박스) │                                      │
└────────────────────────┴──────────────────────────────────────┘
```

**주요 UI 패턴 (v9.5 기준):**
- **StatCard**: X/Y 카드에 Spec 대비 표기 (`value / spec ▲+N%` or `✓N%`), 테두리(border) 포함
- **히트맵 테이블**: Die Average — 양방향 Red-Blue / StdDev·Range — Steel Blue
- **테이블 전체 가운데 정렬**: Summary, Die별 평균, Raw Deviation 모든 데이터 셀 `AlignCenter`
- **Luminance 기반 텍스트 반전**: 배경 밝기에 따라 흰/검 자동 계산
- **드래그 선택 & Ctrl+C 복사**: `CopyableTable` 위젯
- **Sub-Tab 패턴**: '분포', '3D Surface' — X/Y 서브탭 내장
- **Spec 설정 다이얼로그**: Deviation Spec + Offset Limits 표 형태로 표시
- **GuideDialog**: 📖 분석 가이드 보기 팝업 — QSplitter(좌: 목차 QListWidget / 우: QTextBrowser), HTML 렌더링

---

## 6. 데이터 흐름

```
📁 Root 폴더 선택
    ↓
ScanWorker (QThread): 비동기 실행
    ↓
core.recipe_scanner.scan_recipes() → recipes[]
    ↓
폴더명 검증: short_name ↔ spec_deviation 키 엄격 비교
    → 불일치 시 QMessageBox.critical → 스캔 차단
    ↓
core.csv_loader.batch_load_lots() → raw_data[] (method='X'/'Y')
    ↓
core.statistics.compute_statistics(X/Y) → Mean, StdDev, Cpk
core.die_analysis.compute_deviation_matrix() → overall_range, overall_stddev
    ↓
PASS/FAIL 판정: spec_deviation 기준 (core/settings.json)
    ↓
charts.wafer: Contour / Vector Map (Colorbar + Show Values)
charts.interactive: Lot Trend / Distribution / 3D (pyqtgraph)
    ↓
ui.controllers.*: 좌측 Stats+Tables | 우측 Charts 갱신
```

---

## 7. Spec 설정 체계

### 7.1 `spec_deviation` — PASS/FAIL 판정 + Trend Spec 라인

Die간 편차(Range, StdDev) 기준입니다. **키는 `recipe.short_name`과 정확히 일치**해야 합니다.

```json
"spec_deviation": {
    "Vision Pattern":  { "spec_range": 1.0, "spec_stddev": 0.2 },
    "In-Die Align":    { "spec_range": 2.0, "spec_stddev": 0.4 },
    "LLC Translation": { "spec_range": 4.0, "spec_stddev": 0.8 },
    "Global Align":    { "spec_range": 6.0, "spec_stddev": 1.2 }
}
```

**사용처:**
- **PASS/FAIL**: `overall_range ≤ spec_range AND overall_stddev ≤ spec_stddev`
- **Trend 차트**: Overall Mean ± `spec_range / 2` 로 Spec 라인 표시
- **카드 표시**: `value / spec ▲+N%` (초과=빨강) 또는 `✓N%` (여유=초록)

### 7.2 `spec_limits` — Cpk 계산용 LSL/USL

절대 오프셋 한계 (nm 단위)입니다.

```json
"spec_limits": {
    "Vision Pattern": { "X": {"lsl": -5000, "usl": 5000}, "Y": {"lsl": -5000, "usl": 5000} }
}
```

> **중요**: 키가 `short_name`과 불일치 시 경고 로그가 출력되며, 판정이 `—` (판정 불가)로 표시됩니다.

---

## 8. 데이터 필드 규칙

### `method` vs `axis`
CSV 로드 시 각 레코드에는 `method` 키가 할당됩니다. **`axis` 키는 사용하지 않습니다.**

```python
# 올바른 필터링
filter_by_method(data, 'X')   # r.get('method').upper() == 'X'

# ❌ 잘못된 사용
r.get('axis')                  # 존재하지 않음
```

---

## 9. 플러그인 아키텍처 & 메인 프로젝트 통합 전략

### 9.1 목표

이 프로젝트는 최종적으로 **메인 프로젝트(QC Check MES 등)의 플러그인**으로 통합되어야 합니다.

### 9.2 현재 구조의 플러그인 적합성

| 모듈 | 독립성 | 통합 난이도 |
|---|---|---|
| `core/*` | ✅ 완전 독립 (UI 미참조) | 낮음 |
| `charts/*` | ✅ plt 인스턴스 독립 | 낮음 |
| `ui/widgets/*` | ✅ 독립 재사용 가능 | 낮음 |
| `ui/dialogs/*` | ✅ QDialog 단독 팝업 | 낮음 |
| `core/settings.py` | ⚠️ 파일 경로 참조 | 중간 |
| `main.py` (Mixin 조립) | ⚠️ 단독 실행 전제이나 위젯 분리 용이 | 중간 |

### 9.3 플러그인 통합 로드맵

#### Phase P1: 위젯 분리
```
src/
├── main.py              → 앱 진입점 (단독 실행용)
├── xy_offset_plugin.py  → [NEW] 플러그인 진입점 (QWidget)
├── widgets/
│   ├── stat_card.py     → StatCard 위젯 추출
│   ├── nav_bar.py       → Step Navigation 바 추출
│   └── spec_dialog.py   → Spec 설정 다이얼로그 추출
```

#### Phase P2: 인터페이스 정의
```python
class XYOffsetPlugin(QWidget):
    """메인 앱에서 탭/도킹 패널로 삽입 가능한 플러그인 위젯."""
    
    # Signal: 분석 완료 시 결과 전달
    analysis_complete = Signal(dict)  # {recipe, pass_x, pass_y, stats}
    
    def load_data(self, folder: str) -> None: ...
    def get_results(self) -> list[dict]: ...
    def set_spec(self, spec_deviation: dict, spec_limits: dict) -> None: ...
```

#### Phase P3: 설정 통합
- `settings.json` → 메인 앱의 설정 관리 시스템으로 위임
- Spec 값을 메인 앱의 DB/Config에서 동적으로 주입

#### Phase P4: 데이터 연동
- CSV 로더 → 메인 앱의 데이터 파이프라인에서 데이터 전달
- 분석 결과 → 메인 앱의 대시보드/리포트에 통합

### 9.4 통합 시 유의사항

> **테마 통일**: 메인 프로젝트도 Catppuccin Mocha 테마를 사용해야 시각적 일관성 유지
> 
> **pyqtgraph 충돌**: 메인 앱이 Matplotlib만 사용 중이면 pyqtgraph 의존성 추가 필요
> 
> **QThread 관리**: 플러그인의 비동기 작업이 메인 앱의 이벤트 루프와 충돌하지 않도록 주의

---

## 10. 실행 방법

```bash
cd "${DEV_ROOT}\97. XY Stage Positioning Offset Analysis\src"
python main.py
```

### 의존성 설치
```bash
pip install PySide6 numpy pandas scipy matplotlib openpyxl pyqtgraph PyOpenGL
```

---

## 11. 개발 히스토리

| Phase | 내용 | 상태 |
|---|---|---|
| 1~5 | 데이터 로딩, 분석 엔진, 시각화, tkinter GUI | ✅ |
| 6 | Multi-Recipe Dashboard | ✅ |
| 7 | Guided Workflow UI + Cpk/Spec | ✅ |
| 8 | 레이아웃 개편 + System Log + Vector Map + Affine | ✅ |
| 8b~8d | 히트맵, 드래그 복사, Contour 팝업, 텍스트 대비 | ✅ |
| **9** | **PySide6 마이그레이션 완료** | ✅ |
| 9.1 | pyqtgraph 인터랙티브 차트 (Lot Trend, 분포, 3D) | ✅ |
| 9.2 | X/Y 분리 아키텍처 (독립 축별 드리프트 감지) | ✅ |
| 9.3 | Spec 대비 카드 표기 + Spec 다이얼로그 + 하드코딩 제거 | ✅ |
| 9.4 | Die 필터 토글 + XY Scatter 범례 패널 + Log Scale + Lot 필터 | ✅ |
| 9.5 | 엄격 폴더명 검증 + 테이블 가운데 정렬 + GuideDialog + UI 영문화 | ✅ |
| **10.0** | **Mixin 기반 모듈 아키텍처** — core/charts/ui 3계층, 10개 Controller Mixin, 레거시 파일 정리 | ✅ |
| 10.1 | Vector Map Colorbar + Show Values 토글 추가 | ✅ |

---

## 12. 관련 문서

| 문서 | 위치 | 설명 |
|------|------|------|
| [TECH_STACK.md](./TECH_STACK.md) | docs/ | 기술 스택 전체 정의 (의존성, 색상 팔레트, 알고리즘 목록) |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | docs/ | 모듈 의존 관계, 데이터 흐름, UI 레이아웃 트리 |
| [DATA_ANALYSIS_GUIDE.md](./DATA_ANALYSIS_GUIDE.md) | docs/ | 엔지니어용 분석 가이드 (지표 해석, 패턴 진단표) |
| [SUMMARY_TABLE_GUIDE.md](./SUMMARY_TABLE_GUIDE.md) | docs/ | Summary 테이블 컬럼별 상세 설명 |
| [final_migration_report.md](./final_migration_report.md) | docs/ | v10.0 마이그레이션 완료 보고서 (버그 수정 목록, 구조 현황) |

---

*이 문서는 프로젝트의 현재 상태, 기술 스택 표준, 플러그인 통합 전략을 정리한 레퍼런스입니다.*
