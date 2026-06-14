#Requires -Version 3.0

Write-Host ""
Write-Host " ===================================="
Write-Host "   CryptoSplit - 서비스 제거"
Write-Host " ===================================="
Write-Host ""

# 실행 중인 작업 종료
Write-Host "[1/2] 봇 종료..."
schtasks /end /tn "CryptoSplit" 2>$null | Out-Null
Stop-Process -Name pythonw -Force -ErrorAction SilentlyContinue
Write-Host "       완료"

# 작업 스케줄러에서 제거
Write-Host ""
Write-Host "[2/2] 작업 스케줄러 제거..."
schtasks /delete /tn "CryptoSplit" /f
if ($LASTEXITCODE -ne 0) {
    Write-Host "[정보] 등록된 작업이 없거나 이미 제거되었습니다."
} else {
    Write-Host "       제거 완료"
}

Write-Host ""
Write-Host " ===================================="
Write-Host "   완료! 서비스가 제거되었습니다."
Write-Host "   (run_service.bat 은 수동으로 삭제하세요)"
Write-Host " ===================================="
Write-Host ""
Read-Host "계속하려면 Enter"
