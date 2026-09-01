[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$SkipDocker
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$failed = $false

function Check([string]$Label, [scriptblock]$Action) {
    try {
        & $Action
        Write-Host "[PASS] $Label" -ForegroundColor Green
    } catch {
        $script:failed = $true
        Write-Host "[FAIL] $Label - $($_.Exception.Message)" -ForegroundColor Red
    }
}

Check '存在本地 .env' {
    if (-not (Test-Path '.env')) { throw '请先运行 .\scripts\team_bootstrap.ps1' }
}

Check '存在运行目录' {
    foreach ($path in @('uploads/knowledge', 'uploads/workorders')) {
        if (-not (Test-Path $path)) { throw "缺少 $path" }
    }
}

if (-not $SkipDocker) {
    Check 'Docker Compose 可用' {
        & docker compose version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop 未启动或 Compose 不可用' }
    }
    Check 'P0 容器状态' {
        $output = & docker compose -f docker-compose.p0.yml ps --status running --services
        if ($LASTEXITCODE -ne 0 -or -not ($output -contains 'api')) { throw 'api 容器未运行' }
    }
    Check 'API 健康检查' {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8010/api/v1/health' -UseBasicParsing -TimeoutSec 8
        if ($response.StatusCode -ne 200) { throw "HTTP $($response.StatusCode)" }
    }
}

if (-not $SkipFrontend) {
    Check 'Node/npm 可用' {
        & node --version | Out-Null
        & npm --version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Node.js/npm 不可用' }
    }
    Check '前端锁定依赖存在' {
        if (-not (Test-Path 'frontend/package-lock.json')) { throw '缺少 frontend/package-lock.json' }
    }
}

if ($failed) {
    exit 1
}
Write-Host '团队环境检查通过。' -ForegroundColor Green
