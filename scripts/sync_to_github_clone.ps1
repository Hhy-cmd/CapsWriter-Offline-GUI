#Requires -Version 5.1
<#
.SYNOPSIS
  将「日常开发目录」中与 CPW-Pro 相关的叠加面同步到兄弟目录 CapsWriter-Offline-GitHub（小仓库，用于 push GitHub）。

.DESCRIPTION
  【默认】仅复制与 overlay ZIP 一致的目录与根文件（见 scripts\_cpw_overlay_manifest.ps1），另复制 .gitignore / LICENSE（若存在）。不会把整个 CapsWriter 引擎树（util、core_*、models 等）倒进小仓库。

  【-FullMirror】沿用旧逻辑：近乎整棵树 robocopy /E（仅排除虚拟环境、internal、权重扩展名等）——易导致小仓库混入整包，不推荐用于「瘦 GitHub 源」。

  不会在目标目录执行 git add/commit；同步后请自行在目标目录 git status。

.PARAMETER DryRun
  仅列出将执行的 robocopy（/L），不真正复制。

.PARAMETER DestName
  目标文件夹名（同级目录下），默认 CapsWriter-Offline-GitHub。

.PARAMETER FullMirror
  整树同步（旧行为）。默认关闭。

.EXAMPLE
  .\scripts\sync_to_github_clone.ps1
  .\scripts\sync_to_github_clone.ps1 -DryRun
  .\scripts\sync_to_github_clone.ps1 -FullMirror
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$DestName = "CapsWriter-Offline-GitHub",
    [switch]$FullMirror
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

function Invoke-RobocopyOk {
    param([string[]]$RoboArgs)
    $null = & robocopy @RoboArgs
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Write-Error ("robocopy failed exit {0}. Args: {1}" -f $code, ($RoboArgs -join ' '))
    }
}

Write-Host "源: $SourceRoot"
Write-Host "目标: $DestRoot"

if (-not $FullMirror) {
    Write-Host "模式: CPW-Pro 叠加面（与 overlay 清单一致）；不含 util/core_*/models 等整包。" -ForegroundColor Cyan
    Write-Host "若目标里留有以前整树同步的 util\models\…，脚本不会自动删除，请按需手工清理后再 git push。" -ForegroundColor DarkYellow

    . (Join-Path $PSScriptRoot "_cpw_overlay_manifest.ps1")

    foreach ($d in $CpwOverlayDirs) {
        $srcFull = Join-Path $SourceRoot $d.Src
        $dstFull = Join-Path $DestRoot $d.Dst
        if (-not (Test-Path -LiteralPath $srcFull)) {
            Write-Warning "跳过（源缺失）: $($d.Src)"
            continue
        }
        Write-Host "`n[MIRROR] $($d.Src) -> $($d.Dst)"
        $robo = @(
            $srcFull, $dstFull,
            "/MIR",
            "/NFL", "/NDL", "/NJH", "/NJS", "/nc", "/ns", "/np",
            "/XD", "__pycache__", ".pytest_cache", ".mypy_cache",
            "/XF", "*.pyc"
        )
        if ($DryRun) { $robo += "/L" }
        Invoke-RobocopyOk -RoboArgs $robo
    }

    foreach ($f in $CpwOverlayRootFiles) {
        $srcF = Join-Path $SourceRoot $f
        if (-not (Test-Path -LiteralPath $srcF)) { continue }
        Write-Host "FILE  $f"
        if (-not $DryRun) {
            Copy-Item -LiteralPath $srcF -Destination (Join-Path $DestRoot $f) -Force
        }
    }
    foreach ($f in $CpwGithubExtraRootFiles) {
        $srcF = Join-Path $SourceRoot $f
        if (-not (Test-Path -LiteralPath $srcF)) { continue }
        Write-Host "FILE  $f"
        if (-not $DryRun) {
            Copy-Item -LiteralPath $srcF -Destination (Join-Path $DestRoot $f) -Force
        }
    }
} else {
    Write-Host "模式: FullMirror（整树，旧行为）。不适合「瘦 GitHub」。" -ForegroundColor Yellow
    if ($DryRun) {
        Write-Host "DryRun robocopy /L" -ForegroundColor Cyan
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
    if ($p.ExitCode -ge 8) {
        Write-Error "robocopy 退出码 $($p.ExitCode)，请检查路径与权限。"
    }
}

Write-Host "`n完成。下一步在目标目录：" -ForegroundColor Green
Write-Host "  cd `"$DestRoot`""
Write-Host "  git status"
Write-Host '  git add -A && git commit -m "..." && git push'
Write-Host ""
Write-Host "在小仓库打包 CPW-Pro overlay ZIP：" -ForegroundColor Green
Write-Host "  .\scripts\make_release_overlay.ps1 -DryRun"
Write-Host "（或大仓库：`.\scripts\make_release_overlay.ps1 -RepoRoot '..\CapsWriter-Offline-GitHub'`）"
