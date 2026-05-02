#Requires -Version 5.1
<#
.SYNOPSIS
  将「日常开发目录」中的源码同步到兄弟目录 CapsWriter-Offline-GitHub（小仓库，用于 push GitHub）。

.DESCRIPTION
  使用 robocopy 复制文件，跳过 .git / 虚拟环境 / 大模型 / internal 等。
  不会在目标目录执行 git add/commit；同步后请自行在 -GitHub 目录下 git status -> add -> commit -> push。

.PARAMETER DryRun
  仅列出将执行的 robocopy（/L），不真正复制。

.PARAMETER DestName
  目标文件夹名（同级目录下），默认 CapsWriter-Offline-GitHub。

.EXAMPLE
  .\scripts\sync_to_github_clone.ps1
  .\scripts\sync_to_github_clone.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$DestName = "CapsWriter-Offline-GitHub"
)

$ErrorActionPreference = "Stop"
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Parent = Split-Path $SourceRoot -Parent
$DestRoot = Join-Path $Parent $DestName

if (-not (Test-Path $DestRoot)) {
    Write-Error "找不到目标目录：$DestRoot`n请先创建并在此目录 git init，或检查 -DestName。"
}

$SourceRoot = $SourceRoot.TrimEnd("\")
$DestRoot = $DestRoot.TrimEnd("\")

if ($SourceRoot -ieq $DestRoot) {
    Write-Error "源与目标相同，已中止。"
}

Write-Host "源: $SourceRoot"
Write-Host "目标: $DestRoot"
if ($DryRun) {
    Write-Host "模式: 预演 (robocopy /L)" -ForegroundColor Cyan
}

$roboArgs = @(
    $SourceRoot, $DestRoot,
    "/E",
    "/XD", ".git", "myenv", ".venv", "venv", "output", "logs", "internal", "__pycache__", ".pytest_cache", ".mypy_cache", ".claude", ".cursor",
    "/XF", "*.gguf", "*.onnx",
    "/R:2", "/W:2", "/MT:8", "/NP", "/NDL", "/NFL"
)
if ($DryRun) {
    $roboArgs += "/L"
}

$p = Start-Process -FilePath "robocopy" -ArgumentList $roboArgs -Wait -PassThru -NoNewWindow
# robocopy 0-7 常表示有文件被复制；8+ 为错误 — 但 1/2/3 也是“成功有操作”
if ($p.ExitCode -ge 8) {
    Write-Error "robocopy 退出码 $($p.ExitCode)，请检查路径与权限。"
}

Write-Host "`n完成。下一步在目标目录：" -ForegroundColor Green
Write-Host "  cd `"$DestRoot`""
Write-Host "  git status"
Write-Host "  git add -A && git commit -m `"...`" && git push"
