"""pytest 공통 설정 — src/ 를 import 경로에 추가 (앱은 main.py에서 동일하게 처리)."""
import os
import sys

_SRC = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')
)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def show_expert_tab(window, name):
    """전문 분석 창을 열고 그 안의 Advanced 탭 name을 앞에 둔다.

    Advanced(Pareto·Correlation·3D Surface·Positioning Log·Diagnosis)는 메인 창 chart_category_tabs가 아니라
    전문 분석 창에 있다. 그 탭은 창이 열려 있고(최소화 아님) 앞에 있을 때만 '보인다'.
    """
    window._open_expert_window()
    window._select_chart(name)
    assert window._advanced_tab_visible(name), name
