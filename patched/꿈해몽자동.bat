@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 꿈해몽 자동 (글쓰기 + 임시저장)

echo ==========================================
echo   꿈해몽 자동 처리
echo ==========================================
echo.
echo  오늘작성 시트에 넣어 두신 꿈해몽 키워드로
echo  글을 만들고 티스토리에 임시저장까지 합니다.
echo.
echo  공개 발행은 하지 않습니다.
echo  티스토리에서 보시고 직접 발행하시면 됩니다.
echo.

echo [1/2] 글을 만드는 중입니다. 몇 분 걸립니다...
echo.
python blog_content_pipeline.py --category 꿈해몽
if errorlevel 1 goto 실패1
echo.
echo [1/2] 글 만들기 끝.
echo.

echo [2/2] 티스토리에 임시저장하는 중입니다...
echo       크롬 창이 뜨면 손대지 마세요. 알아서 진행합니다.
echo.
python blog_tistory_browser_draft.py --category 꿈해몽
if errorlevel 1 goto 실패2
echo.

echo ==========================================
echo   끝났습니다.
echo.
echo   티스토리 관리 - 글 관리 에서
echo   임시저장된 글을 확인하고 발행하시면 됩니다.
echo ==========================================
echo.
pause
exit /b 0

:실패1
echo.
echo ------------------------------------------
echo  글 만들기에서 멈췄습니다.
echo  위에 빨간 글자가 있으면 그대로 복사해서 물어보세요.
echo ------------------------------------------
pause
exit /b 1

:실패2
echo.
echo ------------------------------------------
echo  임시저장에서 멈췄습니다. 글은 이미 만들어졌습니다.
echo  이 파일을 다시 실행하면 임시저장부터 이어서 합니다.
echo.
echo  로그인이 풀렸을 수 있습니다. 크롬 창이 뜨면
echo  카카오 로그인을 직접 한 번 해 주세요.
echo ------------------------------------------
pause
exit /b 1
