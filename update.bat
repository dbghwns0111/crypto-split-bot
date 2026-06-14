@echo off
chcp 65001 > nul
title CryptoSplit Update
cd /d "%~dp0"

echo.
echo  ====================================
echo   CryptoSplit ^— 업데이트 적용
echo  ====================================
echo.

:: 서비스 등록 여부 감지
schtasks /query /tn "CryptoSplit" > nul 2>&1
if %errorlevel%==0 (
    set USE_SERVICE=1
    echo   [모드] 작업 스케줄러 서비스 모드
) else (
    set USE_SERVICE=0
    echo   [모드] 일반 실행 모드
)
echo.

:: ── [1/3] 봇 종료 ──────────────────────────────────────────
echo [1/3] 실행 중인 봇 종료...
if "%USE_SERVICE%"=="1" (
    schtasks /end /tn "CryptoSplit" > nul 2>&1
    timeout /t 3 /nobreak > nul
    echo        서비스 종료 완료
) else (
    tasklist /fi "imagename eq pythonw.exe" /fo csv 2>nul | find "pythonw.exe" > nul
    if %errorlevel%==0 (
        taskkill /f /im pythonw.exe > nul 2>&1
        echo        pythonw.exe 종료 완료
    ) else (
        tasklist /fi "imagename eq python.exe" /fo csv 2>nul | find "python.exe" > nul
        if %errorlevel%==0 (
            taskkill /f /im python.exe > nul 2>&1
            echo        python.exe 종료 완료
        ) else (
            echo        실행 중인 봇 없음 ^(건너뜀^)
        )
    )
)

:: ── [2/3] 최신 코드 받기 ───────────────────────────────────
echo.
echo [2/3] 최신 코드 받기...
git pull
if %errorlevel% neq 0 (
    echo [오류] git pull 실패. 네트워크 또는 충돌을 확인하세요.
    pause
    exit /b 1
)

:: ── [3/3] 봇 재시작 ────────────────────────────────────────
echo.
echo [3/3] 봇 재시작...
if "%USE_SERVICE%"=="1" (
    schtasks /run /tn "CryptoSplit"
    if %errorlevel% neq 0 (
        echo [오류] 서비스 시작 실패.
        pause
        exit /b 1
    )
    echo        서비스로 시작됨 ^(백그라운드, CMD 창 없음^)
) else (
    start "" pythonw main.py
    echo        백그라운드로 시작됨
    echo        ^(서비스 등록은 install_service.bat 으로 가능^)
)

echo.
echo  ====================================
echo   완료! 봇이 재시작되었습니다.
echo   대시보드: http://localhost:5000
echo  ====================================
echo.
pause
