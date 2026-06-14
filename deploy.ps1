#Requires -Version 3.0
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host " ===================================="
Write-Host "   CryptoSplit - GitHub 배포 (AI)"
Write-Host " ===================================="
Write-Host ""

# ── 1단계: 스테이징 ──────────────────────────────────────
Write-Host "[1/4] 변경 파일 스테이징..."
git add -A

$staged = (git diff --cached --stat) -join "`n"
if (-not $staged.Trim()) {
    Write-Host "       커밋할 변경사항이 없습니다."
    Read-Host "`n계속하려면 Enter"
    exit 0
}
Write-Host $staged

# ── 2단계: AI 커밋 메시지 생성 ───────────────────────────
Write-Host ""
Write-Host "[2/4] Claude AI 커밋 메시지 생성 중..."

$diff = (git diff --cached --stat) -join "`n"
$prompt = "다음은 git 변경 파일 목록이야. 변경 내용을 유추해서 한국어로 간결한 커밋 메시지를 딱 한 줄만 작성해줘. 메시지 텍스트만 출력하고 따옴표·접두사·부연설명은 쓰지 마.`n`n$diff"

$msg = ($prompt | claude -p 2>$null) -join "" | ForEach-Object { $_.Trim() }

if (-not $msg) {
    Write-Host "       AI 생성 실패 — 직접 입력하세요."
    $msg = Read-Host "커밋 메시지"
    if (-not $msg) {
        Write-Host "[오류] 커밋 메시지를 입력하세요."
        exit 1
    }
}

Write-Host "       메시지: $msg"

# ── 3단계: 커밋 ──────────────────────────────────────────
Write-Host ""
Write-Host "[3/4] 커밋..."
git commit -m $msg
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] 커밋 실패."
    Read-Host "`n계속하려면 Enter"
    exit 1
}

# ── 4단계: 푸시 ──────────────────────────────────────────
Write-Host ""
Write-Host "[4/4] GitHub 푸시..."
git push
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] 푸시 실패. 네트워크 또는 인증을 확인하세요."
    Read-Host "`n계속하려면 Enter"
    exit 1
}

Write-Host ""
Write-Host " ===================================="
Write-Host "   완료! GitHub에 업로드되었습니다."
Write-Host "   클라우드 PC에서 update.bat 실행"
Write-Host " ===================================="
Write-Host ""
Read-Host "계속하려면 Enter"
