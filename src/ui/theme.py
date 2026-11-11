"""
theme.py — 색상 상수 및 다크 테마 CSS (Catppuccin Mocha)
"""

# ═══════════════════════════════════════════════
#  Color Constants (Catppuccin Mocha)
# ═══════════════════════════════════════════════
BG      = '#1e1e2e'
BG2     = '#282a3a'
BG3     = '#313244'
FG      = '#cdd6f4'
FG2     = '#a6adc8'
ACCENT  = '#89b4fa'
GREEN   = '#a6e3a1'
RED     = '#f38ba8'
ORANGE  = '#fab387'
PURPLE  = '#cba6f7'

DARK_STYLE = f"""
QMainWindow, QWidget {{ background-color: {BG}; color: {FG}; }}
QSplitter::handle {{ background-color: {BG3}; }}
QTabWidget::pane {{ border: 1px solid {BG3}; background: {BG}; }}
QTabBar::tab {{
    background: {BG3}; color: {FG2}; padding: 8px 16px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    font-weight: bold; font-size: 9pt; margin-right: 2px;
}}
QTabBar::tab:selected {{ background: #45475a; color: {ACCENT}; }}
QTabBar::tab:hover {{ background: #3b3d50; }}
QPushButton {{
    background: {BG3}; color: {FG}; border: none; padding: 6px 14px;
    border-radius: 4px; font-size: 9pt;
}}
QPushButton:hover {{ background: {ACCENT}; color: {BG}; }}
QPushButton[accent="true"] {{
    background: {ACCENT}; color: {BG}; font-weight: bold;
}}
QPushButton[accent="true"]:hover {{ background: #74c7ec; }}
QPushButton[step="true"] {{
    padding: 8px 14px; font-weight: bold;
}}
QPushButton[active_step="true"] {{
    background: {ACCENT}; color: {BG}; font-weight: bold; padding: 7px 13px;
    border-radius: 4px; border: 3px solid #ffffff;
}}
QPushButton[step_pass="true"] {{
    background: {GREEN}; color: {BG}; font-weight: bold; padding: 8px 14px;
    border-radius: 4px; border: 2px solid transparent;
}}
QPushButton[step_pass="true"]:hover {{ background: #8ec07c; }}
QPushButton[step_fail="true"] {{
    background: {RED}; color: {BG}; font-weight: bold; padding: 8px 14px;
    border-radius: 4px; border: 2px solid transparent;
}}
QPushButton[step_fail="true"]:hover {{ background: #cc241d; }}
QPushButton[step_active_pass="true"] {{
    background: {GREEN}; color: {BG}; font-weight: bold; padding: 7px 13px;
    border-radius: 4px; border: 3px solid #ffffff;
}}
QPushButton[step_active_pass="true"]:hover {{ background: #8ec07c; }}
QPushButton[step_active_fail="true"] {{
    background: {RED}; color: {BG}; font-weight: bold; padding: 7px 13px;
    border-radius: 4px; border: 3px solid #ffffff;
}}
QPushButton[step_active_fail="true"]:hover {{ background: #cc241d; }}
QPushButton[step_ref_pass="true"] {{
    background: {ORANGE}; color: {BG}; font-weight: bold; padding: 8px 14px;
    border-radius: 4px; border: 2px solid transparent;
}}
QPushButton[step_active_ref_pass="true"] {{
    background: {ORANGE}; color: {BG}; font-weight: bold; padding: 7px 13px;
    border-radius: 4px; border: 3px solid #ffffff;
}}
QLineEdit {{
    background: {BG3}; color: {FG}; border: 1px solid #45475a;
    padding: 4px 8px; border-radius: 4px;
}}
QTableWidget {{
    background: {BG2}; color: {FG}; gridline-color: #45475a;
    border: none; font-family: 'Consolas'; font-size: 9pt;
}}
QTableWidget::item:selected {{ background: #45475a; }}
QHeaderView::section {{
    background: {BG3}; color: {ACCENT}; font-weight: bold;
    padding: 4px; border: none; font-size: 9pt;
}}
QTextEdit {{
    background: #181825; color: {FG}; border: none;
    font-family: 'Consolas'; font-size: 9pt;
}}
QStatusBar {{ background: {BG3}; color: {FG2}; font-family: 'Consolas'; font-size: 9pt; }}
QScrollBar:vertical {{
    background: {BG2}; width: 10px; border: none;
}}
QScrollBar::handle:vertical {{ background: #45475a; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{
    background: {BG2}; height: 10px; border: none;
}}
QScrollBar::handle:horizontal {{ background: #45475a; border-radius: 5px; min-width: 30px; }}
"""
