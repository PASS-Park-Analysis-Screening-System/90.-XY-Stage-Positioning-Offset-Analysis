@echo off
chcp 65001 >nul 2>&1
REM ====================================================
REM  XY Stage Positioning Offset Analysis - PyInstaller Build
REM  Usage: build.bat
REM ====================================================

set PROJECT_DIR=%~dp0
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo.
echo ============================================
echo   XY Stage Offset Analysis - PyInstaller Build
echo ============================================
echo.

REM  add-data의 대상 경로는 런타임 조회 경로와 일치해야 한다.
REM    assets  : ui_builder_mixin이 __file__ 기준 ..\..\assets 로 찾음 → _MEIPASS\assets
REM              (이전 값 src\assets는 어긋나서 EXE에서 가이드 이미지가 뜨지 않았다)
REM    settings: settings.py가 _MEIPASS\core\settings.json 으로 찾음
REM  hidden-import는 main.py가 sys.path에 src를 넣고 쓰는 실제 패키지명(core/ui/charts)이다.
python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name "XYStageOffset" ^
    --distpath dist ^
    --workpath build ^
    --specpath build ^
    --add-data "%PROJECT_DIR%src\assets;assets" ^
    --add-data "%PROJECT_DIR%src\core\settings.json;core" ^
    --collect-all pspylib ^
    --hidden-import=core ^
    --hidden-import=ui ^
    --hidden-import=charts ^
    src\main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

REM 이전 버전이 생성한 외부 설정 파일 제거 — 배포 결과는 EXE 하나만 유지
if exist "%PROJECT_DIR%dist\settings.json" del /q "%PROJECT_DIR%dist\settings.json"

echo.
echo ============================================
echo   Build Complete!
echo ============================================
echo   EXE: dist\XYStageOffset.exe
echo.
pause
