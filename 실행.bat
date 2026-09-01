@echo off
chcp 65001 >nul
title 유튜브 구간 추출기
cd /d "%~dp0"

echo ============================================
echo   유튜브 구간 추출기
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [설치 필요] 파이썬이 설치되어 있지 않습니다.
    echo.
    echo   1. https://www.python.org/downloads/ 에서 파이썬을 내려받으세요.
    echo   2. 설치 화면 맨 아래 "Add python.exe to PATH" 를 반드시 체크하세요.
    echo   3. 설치가 끝나면 이 파일을 다시 더블클릭하세요.
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo 최초 실행 준비 중입니다. 1~3분 정도 걸립니다. 잠시만 기다려주세요...
    python -m venv .venv
    if errorlevel 1 (
        echo [오류] 준비에 실패했습니다.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

python -m pip install --upgrade pip >nul 2>nul
pip install -q -r requirements-ui.txt
if errorlevel 1 (
    echo [오류] 필요한 프로그램 설치에 실패했습니다. 인터넷 연결을 확인하세요.
    pause
    exit /b 1
)

echo 유튜브 다운로더를 최신 버전으로 맞추는 중...
pip install -q -U yt-dlp >nul 2>nul

echo.
echo 브라우저가 열립니다. 창을 닫으면 프로그램이 종료됩니다.
echo (이 검은 창은 켜 둔 채로 사용하세요)
echo.
python -m src.shorts_ui

pause
