#Requires -Version 3.0
$_root = if ($PSScriptRoot) { $PSScriptRoot } else { $env:_CRYPTO_ROOT }
Set-Location $_root

Write-Host ""
Write-Host " ===================================="
Write-Host "   CryptoSplit - 업데이트 적용"
Write-Host " ===================================="
Write-Host ""

# 서비스 등록 여부 감지
schtasks /query /tn "CryptoSplit" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    $useService = $true
    Write-Host "  [모드] 작업 스케줄러 서비스 모드"
} else {
    $useService = $false
    Write-Host "  [모드] 일반 실행 모드"
}
Write-Host ""

# [1/3] 봇 종료
Write-Host "[1/3] 실행 중인 봇 종료..."
if ($useService) {
    schtasks /end /tn "CryptoSplit" 2>$null | Out-Null
    Start-Sleep -Seconds 3
    Write-Host "       서비스 종료 완료"
} else {
    $pw = Get-Process pythonw -ErrorAction SilentlyContinue
    $py = Get-Process python  -ErrorAction SilentlyContinue
    if ($pw) {
        Stop-Process -Name pythonw -Force
        Write-Host "       pythonw.exe 종료 완료"
    } elseif ($py) {
        Stop-Process -Name python -Force
        Write-Host "       python.exe 종료 완료"
    } else {
        Write-Host "       실행 중인 봇 없음 (건너뜀)"
    }
}

# [2/3] 최신 코드 받기
Write-Host ""
Write-Host "[2/3] 최신 코드 받기..."
git pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] git pull 실패. 네트워크 또는 충돌을 확인하세요."
    Read-Host "`n계속하려면 Enter"
    exit 1
}

# [3/3] 봇 재시작
Write-Host ""
Write-Host "[3/3] 봇 재시작..."
if ($useService) {
    schtasks /run /tn "CryptoSplit"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[오류] 서비스 시작 실패."
        Read-Host "`n계속하려면 Enter"
        exit 1
    }
    Write-Host "       서비스로 시작됨 (백그라운드, CMD 창 없음)"
} else {
    $pythonwCmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($pythonwCmd -and $pythonwCmd.Source) {
        $pythonwPath = $pythonwCmd.Source
    } else {
        $pyCmd = Get-Command python.exe -ErrorAction SilentlyContinue
        $candidate = if ($pyCmd) { Join-Path (Split-Path $pyCmd.Source) "pythonw.exe" } else { $null }
        $pythonwPath = if ($candidate -and (Test-Path $candidate)) { $candidate } else { "pythonw" }
    }
    Start-Process $pythonwPath -ArgumentList "main.py" -WorkingDirectory $_root
    Write-Host "       백그라운드로 시작됨"
    Write-Host "       (서비스 등록은 install_service.bat 으로 가능)"
}

Write-Host ""
Write-Host " ===================================="
Write-Host "   완료! 봇이 재시작되었습니다."
Write-Host "   대시보드: http://localhost:5000"
Write-Host " ===================================="
Write-Host ""
Read-Host "계속하려면 Enter"
