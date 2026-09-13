@echo off
chcp 65001 >nul
title 유튜브 구간 추출기 - 업데이트
cd /d "%~dp0"

rem 내려받을 브랜치. 다른 버전을 쓰려면 이 줄만 바꾸면 된다.
set "BRANCH=claude/youtube-video-time-extraction-t9nm8a"
set "ZIPURL=https://codeload.github.com/piaf0piaf0-code/auto-blog/zip/refs/heads/%BRANCH%"
set "TMPDIR=%TEMP%\shorts-update"

echo ============================================
echo   최신 버전으로 업데이트합니다
echo ============================================
echo.
echo 내려받는 중... (브라우저를 거치지 않습니다)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; if (Test-Path '%TMPDIR%') { Remove-Item '%TMPDIR%' -Recurse -Force }; New-Item -ItemType Directory -Path '%TMPDIR%' | Out-Null; Invoke-WebRequest -Uri '%ZIPURL%' -OutFile '%TMPDIR%\src.zip' -UseBasicParsing; Expand-Archive -Path '%TMPDIR%\src.zip' -DestinationPath '%TMPDIR%' -Force"

if errorlevel 1 (
    echo.
    echo [오류] 내려받기에 실패했습니다.
    echo   인터넷 연결을 확인하고 다시 시도해보세요.
    echo   회사망이나 백신이 막는 경우도 있습니다.
    pause
    exit /b 1
)

set "SRCDIR="
for /d %%D in ("%TMPDIR%\auto-blog-*") do set "SRCDIR=%%D"
if not defined SRCDIR (
    echo [오류] 압축은 받았는데 내용을 찾지 못했습니다.
    pause
    exit /b 1
)

echo 파일을 바꾸는 중...
rem 프로그램 파일만 교체한다. 만든 영상(outputs)과 설정(.env)은 건드리지 않는다.
xcopy "%SRCDIR%\src" "src\" /E /I /Y >nul
xcopy "%SRCDIR%\docs" "docs\" /E /I /Y >nul
xcopy "%SRCDIR%\tests" "tests\" /E /I /Y >nul
copy /Y "%SRCDIR%\requirements-ui.txt" . >nul
copy /Y "%SRCDIR%\requirements.txt" . >nul
copy /Y "%SRCDIR%\README.md" . >nul
copy /Y "%SRCDIR%\실행.bat" . >nul

rem 이 파일(업데이트.bat)은 실행 중이라 스스로 덮어쓰지 않는다.
fc /b "%SRCDIR%\업데이트.bat" "업데이트.bat" >nul 2>nul
if errorlevel 1 (
    copy /Y "%SRCDIR%\업데이트.bat" "업데이트-새버전.bat" >nul
    echo.
    echo [알림] 업데이트 파일 자체가 바뀌었습니다.
    echo        다음부터는 '업데이트-새버전.bat' 을 쓰세요.
)

rmdir /s /q "%TMPDIR%" >nul 2>nul

echo.
echo ✅ 업데이트 끝났습니다.
echo.
echo 프로그램을 실행합니다...
timeout /t 2 >nul
call "실행.bat"
