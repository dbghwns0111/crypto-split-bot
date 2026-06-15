@echo off
net session >nul 2>&1
if not %errorlevel% == 0 (
    powershell -NoProfile -Command "Start-Process -FilePath \"%~f0\" -Verb RunAs -Wait"
    exit /b 0
)
title CryptoSplit Service Uninstall
powershell -ExecutionPolicy Bypass -NoProfile -Command "$env:_CRYPTO_ROOT='%~dp0'.TrimEnd('\'); & ([ScriptBlock]::Create([System.IO.File]::ReadAllText('%~dp0uninstall_service.ps1',[Text.Encoding]::UTF8)))"
if %errorlevel% neq 0 pause
