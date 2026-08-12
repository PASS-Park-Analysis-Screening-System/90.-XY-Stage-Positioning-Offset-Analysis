@echo off
chcp 65001 >nul 2>&1
REM ====================================================
REM  XY Stage Positioning Offset Analysis - PyInstaller Build
REM  Usage: build.bat
REM
REM  빌드 설정(번들 리소스, hidden import 등)은 이 파일이 아니라
REM  tools\build_config.py 한 곳에만 있다. XYStageOffset.spec이 그 값을 읽는다.
REM  설정을 바꾸려면 tools\build_config.py를 수정할 것.
REM ====================================================

set PROJECT_DIR=%~dp0
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo.
echo ============================================
echo   XY Stage Offset Analysis - PyInstaller Build
echo ============================================
echo.

python -m PyInstaller ^
    --noconfirm ^
    --distpath dist ^
    --workpath build ^
    "%PROJECT_DIR%XYStageOffset.spec"

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

REM 이전 버전이 생성한 외부 설정 파일 제거 — 배포 결과는 EXE 하나만 유지
if exist "%PROJECT_DIR%dist\settings.json" del /q "%PROJECT_DIR%dist\settings.json"

REM 번들 리소스가 기대 경로로 들어갔는지 검사한다.
REM add-data 대상이 틀려도 PyInstaller는 성공으로 끝나므로, 여기서 잡지 않으면
REM 실행 중에 리소스를 못 찾는 EXE가 그대로 배포된다.
echo.
echo --- Verifying bundled resources ---
python "%PROJECT_DIR%tools\verify_build.py"
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] 번들 리소스 검증 실패 - 배포하지 마세요.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build Complete!
echo ============================================
echo   EXE: dist\XYStageOffset.exe
echo.
pause
