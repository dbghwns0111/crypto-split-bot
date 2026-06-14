#Requires -Version 3.0

Write-Host ""
Write-Host " ===================================="
Write-Host "   CryptoSplit - 서비스 설치"
Write-Host " ===================================="
Write-Host ""

# ── 관리자 권한 확인 ──────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "[오류] 관리자 권한이 필요합니다."
    Write-Host "       install_service.bat 을 우클릭 -> 관리자로 실행 하세요."
    Read-Host "`n계속하려면 Enter"
    exit 1
}

$ProjectDir = if ($PSScriptRoot) { $PSScriptRoot } else { $env:_CRYPTO_ROOT }

# ── Python 실행파일 경로 자동 감지 ───────────────────────
# 관리자 CMD는 conda PATH가 없으므로 디스크에서 직접 탐색
$pythonw = $null

# 1) PATH에서 탐색 (일반 CMD 실행 시)
foreach ($exe in @("pythonw.exe", "python.exe")) {
    $c = Get-Command $exe -ErrorAction SilentlyContinue
    if ($c -and $c.Source -and (Test-Path $c.Source)) {
        $src = $c.Source
        # Windows Store 더미 제외
        if ($src -notlike "*WindowsApps*") {
            $pythonw = $src; break
        }
    }
}

# python.exe 옆에 pythonw.exe 있으면 우선 사용
if ($pythonw -and $pythonw -like "*python.exe") {
    $pw = Join-Path (Split-Path $pythonw) "pythonw.exe"
    if (Test-Path $pw) { $pythonw = $pw }
}

# 2) conda/Python 일반 설치 경로 직접 탐색 (관리자 CMD 대응)
if (-not $pythonw) {
    $roots = @(
        "$env:USERPROFILE\miniconda3",
        "$env:USERPROFILE\miniconda",
        "$env:USERPROFILE\anaconda3",
        "$env:USERPROFILE\anaconda",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python*",
        "C:\ProgramData\miniconda3",
        "C:\ProgramData\anaconda3",
        "C:\Python*",
    )
    foreach ($root in $roots) {
        foreach ($expanded in (Resolve-Path $root -ErrorAction SilentlyContinue)) {
            foreach ($exe in @("pythonw.exe", "python.exe")) {
                $candidate = Join-Path $expanded.Path $exe
                if (Test-Path $candidate) { $pythonw = $candidate; break }
            }
            if ($pythonw) { break }
        }
        if ($pythonw) { break }
    }
}

# 3) 자동 탐색 실패 — 수동 입력
if (-not $pythonw) {
    Write-Host "[경고] Python 을 자동으로 찾지 못했습니다."
    Write-Host "       (예: C:\Users\awesome\miniconda3\python.exe)"
    $manualPath = Read-Host "   python.exe 전체 경로를 직접 입력하세요"
    $manualPath = $manualPath.Trim('"').Trim("'")
    if (-not $manualPath -or -not (Test-Path $manualPath)) {
        Write-Host "[오류] 경로가 없거나 유효하지 않습니다."
        Read-Host "`n계속하려면 Enter"
        exit 1
    }
    $pythonw = $manualPath
}

Write-Host "   프로젝트: $ProjectDir"
Write-Host "   Python:   $pythonw"
Write-Host ""

# ── logs 폴더 생성 ────────────────────────────────────────
New-Item -ItemType Directory -Force -Path "$ProjectDir\logs" | Out-Null

# ── run_service.bat 생성 (경로 고정 런처) ─────────────────
Write-Host "[1/4] 런처 스크립트 생성..."
$runScript = "@echo off`r`ncd /d `"$ProjectDir`"`r`n`"$pythonw`" main.py"
$runScript | Out-File -FilePath "$ProjectDir\run_service.bat" -Encoding ascii
Write-Host "       run_service.bat 생성 완료"

# ── 기존 작업 제거 ────────────────────────────────────────
Write-Host ""
Write-Host "[2/4] 기존 작업 확인..."
schtasks /query /tn "CryptoSplit" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    schtasks /end /tn "CryptoSplit" 2>$null | Out-Null
    schtasks /delete /tn "CryptoSplit" /f 2>$null | Out-Null
    Write-Host "       기존 작업 제거 완료"
} else {
    Write-Host "       기존 작업 없음 (건너뜀)"
}

# ── 작업 스케줄러 XML 등록 ────────────────────────────────
Write-Host ""
Write-Host "[3/4] 작업 스케줄러 등록..."

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>CryptoSplit 자동매매 봇</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT10S</Delay>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal>
      <UserId>SYSTEM</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions>
    <Exec>
      <Command>$ProjectDir\run_service.bat</Command>
      <WorkingDirectory>$ProjectDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$xmlPath = [System.IO.Path]::GetTempFileName() + ".xml"
$taskXml | Out-File -FilePath $xmlPath -Encoding unicode

schtasks /create /tn "CryptoSplit" /xml $xmlPath /f
$createResult = $LASTEXITCODE
Remove-Item $xmlPath -Force

if ($createResult -ne 0) {
    Write-Host "[오류] 작업 등록 실패."
    Read-Host "`n계속하려면 Enter"
    exit 1
}
Write-Host "       등록 완료 (부팅 시 자동 시작 + 비정상 종료 시 1분 후 재시작)"

# ── 즉시 시작 ─────────────────────────────────────────────
Write-Host ""
Write-Host "[4/4] 봇 시작..."
schtasks /run /tn "CryptoSplit"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] 봇 시작 실패."
    Read-Host "`n계속하려면 Enter"
    exit 1
}

Write-Host ""
Write-Host " ===================================="
Write-Host "   설치 완료!"
Write-Host ""
Write-Host "   - CMD 창 닫아도 봇은 계속 실행됩니다"
Write-Host "   - PC 재부팅 시 10초 후 자동 시작됩니다"
Write-Host "   - 비정상 종료 시 1분 후 자동 재시작됩니다"
Write-Host "   - 로그: logs\bot.log"
Write-Host "   - 대시보드: http://localhost:5000"
Write-Host " ===================================="
Write-Host ""
Read-Host "계속하려면 Enter"
