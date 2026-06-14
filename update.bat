@echo off
powershell -ExecutionPolicy Bypass -NoProfile -Command "$PSScriptRoot='%~dp0'.TrimEnd('\'); & ([ScriptBlock]::Create([System.IO.File]::ReadAllText('%~dp0update.ps1',[Text.Encoding]::UTF8)))"
if %errorlevel% neq 0 pause
