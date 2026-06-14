@echo off
powershell -ExecutionPolicy Bypass -NoProfile -Command "& ([ScriptBlock]::Create([System.IO.File]::ReadAllText('%~dp0deploy.ps1',[Text.Encoding]::UTF8)))"
if %errorlevel% neq 0 pause
