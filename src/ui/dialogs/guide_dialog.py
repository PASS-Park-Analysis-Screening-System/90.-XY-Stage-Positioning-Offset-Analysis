"""
guide_dialog.py — Analysis Guide 도움말 대화상자
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QSplitter, QListWidget,
    QTextBrowser, QDialogButtonBox,
)
from PySide6.QtCore import Qt
from ui.theme import BG, BG2, BG3, FG, FG2, ACCENT, GREEN, RED


class GuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analysis Guide")
        self.resize(1000, 700)
        self.setStyleSheet(f"background: {BG}; color: {FG}; font-family: 'Malgun Gothic', 'Segoe UI', sans-serif;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        splitter = QSplitter(Qt.Horizontal)
        
        # Left Panel (List)
        self.nav_list = QListWidget()
        self.nav_list.setStyleSheet(f"""
            QListWidget {{ background: {BG2}; border: 1px solid {BG3}; border-radius: 6px; outline: none; }}
            QListWidget::item {{ padding: 14px 10px; border-bottom: 1px solid {BG3}; border-radius: 4px; margin: 2px; }}
            QListWidget::item:selected {{ background: {ACCENT}; color: {BG}; font-weight: bold; }}
            QListWidget::item:hover:!selected {{ background: {BG3}; }}
        """)
        self.nav_list.setCursor(Qt.PointingHandCursor)
        
        # Guide Contents
        self.contents = {
            "1. 데이터 테이블 & 지표 해석": (
                "<h2> 1. 데이터 테이블 & 기초 통계 지표 해석</h2>"
                "<p>XY Stage Positioning 분석의 출발점은 <b>통계 지표가 의미하는 장비 상태를 정확히 파악</b>하는 것입니다. 각 지표는 서로 독립적으로 보지 않고, <b>조합해서 해석</b>할 때 가장 유효합니다.</p>"
                "<table>"
                "<tr><th>지표</th><th>수식 / 정의</th><th>엔지니어 해석 포인트</th></tr>"
                "<tr><td><b>Mean (평균)</b></td><td>Σx / n</td><td>스테이지의 <b>계통 오차(Systematic Error)</b>를 나타냄. 0에서 벗어날수록 기계적 오프셋(Offset)이나 캘리브레이션 오류 의심. X Mean ≠ 0이면 X축 스테이지의 영점이 틀어진 것.</td></tr>"
                "<tr><td><b>StdDev (표준편차, σ)</b></td><td>√(Σ(x-μ)² / n)</td><td>측정 <b>재현성(Repeatability)</b>의 지표. 동일 조건에서 반복 측정했을 때 값이 흩어지는 정도. σ가 크면 스테이지 진동, 인코더 노이즈, 기계적 유격(Backlash) 등을 의심.</td></tr>"
                "<tr><td><b>Range (범위)</b></td><td>Max − Min</td><td>Single Outlier에도 민감하게 반응. Outlier 제거 후에도 Range가 크지 함께 확인해야 함. Out 지표와 교차 분석 필수.</td></tr>"
                "<tr><td><b>CV% (변동계수)</b></td><td>(σ / |μ|) × 100</td><td>Mean이 0에 가까울 때 CV%는 매우 민감하게 반응하므로, Mean이 극소값일 때 단독 해석에 주의.</td></tr>"
                "<tr><td><b>Out (이상치 수)</b></td><td>X, Y 축별 IQR 기준 탐지</td><td>축마다 Q1 − 1.5×IQR ~ Q3 + 1.5×IQR 밖의 데이터 수. Out &gt; 0이면 특정 Lot/시점에 국소적 이벤트(기계 충격, 진동등)가 발생했음을 시사.</td></tr>"
                "<tr><td><b>Cpk (공정능력지수)</b></td><td>min[(USL−μ)/3σ, (μ−LSL)/3σ]</td><td><b>1.33 이상이 합격 기준</b>(4σ). Cpk가 낮은데 σ가 작다면 Mean이 규격 중심에서 치우쳐있는 것으로 영점 재조정 필요.</td></tr>"
                "</table>"
                "<h3>종합 해석 사례</h3>"
                "<table>"
                "<tr><th>상황</th><th>엔지니어 해석 및 조치</th></tr>"
                "<tr><td>Mean 작음 + σ 큼</td><td>평균 위치는 맞지만 <b>재현성이 나쁜 상태</b> → 스테이지 진동 또는 인코더 노이즈 조사</td></tr>"
                "<tr><td>σ 작음 + Mean 큼</td><td>정밀하지만 <b>정확하지 않음(Accurate but not precise)</b> → Offset 보정(Calibration) 필요</td></tr>"
                "<tr><td>σ, Mean 둘 다 나쁜 경우</td><td>근본적인 <b>스테이지 정렬 실패</b> 검토 필요</td></tr>"
                "</table>"
            ),
            "2. Die & Raw 분산 테이블": (
                "<h2> 2. Die별 평균 & Raw Deviation 분석</h2>"
                "<h3>Die별 평균 (Die Average Heatmap)</h3>"
                "<p>Wafer 전면을 격자로 분할한 <b>각 Die 위치에서의 평균 오차</b>입니다. 색상 히트맵으로 표현되어 공간적 패턴을 직관적으로 파악할 수 있습니다.</p>"
                "<table>"
                "<tr><th>관심 패턴</th><th>시각적 특징</th><th>가능한 원인</th></tr>"
                "<tr><td><b>Edge Effect</b></td><td>Wafer 가장자리 Die만 붉거나 파란색</td><td>Chuck Edge 홀딩 불균일, 진공 흡착 불량, 가장자리 노광(Exposure) 편차</td></tr>"
                "<tr><td><b>대각선 편차</b></td><td>특정 대각 방향으로 색상이 치우침</td><td>스테이지 Pitch/Yaw 틀어짐, 레일 마모 편차</td></tr>"
                "<tr><td><b>중앙/주변 분리</b></td><td>중앙 정상 + 주변 이상 (또는 반대)</td><td>Chuck 곡률(Bow/Warp) 문제, 웨이퍼 핀 기준점 오류</td></tr>"
                "<tr><td><b>특정 Die 집중</b></td><td>단 1~2개 Die만 극단적 색상</td><td>해당 위치 인코더 이상, 해당 좌표 기구학적 이물질</td></tr>"
                "</table>"
                "<p><b>해석 방법:</b> StdDev 열과 함께 확인하세요. Avg만 크고 StdDev가 작으면 해당 위치에서의 오차는 <b>일관되고 반복적(즉, 구조적 문제)</b>입니다. Avg와 StdDev가 모두 크면 <b>랜덤한 노이즈</b> 성분이 강한 것입니다.</p>"
                "<hr>"
                "<h3>Raw Deviation </h3>"
                "<p>통계 처리 없이, <b>개별 측정(Repeat) × Die 위치</b>의 순수 측정값 행렬입니다.</p>"
                "<table>"
                "<tr><th>확인 방향</th><th>해석 내용</th></tr>"
                "<tr><td><b>수평 방향 (행: Repeat)</b></td><td>같은 Repeat 내 Die 간 오차 분포 확인. <b>특정 Repeat에서만 전 Die가 오차가 큰 경우</b> → 해당 측정 시점의 환경(진동, 온도 변화) 의심</td></tr>"
                "<tr><td><b>수직 방향 (열: Die)</b></td><td>동일 Die에서 Repeat 간 오차 변화 확인. <b>Repeat이 반복될수록 오차가 커지는 경우</b> → 해당 위치의 <b>Thermal Drift</b> 또는 <b>기계적 유격 누적</b> 의심</td></tr>"
                "</table>"
            ),
            "3. XY Scatter (산점도) 분석": (
                "<h2> 3. XY Scatter 플롯 심화 해석</h2>"
                "<p>XY Scatter는 <b>X 오차 vs Y 오차</b>를 2D 평면에 표현한 차트로, 스테이지의 <b>동작 특성을 가장 직관적으로 진단</b>할 수 있는 그래프입니다.</p>"
                "<h3>3.1 분포 형태별 엔지니어 해석</h3>"
                "<table>"
                "<tr><th>분포 형태</th><th>시각적 설명</th><th>원인 및 조치</th></tr>"
                "<tr><td><b>Bull's-eye (동심원 집중)</b></td><td>점들이 원점 중심으로 조밀하게 모임</td><td>✅ 정상 상태. 스테이지 정밀도 우수</td></tr>"
                "<tr><td><b>수직·수평 편위</b></td><td>점군이 X 또는 Y 한 방향으로만 치우침</td><td>해당 축 Linear Motor 또는 Encoder 오류, 오프셋 보정 필요</td></tr>"
                "<tr><td><b>사선 형태 (Linear Trend)</b></td><td>45° 또는 임의 각도의 선형 분포</td><td>X-Y 축 간 커플링(Coupling) 발생. 스테이지 <b>직각도(Squareness) 불량</b> 의심</td></tr>"
                "<tr><td><b>다중 군집</b></td><td>2~3개의 점 그룹이 분리되어 존재</td><td>Lot 간 재현 조건 차이(온도, 웨이퍼 로딩 변화) 또는 특정 레시피에만 나타나는 구조적 차이</td></tr>"
                "<tr><td><b>타원형 분포 (Ellipse)</b></td><td>타원형으로 넓게 퍼진 분포</td><td>두 축의 진동 크기가 다름. <b>기계 구동부의 강성(Stiffness) 불균형</b> 의심</td></tr>"
                "<tr><td><b>Outlier 산재</b></td><td>대부분은 집중, 일부가 멀리 떨어짐</td><td>특정 Lot의 이상 이벤트. Raw Data의 Outlier 탭과 교차 확인 필요</td></tr>"
                "</table>"
                "<h3>3.2 Spec Guide Box 해석</h3>"
                "<p>그래프 중앙의 <b>빨간 점선 사각형(Guide Box)</b> = ± Spec Range 한계치입니다.</p>"
                "<ul>"
                "<li>점들의 <b>분포 중심(Centroid)이 박스 안</b>에 있어도 <b>일부 점이 밖</b>에 있으면 → 오차 Range 초과로 FAIL</li>"
                "<li>박스가 매우 <b>좌우 빽빽하게 채워진</b> 경우 → 장비가 Spec 한계선 경계에서 운용 중. <b>Spec 여유도(Spec Margin) 검토 필요</b></li>"
                "<li><b>Log Scale 전환 시</b> → 극단적 Outlier의 영향을 줄이고 전체 분포 패턴 파악에 유리</li>"
                "</ul>"
            ),
            "3-1. X/Y Correlation 분석": (
                "<h2> 3-1. X/Y Correlation (상관 분석) 심화 해석</h2>"
                "<p>Correlation 차트는 <b>Die별 X 편차 (평균) vs Y 편차 (평균)</b> 를 2D 산점도로 표현하고, "
                "<b>선형 회귀(Linear Regression)</b> 를 적용하여 두 축 사이의 통계적 관계를 정량화합니다.</p>"

                "<h3>3-1.1 핵심 통계 지표 해석</h3>"
                "<table>"
                "<tr><th>지표</th><th>수식 / 범위</th><th>엔지니어 해석</th></tr>"
                "<tr><td><b>r (피어슨 상관계수)</b></td><td>-1 ~ +1</td>"
                "<td><b>방향과 강도</b><br>"
                "• |r| &lt; 0.3 → X/Y 축 독립 (양호)<br>"
                "• 0.3 ≤ |r| &lt; 0.7 → 약~중간 상관 (점검 권장)<br>"
                "• |r| ≥ 0.7 → 강한 상관 → 축간 <b>기계적 커플링</b> 의심</td></tr>"
                "<tr><td><b>R² (결정계수)</b></td><td>0 ~ 1 (= r²)</td>"
                "<td>X 편차로 Y 편차를 설명할 수 있는 비율.<br>"
                "R² ≈ 0 → 두 축 완전 독립 (이상적)<br>"
                "R² &gt; 0.5 → Y 변동의 50%이상이 X와 연동 → 구조적 결함</td></tr>"
                "<tr><td><b>RMSE</b></td><td>√(Σ(실측-예측)² / n)</td>"
                "<td>회귀선으로부터의 평균 잔차(μm). RMSE가 크면 <b>비선형 성분</b> 또는 <b>랜덤 노이즈</b>가 큰 것.</td></tr>"
                "<tr><td><b>회귀선 기울기 (slope)</b></td><td>y = slope·x + intercept</td>"
                "<td>기울기 ≈ 0 → X 이동 시 Y 영향 없음 (독립)<br>"
                "기울기 ≠ 0 → X 이동량에 비례해 Y도 밀림 → 직각도(Squareness) 불량 또는 기계적 간섭 의심</td></tr>"
                "</table>"

                "<h3>3-1.2 패턴별 진단 가이드</h3>"
                "<table>"
                "<tr><th>산점도 패턴</th><th>통계 지표</th><th>원인 및 조치</th></tr>"
                "<tr><td><b>원점 주변 원형 분포</b></td>"
                "<td>|r| &lt; 0.1, R² ≈ 0</td>"
                "<td>이상적 상태. 두 축이 완전 독립적으로 동작.</td></tr>"
                "<tr><td><b>양(+)의 대각선 분포</b></td>"
                "<td>r &gt; 0.5, 기울기 &gt; 0</td>"
                "<td>X(+) 방향 밀림 시 Y도 (+) 방향으로 밀림 → <b>동일 방향 커플링</b>. 스테이지 가이드 레일 평행도 점검.</td></tr>"
                "<tr><td><b>음(-)의 대각선 분포</b></td>"
                "<td>r &lt; -0.5, 기울기 &lt; 0</td>"
                "<td>X(+) 방향 밀림 시 Y는 (-) 방향으로 밀림 → <b>역방향 커플링</b>. 스테이지 직각도(Squareness) 또는 회전 오차 검토.</td></tr>"
                "<tr><td><b>수평/수직 밴드</b></td>"
                "<td>|r| &lt; 0.2</td>"
                "<td>한 축만 산포가 큰 상태. 해당 축의 <b>인코더 노이즈</b> 또는 <b>모터 진동</b> 개별 점검.</td></tr>"
                "<tr><td><b>군집 분리 (2~3개 그룹)</b></td>"
                "<td>r 값 불안정</td>"
                "<td>Repeat 간 조건 변동(온도, 웨이퍼 로딩) 또는 특정 Die 영역의 구조적 차이. <b>Outlier Die 식별</b> 후 개별 점검.</td></tr>"
                "<tr><td><b>극단적 Outlier 1~2개</b></td>"
                "<td>r 값 왜곡 가능</td>"
                "<td>해당 Die 특이점 분석. <b>Outlier 제거 후 r 값 재계산</b>하여 구조적 상관 여부 재평가.</td></tr>"
                "</table>"

                "<h3>3-1.3 Die 색상별 군집 해석</h3>"
                "<p>각 점은 <b>Die별로 고유 색상</b>이 부여됩니다. 특정 색상(Die)이 원점에서 크게 벗어나 있다면:</p>"
                "<ul>"
                "<li>해당 Die 위치에 <b>국소적 기구 이상</b>(Chuck 핀 높이 편차, 이물질 등)이 존재할 가능성</li>"
                "<li>Vector Map에서 같은 Die 위치의 벡터 방향/크기를 교차 확인하세요</li>"
                "<li>Die Average Heatmap에서 해당 Die의 평균 편차 수준과 대조하면 원인 격리에 유용합니다</li>"
                "</ul>"

                "<h3>3-1.4 실무 판단 기준 (요약)</h3>"
                "<table>"
                "<tr><th>|r| 범위</th><th>R² 범위</th><th>판정</th><th>조치</th></tr>"
                "<tr><td>&lt; 0.1</td><td>&lt; 0.01</td><td>정상</td><td>별도 조치 불필요</td></tr>"
                "<tr><td>0.1 ~ 0.3</td><td>0.01 ~ 0.09</td><td>관찰</td><td>트렌드 모니터링, 주기적 확인</td></tr>"
                "<tr><td>0.3 ~ 0.7</td><td>0.09 ~ 0.49</td><td>주의</td><td>축간 커플링 원인 조사 착수</td></tr>"
                "<tr><td>&gt; 0.7</td><td>&gt; 0.49</td><td>이상</td><td>즉시 기구 점검 (직각도, 가이드, 베어링)</td></tr>"
                "</table>"

                "<h3>3-1.5 XY Scatter와의 차이점</h3>"
                "<table>"
                "<tr><th>항목</th><th>XY Scatter (Basic Analysis)</th><th>Correlation (Advanced)</th></tr>"
                "<tr><td><b>데이터 단위</b></td><td>모든 개별 측정점 (Repeat x Die)</td><td><b>Die별 평균</b> 오차 (Repeat 평균화)</td></tr>"
                "<tr><td><b>초점</b></td><td>전체 산포 분포 + Spec Box 판정</td><td><b>X-Y 축간 상관관계</b> (커플링 진단)</td></tr>"
                "<tr><td><b>통계 지표</b></td><td>없음 (시각적 판단)</td><td>R², r, RMSE, 회귀식 정량 제공</td></tr>"
                "<tr><td><b>활용</b></td><td>Spec PASS/FAIL 판단, Outlier 탐지</td><td>기계 구조적 결함 진단, 축 독립성 검증</td></tr>"
                "</table>"
            ),
            "4. Vector & Wafer Map 이해": (
                "<h2> 4. Vector Map & Wafer Contour 심화 분석</h2>"
                "<h3>4.1 Vector Map (벡터맵) 패턴 해석</h3>"
                "<p>각 Die 위치에서의 <b>오차 벡터(크기 + 방향)</b>를 화살표로 표현합니다. 단순한 크기가 아니라 <b>방향성 패턴</b>이 핵심입니다.</p>"
                "<table>"
                "<tr><th>벡터 패턴</th><th>엔지니어 해석 및 조치</th></tr>"
                "<tr><td><b>모든 화살표가 같은 방향</b></td><td>고정 오프셋(Constant Offset). Mean 값이 0에서 치우쳐 있는 상태. <b>Calibration으로 즉시 수정 가능</b></td></tr>"
                "<tr><td><b>방사형 (중앙→가장자리로 퍼짐)</b></td><td>스케일 오차(Scale Error). <b>EGA 스케일 파라미터 재보정</b> 필요</td></tr>"
                "<tr><td><b>소용돌이형 (Swirl)</b></td><td>스테이지 회전(Rotation/Theta) 오차. <b>Theta 축 Offset 조정</b> 필요</td></tr>"
                "<tr><td><b>지역적 불규칙</b></td><td>특정 구역만 벡터 방향·크기 이상 → 해당 영역의 <b> Chuck 핀 이상</b> 또는 <b>국소 기구 마모</b> 검토</td></tr>"
                "<tr><td><b>랜덤 방향 (무작위)</b></td><td>구조적 패턴 없음 → 측정 재현성 자체가 부족. <b>진동 방지(Isolation)</b>나 측정 환경 개선 검토</td></tr>"
                "</table>"
                "<p><b>Vector Scale 슬라이더 활용:</b> 실제 오차는 수 μm 이내라 육안 관찰이 어렵습니다. 슬라이더로 벡터 길이를 수십~수백 배 증폭하여 패턴을 명확하게 확인하세요. 단, <b>증폭된 길이 자체가 실제 오차 수치가 아님</b>에 유의하세요.</p>"
                "<hr>"
                "<h3>4.2 Wafer Contour (등고선 맵) 해석</h3>"
                "<p>Wafer 전체의 <b>오차 분포를 연속적인 컬러 맵</b>으로 시각화합니다. 측정하지 않은 Die 사이의 영역은 보간법(Interpolation)을 적용하여 보정합니다.</p>"
                "<table>"
                "<tr><th>색상</th><th>의미</th></tr>"
                "<tr><td>🔴 붉은 계열</td><td>양(+) 방향의 큰 오차 구역 (High-Deviation Zone)</td></tr>"
                "<tr><td>🔵 청색/녹색 계열</td><td>음(-) 방향 또는 오차가 작은 구역 (Low-Deviation Zone)</td></tr>"
                "</table>"
                "<p><b>색상 전환이 급격한 구역</b> → <b>오차 변화율(Gradient)이 급격</b>하게 변하는곳으로, 해당 위치에 기구적 변곡점(기계적 틀어짐)이 존재할 가능성이 있음.</p>"
                "<p><b>X / Y 채널 교차 비교:</b> X 채널은 정상이고 Y만 등고선이 기울어진다면 → Y축 단독 부품(인코더, 가이드 레일, 모터)의 문제일 수 있으므로 점검이 필요합니다. X와 Y 두 채널의 오차 경향(패턴)이 유사한 경우 → 공통 원인(기울어진 Chuck, 하중 편심) 가능성.</p>"
            ),
            "5. Spec 판정과 오류 처리": (
                "<h2> 5. Spec 판정 (PASS/FAIL) 기준 및 설정 로직</h2>"
                "<h3>⚠️ 5.0 분석 대상 Die 범위 — 반드시 확인</h3>"
                "<table>"
                "<tr><th>항목</th><th>내용</th></tr>"
                "<tr><td><b>전체 측정 Die</b></td><td>22 Die</td></tr>"
                "<tr><td><b>제외 Die</b></td><td><b>Die #1 (첫 번째 Die)</b> — 스테이지 안정화(Stabilization) 명목으로 <b>자동 제외</b></td></tr>"
                "<tr><td><b>실제 분석 기준</b></td><td><b>총 21 Die</b> (Die #2 ~ #22)</td></tr>"
                "<tr><td><b>제외 Repeat</b></td><td>폴더가 <b>11개 이상</b>이고 측정 순서상 첫 폴더의 <b>Repeat No가 1</b>이면 그 폴더를 <b>자동 제외</b>합니다. 폴더가 10개면 그대로 분석합니다. 제외 내역은 System Log에 남습니다.</td></tr>"
                "<tr><td><b>적용 대상</b></td><td>Summary 탭의 X/Y Dev Range, StdDev, Result 전체, Step 버튼 PASS/FAIL, Statistics 행, Lot Trend, Recipe 비교 차트, Excel·PDF 통계. Step 카드·Die/Deviation 표·그래프의 기본값</td></tr>"
                "<tr><td><b>22 Die 유지</b></td><td>Raw Data 표, CSV, Excel의 Raw Data·Anomalies·By Die 시트. 파일에는 분석 대상 열로 구분합니다.</td></tr>"
                "</table>"
                "<p><b>사용자 인지 포인트:</b></p>"
                "<ul>"
                "<li><b>Die 필터</b>는 스캔할 때마다 <b>Die #1 해제 상태</b>로 시작합니다. 개별 Die 선택/해제는 <b>Step 카드, Die/Deviation 표, 그래프(Contour, Scatter 등)에만 실시간 반영</b>되고, 기본과 다르면 카드와 필터 안내에 Custom filter로 표시됩니다.</li>"
                "<li><b>Summary 체크리스트 값</b>(Dev Range, StdDev, PASS/FAIL)은 항상 <b>안정화 Die(#1) 제외 후 21 Die 기준</b>으로 집계됩니다.</li>"
                "<li>따라서 체크리스트에 기입하는 수치는 <b>Die 필터 조작과 무관하게 고정</b>됩니다.</li>"
                "</ul>"
                "<hr>"
                "<h3>5.1 판정 흐름</h3>"
                "<table>"
                "<tr><th>단계</th><th>내용</th></tr>"
                "<tr><td>1. 스캔 데이터 수집</td><td>CSV 폴더에서 원시 수치 로드</td></tr>"
                "<tr><td>2. Deviation Matrix 계산</td><td>overall_range, overall_stddev 산출</td></tr>"
                "<tr><td>3. Spec값과 비교</td><td>overall_range ≤ spec_range <b>and</b> overall_stddev ≤ spec_stddev</td></tr>"
                "<tr><td>4. 최종 판정</td><td><b>두 조건 모두 충족</b> → ✅ PASS / <b>하나라도 초과</b> → ❌ FAIL / <b>Die 2개 미만, Spec 미정의</b> → — (판정 안 함, Cpk도 —). <b>Repeat 1개</b> → 판정·Cpk를 내되 ⚠ 1 Repeat 참고 판정으로 표시(반복성 미확인). 경고가 있는 PASS는 카드에 ⚠ PASS로 표시</td></tr>"
                "</table>"
                "<h3>5.2 Spec 설정 값 의미</h3>"
                "<table>"
                "<tr><th>Spec 항목</th><th>의미</th><th>설정 가이드</th></tr>"
                "<tr><td><b>spec_range</b></td><td>허용되는 최대 오차 범위 (μm)</td><td>고객/장비 사양의 Positioning Accuracy 값을 기준으로 설정</td></tr>"
                "<tr><td><b>spec_stddev</b></td><td>허용되는 최대 표준편차 (μm)</td><td>Repeatability 사양 기준으로 설정. 일반적으로 range의 1/3~1/5 수준</td></tr>"
                "</table>"
                "<h3>5.3 폴더명과 Workflow 연결 (Workflow별 선택)</h3>"
                "<p>Scan 하면 루트 폴더 바로 아래 폴더 이름을 Spec 이름과 비교해 <b>자동으로 연결</b>합니다. 대소문자와 앞의 번호(<code>4. </code> 등)는 무시합니다.</p>"
                "<table>"
                "<tr><th>Spec 이름</th><th>자동 연결되는 폴더명 (✅)</th><th>자동 연결되지 않는 폴더명 (❌)</th></tr>"
                "<tr><td>Vision Pattern</td><td>Vision Pattern, 1. Vision Pattern, vision pattern</td><td>1. Vision Pattern Recognize, VisionPttrn</td></tr>"
                "<tr><td>In-Die Align</td><td>In-Die Align, 2. In-Die Align</td><td>In Die Align, in_die_align</td></tr>"
                "<tr><td>LLC Translation</td><td>LLC Translation, 3. LLC Translation</td><td>LLC Trans, LLCtranslation</td></tr>"
                "<tr><td>Global Align</td><td>Global Align, 4. Global Align</td><td>GlobalAlign</td></tr>"
                "</table>"
                "<p>이름이 다른 폴더가 있거나, 고른 폴더에 Lot 폴더가 바로 들어 있으면(예: <code>1</code>~<code>14</code> Lot만 있는 Vision Pattern 폴더) <b>Workflow별 폴더 선택</b> 창이 열립니다. "
                "상단의 <b>Workflow별 선택</b> 버튼으로 언제든 직접 열 수도 있습니다.</p>"
                "<ul>"
                "<li>Workflow마다 데이터 폴더를 지정합니다. 폴더 이름은 Spec 이름과 달라도 됩니다. 날짜 폴더, 반복 수가 다른 시험 폴더도 됩니다.</li>"
                "<li><b>사용</b>에 체크한 Workflow만 분석합니다. 체크하지 않은 Workflow는 건너뜁니다.</li>"
                "<li>상태 칸에 Lot 수(예: <code>✅ Lot 14개 · (root)</code>, <code>✅ 1st Lot 11개, 2nd Lot 10개</code>)가 보입니다. Lot(Repeat) 수는 참고용이며 분석을 막지 않습니다.</li>"
                "<li>체크한 Workflow의 폴더가 없거나 Lot 데이터가 없으면 확인할 수 없습니다. 같은 폴더를 두 Workflow에 지정하면 경고만 보여 줍니다.</li>"
                "<li>고른 폴더는 다음에 다시 열 때 <b>(지난번 선택)</b>으로 채워집니다. 스캔에서 열린 경우, 선택한 폴더 밖의 지난번 폴더와 Lot이 바로 들어 있는 선택한 폴더는 <b>체크하지 않은 채</b> 보여 주므로 어느 Workflow인지 확인한 뒤 체크하세요. 경로는 전체 경로로 입력하며, 탐색기 '경로로 복사'의 큰따옴표는 자동으로 뗍니다.</li>"
                "</ul>"
                "<h3>5.4 회차(트러블슈팅 반복) 폴더</h3>"
                "<p>Workflow 폴더 아래에 <code>1</code>~<code>14</code>나 날짜 이름의 폴더가 있고 그 안에 Lot이 있으면 각 폴더를 <b>회차</b>로 보고 <b>모든 회차를 따로 분석</b>합니다. 회차마다 21 Die × 10 Repeat 기준, 첫 리핏 제외, 이상치 판정이 그대로 적용됩니다.</p>"
                "<ul>"
                "<li>Step 상세(카드·차트·PDF·Excel)는 기본으로 <b>마지막 회차</b>를 보여 줍니다. 카드 아래 <b>Spec Config</b> 버튼 줄의 <b>회차</b> 콤보에서 바꿀 수 있고, PDF에는 <code>Run: 14 (14회차 중)</code>처럼 적힙니다. 최신 회차에 데이터가 없으면 데이터가 있는 마지막 회차가 기본이 되고 System Log에 남습니다.</li>"
                "<li>회차 폴더는 숫자 값 순서로 정렬합니다(1, 2, …, 10). 날짜 이름은 <code>YYYY-MM-DD</code>(예: <code>2026-11-01</code>)로 쓰면 날짜 순서대로 정렬됩니다.</li>"
                "<li>기존 <code>1st</code>/<code>2nd</code> 구조는 지금처럼 <b>1st</b>가 기본이며 값도 같습니다. 회차 콤보·Run Comparison 탭으로 2nd와 비교할 수 있습니다.</li>"
                "<li>Chart 영역 <b>Comparison → Run Comparison</b> 탭에서 회차별 Range·StdDev·Avg·Cpk·판정 표와 추이·박스플롯을 봅니다. 행을 클릭하면 그 회차로 바뀝니다. ⚠는 경고가 있는 회차입니다.</li>"
                "<li>Excel 리포트에는 회차가 둘 이상인 Workflow마다 <b>Run Comparison</b> 시트가 붙습니다.</li>"
                "</ul>"
                "<h3>5.5 AI 분석용 Markdown (LLM 원인 분석)</h3>"
                "<p>Remark → Export의 <b>AI 분석용 Markdown</b>은 'Spec이 왜 나갔는지'를 LLM(ChatGPT, Claude 등)에 물어보기 위한 파일입니다. 화면은 사람이 보는 용도이고, 원인 추론은 이 파일을 LLM에 붙여 넣어 맡깁니다.</p>"
                "<ul>"
                "<li>한 파일(.md, UTF-8)에 분석 조건·결과 요약(Spec 초과 먼저, 여유 µm·%)·원인 단서(축별 평면 계수 Sx·Sy·θx·θy와 회전·직교도, 계통 성분을 뺀 what-if, Die별 공간 분포와 평면 잔차, Lot 순서별 drift, 이상치·측정 실패)·회차 비교·선택 회차의 Die×Repeat 편차 표가 들어 있습니다. 원인 단서는 화면에서 고른 회차 기준입니다. 모든 회차의 셀 값은 같은 이름에 <code>_raw.csv</code>가 붙은 CSV로 옆에 저장되며, 필요하면 LLM에 함께 첨부합니다.</li>"
                "<li><b>회차 변경 이력 (사용자 기입)</b> 표에 회차마다 바꾼 모듈·설정을 직접 적은 뒤 LLM에 넣으면 회차 간 차이의 원인까지 묻습니다.</li>"
                "<li>파일 끝의 <b>LLM 분석 요청</b>이 질문입니다. 파일 전체를 그대로 붙여 넣거나 첨부하면 됩니다. 파일이 크면 첨부를 쓰세요.</li>"
                "<li><b>(파생)</b> 표시는 도구가 유도한 참고값·경험 규칙이며 통계 검정이 아닙니다. 회차 비교에는 회차별 평면 계수와 회차 × Die 평균 편차 표가 있어, 바꾼 내용이 기하 오차를 움직였는지 볼 수 있습니다.</li>"
                "<li>LLM 답은 이 파일의 숫자만 근거로 한 추정입니다. 장비 사실 확인과 결론은 담당자가 합니다.</li>"
                "</ul>"
                "<h3>5.5-1 비전 보정 로그 대조 (Advanced → Positioning Log)</h3>"
                "<p>Lot 폴더 <code>Debug/SamplePositioning</code>·<code>Debug/TipPositioning</code> 로그를 최종 착지(HZ1_O 편차)와 대조합니다. "
                "Die마다 도착 오차(Coarse Match), 비전 보정(Coarse·Fine Move), 정밀 잔차(Fine Match), Optical Shift, Tip 위치를 Die 좌표에 평면 피팅해 "
                "Sx·θx·θy·Sy(t)와 Range, HZ1_O와의 상관(값 그대로 r, 계수별 부분 잔차 r)을 표로 보입니다. 최종 착지의 유의한 성분(|t| ≥ 3)이 비전 로그 어디에도 "
                "같은 계수(부분 잔차 상관·부호까지 맞음)로 없으면 <b>비전 정렬 이후(카메라→Tip 착지) 구간</b>, 있으면 그 단계 구간과 같은·반대 부호를 표시합니다"
                "(파생, 규칙은 표시줄 툴팁). 좌표가 한쪽으로 몰리거나 한 직선이면 그 계수는 —입니다. "
                "로그 행은 측정 순서로 Die에 짝짓고 Die 좌표로 확인합니다(샘플 정렬 회전·원점 차는 맞춘 뒤 비교, 회전이 1°를 넘거나 다른 샘플·Slot의 로그면 그 Lot은 빼고 이유를 보입니다). "
                "로그는 이 탭을 열 때 읽습니다(데이터 로드는 로그를 읽지 않음). 네트워크·잠김으로 읽지 못하면 '로그 없음'과 따로 알리고 다음에 다시 읽습니다. "
                "Tip Positioning이 한 위치(Center)뿐이면 Tip X와 스테이지 Y 관계는 알 수 없어, Y +120 / 0 / −120 mm 세 곳에서 해 보기를 제안합니다. "
                "같은 내용이 AI 분석용 Markdown의 '비전 보정 로그 대조' 절에 들어갑니다.</p>"
                "<h3>5.5-2 트러블슈팅 진단 (Advanced → Diagnosis (트러블슈팅))</h3>"
                "<p>선택한 Step·회차의 측정값과 Lot 폴더 <code>Log/&lt;Sample&gt;_Log.csv</code>로 X/Y Spec Out 원인 후보를 자동으로 찾습니다. "
                "찾는 원인의 예: X 라인을 스캐너 Y −28 µm에서 재면 Y 구동이 웨이퍼를 µm당 약 0.19 µrad 돌려 θx ≈ 5 µrad, X FAIL "
                "(레시피 X 라인 Y Offset −25 → −5 µm로 θx 0.95, X PASS).</p>"
                "<ul>"
                "<li><b>스캐너 오프셋</b>: Log의 ImageJob 'Set scan params'(Scan Direction, X/Y Offset)가 측정 라인마다 스캐너에 건 값입니다. "
                "<b>적용 = 레시피 + 기준 보정(Reference, 기준 스캔) + 미세 보정(Fine, 정밀 정렬)</b>. Log에 기준 스캔 결과가 없으면(장비에 저장된 값을 씀) "
                "레시피와 기준 보정을 나눌 수 없어 '—'로 두고 '레시피 + 기준 보정'만 보입니다(툴팁).</li>"
                "<li><b>모양</b>: Die 평균 편차를 Die 좌표에 평면 피팅(안정화 Die 제외). X는 θx = ∂dx/∂y, Y는 θy = ∂dy/∂x (µrad, t = 값/SE).</li>"
                "<li><b>회전</b>: κ_eff = |θ| / |구동 축 스캐너 오프셋| (X 라인은 스캐너 Y, Y 라인은 스캐너 X; 오프셋 2 µm 이상일 때만). "
                "Range 기여 = |θ| × Die 좌표 폭.</li>"
                "<li><b>What-if</b>: X 라인 레시피 Y Offset을 지금 값과 같은 쪽(부호)의 25, 15, 10, 5, 3, 0 µm(와 현재 값)로 바꿨을 때 이 회차 "
                "Lot·Die 값에서 κ_eff × (현재 − 새 값) × Die Y를 빼 Lot별 Range·StdDev·PASS Lot 수를 예측합니다(선형·원점 통과 가정 — 실데이터는 "
                "원점을 지나지 않아 근사이며, 줄이는 쪽으로는 보수적. θ가 유의하지 않으면 예측 안 함). |현재 값| &lt; 0.5 µm는 0으로 봅니다. "
                "굵게 = 현재, 초록 = 권장: 현재 행이 모든 Lot PASS면 현재 유지, 아니면 패턴 꼭짓점 여유 5 µm 이상이고 모든 Lot PASS인 후보 중 "
                "|값|이 가장 작은 것 (없으면 권장 없음, 3D 보고서와 같은 규칙). 레시피 값을 모르면(기준 스캔 없음) 행은 스캐너 Y 적용값 목표입니다.</li>"
                "<li><b>Drift</b>: Lot 평균 대 Lot 시작 시각(CSV Start Time), Lot 안 측정 순서 기울기(µm/Die, 유의한 평면 모양만 뺀 값). "
                "Lot 3개 이상에서 축별 Lot 평균 폭 &gt; 0.2 µm 또는 첫 Lot |기울기| &gt; 0.01 µm/Die(|r| ≥ 0.5)면 주의 — 예열(열) 드리프트 의심, "
                "측정 전 워밍업/더미 Lot 권장.</li>"
                "<li><b>Transient</b>: Lot마다 Die4 이후로 맞춘 평면 대비 Die1·2·3 잔차의 Lot 평균. Lot 3개 이상에서 판정 대상 Die2가 0.15 µm를 넘고 "
                "|평균|/SE ≥ 3이면 주의.</li>"
                "</ul>"
                "<p><b>잠정 규격</b> (settings.json <code>rotation_spec</code>, 장비 데이터 평균 + 3σ로 확정 전): |θ| 주의 0.7 / 한도 1.0 µrad "
                "(±120 mm Die에서 Range 기여 θ × 0.24 µm, 1.0 µm Spec의 25 %), κ 주의 0.07 / 한도 0.10 µrad/µm, 측정 라인 스캐너 오프셋 한도 10 µm. "
                "회전: θ가 유의(|t| ≥ 3, Die 8개 이상)하고 한도 이상이면 이상, 주의 이상이면 주의. κ_eff는 구동 ≥ 5 µm이고 |θ| ≥ 0.35 µrad일 때만 "
                "주의 이상이면 적어도 주의(그 밖은 참고). "
                "X 라인 |스캐너 Y| &gt; 10 µm면 레시피 확인 주의. Y 라인 |스캐너 X| &gt; 10 µm는 <b>참고</b>(기준 장비에서 X 구동은 돌지 않았음).</p>"
                "<p><b>부품 판정 규칙</b> (경험 규칙, 근거는 표의 문장):</p>"
                "<ul>"
                "<li><b>XY 스캐너 Y 구동</b>: <b>이상</b>은 최종 θx가 유의하고 한도 이상이며, X 라인 |스캐너 Y| ≥ 5 µm이고, Positioning Log 분석이 "
                "그 성분을 비전 정렬 이후로 볼 때만입니다. 로그 대조 전이면 많아야 주의('비전 로그 대조 전 — Positioning Log 확인 시 확정'), "
                "스캐너 Y를 모르면 주의, 5 µm 미만으로 구동했으면 —('스캐너 구동이 작아 원인으로 단정 불가'). 비전 단계가 같은 성분을 가졌으면 OK"
                "(구동이 10 µm를 넘으면 주의). 주의 구간 θ는 같은 조건에서 주의. |스캐너 Y| &lt; 2 µm면 '줄이세요'를 내지 않습니다.</li>"
                "<li><b>척</b>: 스캐너 Y가 주의·이상이면 주의 — 데이터로는 스캐너와 척을 나눌 수 없습니다.</li>"
                "<li><b>비전 / XY 스테이지</b>: Positioning Log가 그 성분을 비전 로그 단계에 돌리면(도착·거친 보정 → XY 스테이지, "
                "정밀 정렬·보정·Optical Shift·Tip 위치 → 비전) 주의·이상. 로그 대조가 없으면 —. 이 탭을 열면 Positioning Log 분석도 읽어 반영합니다.</li>"
                "<li><b>Z 스캐너·Tip / F 스테이지</b>: drift나 transient가 주의면 주의 (헤드 쪽 열·안착).</li>"
                "<li><b>XY 스캐너 X 구동</b>: Y 라인(θy, 스캐너 X)에 같은 규칙. Y 라인에 같은 모양이 없으면 OK.</li>"
                "<li><b>스캐너 회전 시험</b>(폐루프 2지점 스윕, 출하 검사·스캐너 교체·선형화 후 관리 포인트) 결과가 있으면 요약 칩에 κ·판정·시험 단계·날짜를 보이고, "
                "폐루프·Y 축 시험이 주의·이상이면 XY 스캐너 Y 구동을 적어도 그 판정으로 올립니다(직접 증거). 시험 결과는 장비의 것이라 회차를 바꿔도 "
                "남지만, 같은 장비(Log의 Tool ID, 없으면 연 폴더)의 진단에만 붙고 다른 장비면 '다른 장비의 시험 결과 — 적용 안 함'을 보입니다.</li>"
                "<li>그 밖(Z 스테이지, 샘플, 본체, 제진대, EFEM)은 이 데이터로 판단할 신호가 없어 OK, 데이터가 없으면 —.</li>"
                "</ul>"
                "<p>Log는 이 탭을 열 때 그 회차만 읽습니다(Lot마다 Log 폴더 목록·Log 파일·CSV 앞부분). 네트워크·잠김으로 읽지 못하면 'Log 없음'과 따로 "
                "알리고 담지 않아 다음에 다시 읽습니다. <b>진단 JSON 저장</b>은 <code>pass.xy.diagnosis/1</code> 형식으로 저장합니다(3D 보고서·회전 시험 분석이 씀). "
                "같은 내용이 AI 분석용 Markdown의 '트러블슈팅 진단 (Diagnosis)' 절에 들어가고, Comparison → Run Comparison 표와 Excel에는 회차별 θx·θy(CSV 값만)가 있습니다.</p>"
                "<h3>5.6 Cpk와 Spec Range 연계 해석</h3>"
                "<p>Cpk는 <code>spec_limits</code>의 LSL/USL 기준이며, PASS/FAIL 판정은 <code>spec_deviation</code>의 <code>spec_range</code>/<code>spec_stddev</code> 기준입니다. 두 기준이 <b>독립적</b>으로 동작하므로, <b>Cpk가 높아도 Range가 Spec을 초과하면 FAIL</b>이 날 수 있습니다. 반드시 두 지표를 함께 확인하세요. 단, 판정이 <b>—</b>이면(Spec 미정의 또는 Die 2개 미만) Cpk도 —로 표시합니다. 1 Repeat 참고 판정이면 Cpk에도 (참고)를 붙입니다.</p>"
            )
        }

        for title in self.contents.keys():
            self.nav_list.addItem(title)
            
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)

        # Right Panel (Content)
        self.content_browser = QTextBrowser()
        self.content_browser.setStyleSheet(f"""
            QTextBrowser {{ background: {BG2}; border: 1px solid {BG3}; border-radius: 6px; padding: 24px; font-size: 11pt; }}
        """)
        self.content_browser.setOpenExternalLinks(False)

        splitter.addWidget(self.nav_list)
        splitter.addWidget(self.content_browser)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

        layout.addWidget(splitter)
        
        # 버튼 박스
        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.setStyleSheet(f"""
            QPushButton {{ background: {BG3}; color: {FG}; padding: 8px 30px; border-radius: 4px; border: 1px solid {BG3}; font-weight: bold; }}
            QPushButton:hover {{ background: {FG2}; color: {BG}; }}
        """)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.nav_list.setCurrentRow(0)

    def _on_nav_changed(self, idx):
        if idx < 0: return
        itemTitle = self.nav_list.item(idx).text()
        html_body = self.contents.get(itemTitle, "")
        
        css = (
            f"h2 {{ color: {ACCENT}; border-bottom: 2px solid {BG3}; padding-bottom: 12px; margin-bottom: 20px; font-size: 18pt; }}"
            f"h3 {{ color: {ACCENT}; margin-top: 32px; font-size: 14pt; }}"
            f"p {{ line-height: 1.6; margin-bottom: 16px; color: {FG}; }}"
            f"b {{ color: {GREEN}; font-weight: bold; font-size: 11pt; }}"
            f"ul {{ margin-top: 8px; margin-bottom: 16px; margin-left: -10px; }}"
            f"li {{ margin-bottom: 10px; line-height: 1.6; color: {FG}; }}"
            f"hr {{ border: none; border-top: 1px solid {BG3}; margin: 28px 0; }}"
            f"table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}"
            f"th, td {{ border: 1px solid {BG3}; padding: 12px; text-align: left; line-height: 1.4; }}"
            f"th {{ background-color: {BG}; color: {FG}; font-weight: bold; border-bottom: 2px solid {BG3}; }}"
            f"td {{ color: {FG}; }}"
            f"code {{ background-color: rgba(0,0,0,0.3); padding: 4px 6px; border-radius: 4px; color: {RED}; font-family: Consolas, monospace; }}"
        )
        
        full_html = "<html><head><style>" + css + "</style></head><body>" + html_body + "</body></html>"
        self.content_browser.setHtml(full_html)
