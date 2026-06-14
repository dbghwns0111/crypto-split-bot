@echo off
chcp 65001 > nul
title CryptoSplit Update

echo.
echo  ====================================
echo   CryptoSplit ^— 업데이트 적용
echo  ====================================
echo.

:: 실행 중인 봇 프로세스 확인
echo [1/3] 실행 중인 봇 종료...
tasklist /fi "imagename eq python.exe" /fo csv 2>nul | find "python.exe" > nul
if %errorlevel%==0 (
    taskkill /f /im python.exe > nul 2>&1
    echo        봇 프로세스를 종료했습니다.
) else (
    echo        실행 중인 봇 없음 ^(건너뜀^)
)

echo.
echo [2/3] 최신 코드 받기...
git pull
if %errorlevel% neq 0 (
    echo [오류] git pull 실패. 네트워크 또는 충돌을 확인하세요.
    pause
    exit /b 1
)

echo.
echo [3/3] 봇 재시작...
start "CryptoSplit Bot" cmd /k "python main.py"

echo.
echo  ====================================
echo   완료! 봇이 새 창에서 시작됩니다.
echo   대시보드: http://localhost:5000
echo  ====================================
echo.
pause
