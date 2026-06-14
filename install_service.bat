@echo off
chcp 65001 > nul
title CryptoSplit 서비스 설치

powershell -ExecutionPolicy Bypass -NoProfile -Command "$PSScriptRoot='%~dp0'.TrimEnd('\'); & ([ScriptBlock]::Create([System.IO.File]::ReadAllText('%~dp0install_service.ps1',[Text.Encoding]::UTF8)))"

if %errorlevel% neq 0 (
    echo.
    echo [오류] 설치 중 문제가 발생했습니다. 위 메시지를 확인하세요.
    pause
)
