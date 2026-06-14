@echo off
chcp 65001 > nul

powershell -ExecutionPolicy Bypass -NoProfile -Command "& ([ScriptBlock]::Create([System.IO.File]::ReadAllText('%~dp0deploy.ps1',[Text.Encoding]::UTF8)))"

if %errorlevel% neq 0 (
    echo.
    echo [오류] 배포 중 문제가 발생했습니다. 위 메시지를 확인하세요.
    pause
)
