[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$SkipBuild,
    [switch]$ResetData,
    [switch]$StartAml,
    [switch]$FullStack,
    [int]$HealthTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "缺少 $Name。$Hint"
    }
}

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

Require-Command 'docker' '请安装 Docker Desktop，并确认 docker 命令已加入 PATH。'
Require-Command 'npm' '请安装 Node.js LTS。'

Write-Step '检查 Docker Compose'
& docker compose version
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Compose 不可用，请确认 Docker Desktop 已启动。'
}

if (-not (Test-Path '.env')) {
    if (-not (Test-Path '.env.example')) {
        throw '找不到 .env.example，无法初始化本地配置。'
    }
    Copy-Item '.env.example' '.env'
    Write-Host '已从 .env.example 创建 .env；请按需填写 DASHSCOPE_API_KEY。' -ForegroundColor Yellow
} else {
    Write-Host '检测到已有 .env，不覆盖现有配置。' -ForegroundColor DarkGray
}

Write-Step '创建运行目录'
New-Item -ItemType Directory -Force -Path 'uploads/knowledge', 'uploads/workorders', 'logs' | Out-Null

if (-not $SkipFrontend) {
    Write-Step '安装前端锁定依赖'
    Push-Location 'frontend'
    try {
        if (-not (Test-Path 'package-lock.json')) {
            throw 'frontend/package-lock.json 不存在，不能执行可复现安装。'
        }
        & npm ci
        if ($LASTEXITCODE -ne 0) {
            throw 'npm ci 失败。'
        }
    } finally {
        Pop-Location
    }
}

$composeFile = if ($FullStack) { 'docker-compose.yml' } else { 'docker-compose.p0.yml' }
Write-Step "启动 $composeFile"
$composeArgs = @('-f', $composeFile, 'up', '-d')
if (-not $SkipBuild) { $composeArgs += '--build' }
& docker compose @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Compose 启动失败。'
}

if ($ResetData) {
    Write-Step '重置开发数据并重新导入演示数据'
    & docker compose -f $composeFile exec -T api python -m scripts.reset_dev_state
    if ($LASTEXITCODE -ne 0) {
        throw '开发数据重置失败。仅可在 development/dev/local 环境执行。'
    }
}

if ($StartAml -and -not $FullStack) {
    Write-Step '启动 AML Scheduler profile'
    & docker compose -f docker-compose.p0.yml --profile aml up -d aml-scheduler
    if ($LASTEXITCODE -ne 0) {
        throw 'AML Scheduler 启动失败。'
    }
}

Write-Step '等待 API 健康检查'
$healthUrl = 'http://127.0.0.1:8010/api/v1/health'
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 3
    }
}
if (-not $healthy) {
    Write-Host 'API 未在限定时间内通过健康检查，下面打印容器状态供排查。' -ForegroundColor Yellow
    & docker compose -f $composeFile ps
    throw "健康检查失败：$healthUrl"
}

Write-Host "`n脚手架初始化完成。" -ForegroundColor Green
Write-Host '前端：Set-Location frontend; npm run dev -- --host 127.0.0.1'
Write-Host 'API： http://127.0.0.1:8010/api/v1/health'
Write-Host '文档：docs/TEAM_ONBOARDING.md'
Write-Host '停止：docker compose -f docker-compose.p0.yml stop'
