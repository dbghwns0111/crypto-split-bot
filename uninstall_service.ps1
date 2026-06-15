#Requires -Version 3.0

Write-Output ""
Write-Output " ===================================="
Write-Output "   CryptoSplit - 서비스 제거"
Write-Output " ===================================="
Write-Output ""

# 실행 중인 작업 종료
Write-Output "[1/2] 봇 종료..."
schtasks /end /tn "CryptoSplit" 2>$null | Out-Null

# pythonw.exe 종료 (Task Scheduler 등록 환경)
Stop-Process -Name pythonw -Force -ErrorAction SilentlyContinue

# python.exe 중 main.py 실행 중인 프로세스 종료 (conda 환경)
$botProcs = Get-WmiObject Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -like '*main.py*' }
if ($botProcs) {
    $botProcs | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "       완료"

# 작업 스케줄러에서 제거
Write-Output ""
Write-Output "[2/2] 작업 스케줄러 제거..."
schtasks /delete /tn "CryptoSplit" /f
if ($LASTEXITCODE -ne 0) {
    Write-Output "[정보] 등록된 작업이 없거나 이미 제거되었습니다."
} else {
    Write-Output "       제거 완료"
}

Write-Output ""
Write-Output " ===================================="
Write-Output "   완료! 서비스가 제거되었습니다."
Write-Output "   (run_service.bat 은 수동으로 삭제하세요)"
Write-Output " ===================================="
Write-Output ""
Read-Host "계속하려면 Enter"
