@echo off
title CryptoSplit Service Install
powershell -ExecutionPolicy Bypass -NoProfile -Command "$env:_CRYPTO_ROOT='%~dp0'.TrimEnd('\'); & ([ScriptBlock]::Create([System.IO.File]::ReadAllText('%~dp0install_service.ps1',[Text.Encoding]::UTF8)))"
if %errorlevel% neq 0 pause
