@echo off
chcp 65001 > nul
powershell -ExecutionPolicy Bypass -NoProfile -Command "$PSScriptRoot='%~dp0'.TrimEnd('\'); & ([ScriptBlock]::Create([System.IO.File]::ReadAllText('%~dp0install_service.ps1',[Text.Encoding]::UTF8)))"
