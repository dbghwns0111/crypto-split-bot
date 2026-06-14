@echo off
chcp 65001 > nul
title CryptoSplit 서비스 제거

echo.
echo  ====================================
echo   CryptoSplit ^— 서비스 제거
echo  ====================================
echo.

:: 실행 중인 작업 종료
echo [1/2] 봇 종료...
schtasks /end /tn "CryptoSplit" > nul 2>&1
taskkill /f /im pythonw.exe > nul 2>&1
echo        완료

:: 작업 스케줄러에서 제거
echo.
echo [2/2] 작업 스케줄러 제거...
schtasks /delete /tn "CryptoSplit" /f
if %errorlevel% neq 0 (
    echo [정보] 등록된 작업이 없거나 이미 제거되었습니다.
) else (
    echo        제거 완료
)

echo.
echo  ====================================
echo   완료! 서비스가 제거되었습니다.
echo   (run_service.bat 은 수동으로 삭제하세요)
echo  ====================================
echo.
pause
