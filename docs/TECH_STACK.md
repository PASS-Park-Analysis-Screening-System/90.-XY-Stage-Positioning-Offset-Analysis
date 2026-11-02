# 🛠️ 기술 스택 정의 문서

> XY Stage Positioning Offset Analysis Tool — v9.x  
> 최종 업데이트: 2026-03-06

---

## 1. 언어 및 런타임 환경

| 항목 | 사양 | 비고 |
|------|------|------|
| **언어** | Python 3.11 | 최소 권장 버전. `match`, `typing` 고급 기능 사용 |
| **플랫폼** | Windows 10/11 (64-bit) | 사내 DLP 보안 환경 고려 |
| **실행 방식** | `python main.py` 단독 실행 | 인스톨러 없이 소스 직접 실행 |

---

## 2. GUI 프레임워크

| 항목 | 기술 | 버전 | 라이선스 | 비고 |
|------|------|------|----------|------|
| **메인 GUI** | PySide6 (Qt6) | 6.x | LGPL v3 | `QMainWindow`, `QDialog`, `QSplitter`, `QTabWidget` |
| **테마 시스템** | Qt StyleSheet (QSS) | — | — | Catppuccin Mocha 다크 테마 하드코딩 |
| **인터랙티브 차트** | pyqtgraph | 0.13+ | MIT | `PlotWidget`, `GraphicsLayoutWidget`, `OpenGLViewWidget` |
| **커스텀 렌더링** | `QPainter` | Qt6 내장 | — | `SparklineTrendDelegate` — 테이블 셀 내 미니 차트 |

### 2.1 Qt 위젯 사용 현황

```
QMainWindow          — 앱 루트
  QSplitter(H)       — 좌: 제어 패널 / 우: 차트 패널
  ├ [LEFT]
  │  QWidget
  │   QPushButton    — Open, Scan & Analysis
  │   QTabWidget     — Recipe 단계 탐색 (Workflow 탭)
  │   QFrame         — StatCard (X/Y 통계 카드)
  │   QTabWidget     — 시스템 로그 | 데이터 테이블
  │     QTableWidget — Summary / Die 평균 / Raw Deviation / 원본 데이터
  │   QListWidget    — Die 필터 체크박스
  └ [RIGHT]
     QTabWidget      — 기본 분석 | 인터랙티브 | 고급 분석 | 비교 | Export
       ChartWidget(QWidget + FigureCanvas)  — Matplotlib 차트
       InteractiveChartWidget               — pyqtgraph 차트
       GuideDialog (QDialog)                — 분석 가이드 도움말 팝업
```

---

## 3. 데이터 처리 & 분석

| 라이브러리 | 용도 | 주요 API |
|------------|------|----------|
| **numpy** | 배열 연산, 행렬 계산 | `np.linalg.lstsq`, `np.std`, `np.percentile` |
| **pandas** | CSV 파싱, 테이블 처리 | `pd.read_csv`, DataFrame 필터링 |
| **scipy** | 보간 (Contour Map) | `scipy.interpolate.griddata(method='cubic')` |

### 3.1 핵심 알고리즘

| 알고리즘 | 모듈 | 함수 | 설명 |
|----------|------|------|------|
| 기술 통계 | `core/statistics.py 및 core/die_analysis.py` | `compute_statistics()` | N, Mean, StdDev, Min, Max, CV% |
| 공정 능력 | `core/statistics.py 및 core/die_analysis.py` | `compute_cpk()` | Cpk = min[(USL-μ)/3σ, (μ-LSL)/3σ] |
| 편차 행렬 | `core/statistics.py 및 core/die_analysis.py` | `compute_deviation_matrix()` | Die×Repeat 행렬, overall_range/stddev |
| Affine 변환 | `core/statistics.py 및 core/die_analysis.py` | `compute_affine_transform()` | 최소자승법: Tx/Ty/Sx/Sy/θ 추출 |
| 이상치 탐지 | `core/statistics.py 및 core/die_analysis.py` | `detect_outliers_by_axis()` | X, Y 축별 IQR (Q1-1.5×IQR ~ Q3+1.5×IQR) |
| 트렌드 계산 | `core/recipe_scanner.py` | `compute_trend()` | Lot별 Mean/StdDev 시계열 |
| Contour 보간 | `charts/ 모듈 (basic.py, wafer.py 등)` | `plot_contour_map()` | scipy griddata, cubic interpolation |

---

## 4. 시각화

| 엔진 | 용도 | 장단점 |
|------|------|--------|
| **Matplotlib** (`charts/ 모듈 (basic.py, wafer.py 등)`) | Wafer Contour Map, Vector Map, XY Scatter, Boxplot 등 정적 차트 | 고품질 렌더링, 인터랙션 제한 |
| **pyqtgraph** (`charts/interactive.py 및 charts/interactive_widgets.py`) | Lot Trend, 히스토그램+KDE, 3D Surface, TIFF Viewer | 실시간 줌/팬, OpenGL 가속 |

### 4.1 Matplotlib 백엔드

```python
matplotlib.use("Agg")  # GUI 비표시 렌더링 (PySide6와 충돌 방지)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
```

### 4.2 pyqtgraph OpenGL

```python
import pyqtgraph.opengl as gl  # 3D Surface 렌더링
```

---

## 5. 파일 I/O

| 형식 | 모듈 | 라이브러리 | 용도 |
|------|------|------------|------|
| **CSV/TXT** | `core/csv_loader.py` | `pandas.read_csv` | 원시 측정 데이터 로드 |
| **JSON** | `core/settings.py` | `json` 표준 라이브러리 | Spec 설정, 창 위치 저장 |
| **Excel (.xlsx)** | `core/exporter.py` | `openpyxl` | 분석 결과 리포트 |
| **PDF** | `core/pdf_generator.py` | matplotlib PDF 백엔드 | 차트+통계 복합 리포트 |
| **TIFF** | `core/tiff_loader.py` | `struct` 표준 라이브러리 | PSPylib 전용 바이너리 파서 |

### 5.1 네트워크 드라이브 (Z:) 접근

> ⚠️ 사내 DLP 보안 정책으로 인해 Python 표준 파일 API(`open()`, `os.listdir()`)가 네트워크 드라이브에서 차단될 수 있음.

현재 우회 방법: `os.popen("xcopy /Y /Z ...")` 또는 `subprocess.run(["xcopy", ...])` 사용으로 로컬 임시 디렉토리에 복사 후 접근.

---

## 6. 설정 관리 (settings.json)

```json
{
  "standard_recipe_names": ["Vision Pattern", "In-Die Align", "LLC Translation", "Global Align"],
  "spec_deviation": {
    "Vision Pattern":  { "spec_range": 1.0, "spec_stddev": 0.2 },
    "In-Die Align":    { "spec_range": 2.0, "spec_stddev": 0.4 },
    "LLC Translation": { "spec_range": 4.0, "spec_stddev": 0.8 },
    "Global Align":    { "spec_range": 6.0, "spec_stddev": 1.2 }
  },
  "spec_limits": {
    "Vision Pattern": {
      "X": { "lsl": -5000, "usl": 5000 },
      "Y": { "lsl": -5000, "usl": 5000 }
    }
  },
  "wafer_radius_um": 150000
}
```

---

## 7. 디자인 시스템 — Catppuccin Mocha

### 7.1 색상 팔레트

| 변수 | HEX | 용도 |
|------|-----|------|
| `BG` | `#1e1e2e` | 메인 배경 |
| `BG2` | `#282a3a` | 카드/패널 배경 |
| `BG3` | `#313244` | 구분선, 비활성 탭 |
| `FG` | `#cdd6f4` | 기본 텍스트 |
| `FG2` | `#a6adc8` | 보조 텍스트 |
| `ACCENT` | `#89b4fa` | 강조, 활성 탭, 링크 |
| `GREEN` | `#a6e3a1` | PASS, 성공 |
| `RED` | `#f38ba8` | FAIL, 오류, 경고 |
| `ORANGE` | `#fab387` | 주의, 보조 강조 |
| `PURPLE` | `#cba6f7` | 특수 강조 |

### 7.2 차트 색상 규칙

| 요소 | 색상 |
|------|------|
| X축 데이터 | `#89b4fa` (ACCENT-Blue) |
| Y축 데이터 | `#f38ba8` (RED-Pink) |
| Spec 한계선 | `#cba6f7` (PURPLE), dashed |
| Overall Mean선 | `#a6adc8` (FG2), dashed |
| 배경 | `#1e1e2e` (BG) |
| 그리드 | `#313244` (BG3), alpha=0.3 |

---

## 8. 외부 의존성 목록 (pip install)

```bash
pip install PySide6 numpy pandas scipy matplotlib openpyxl pyqtgraph PyOpenGL
```

| 패키지 | 최소 권장 버전 | 특이사항 |
|--------|--------------|----------|
| PySide6 | 6.4+ | Qt 6 LGPL, PyQt6와 호환 불가 API 일부 존재 |
| numpy | 1.23+ | — |
| pandas | 1.5+ | — |
| scipy | 1.9+ | `griddata` cubic 보간 |
| matplotlib | 3.6+ | `QtAgg` 백엔드 (`backend_qtagg`) |
| openpyxl | 3.0+ | Excel .xlsx 전용 |
| pyqtgraph | 0.13+ | OpenGL 가속을 위해 PyOpenGL 필요 |
| PyOpenGL | 3.1+ | pyqtgraph 3D 뷰어 의존성 |
