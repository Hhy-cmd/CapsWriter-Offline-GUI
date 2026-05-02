#Requires -Version 5.1
<#
.SYNOPSIS
  打成「CPW-Pro 叠加包」ZIP：合并到官方 CapsWriter 解压根目录即可（不含 internal / exe / 模型）。

.DESCRIPTION
  版本号默认从 cpwpro\_version.py 的 __version__ 读取。
  使用 robocopy 复制目录并跳过 __pycache__ 等，再 Compress-Archive。

.PARAMETER OutDir
  ZIP 输出目录，默认：<仓库>\dist

.PARAMETER Version
  覆盖版本号（默认读 cpwpro\_version.py）

.PARAMETER DryRun
  仅打印将要打包的路径与输出文件名，不创建 ZIP。

.PARAMETER RepoRoot
  可选。显式指定「要打包的仓库根目录」（例如小仓库 CapsWriter-Offline-GitHub）。
  省略时默认为：本脚本所在目录的上一级（即 …\scripts\..）。

.EXAMPLE
  # 在小仓库根目录执行（推荐）
  cd D:\...\CapsWriter-Offline-GitHub
  .\scripts\make_release_overlay.ps1
  .\scripts\make_release_overlay.ps1 -OutDir D:\releases -Version 1.0.1

.EXAMPLE
  # 从大仓库指向小仓库根目录
  .\scripts\make_release_overlay.ps1 -RepoRoot ..\CapsWriter-Offline-GitHub
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$OutDir = "",
    [string]$Version = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ($RepoRoot) {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path.TrimEnd("\")
} else {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd("\")
}

$cpwVersionFile = Join-Path $RepoRoot $(Join-Path "cpwpro" "_version.py")
if (-not (Test-Path -LiteralPath $cpwVersionFile)) {
    $scriptPath = if ($MyInvocation.MyCommand.Path) { $MyInvocation.MyCommand.Path } else { '(unknown)' }
    $nl = [Environment]::NewLine
    $hint = @(
        "Not a CPW-Pro repo root (missing cpwpro/_version.py): $RepoRoot",
        'If "script not recognized": run from repo root, sync from dev first:',
        '  .\\scripts\\sync_to_github_clone.ps1',
        'Or pack the GitHub clone path from the dev repo:',
        '  .\\scripts\\make_release_overlay.ps1 -RepoRoot ..\\CapsWriter-Offline-GitHub',
        'Or: powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\make_release_overlay.ps1',
        "This script file: $scriptPath"
    ) -join $nl
    Write-Error $hint
}

function Assert-CpwOverlayVersionFormat {
    param([string]$v)
    if ([string]::IsNullOrWhiteSpace($v)) {
        Write-Error '-Version empty.'
    }
    if ($v.Length -gt 48) {
        Write-Error '-Version too long; you may have pasted a path into -Version by mistake.'
    }
    if ($v -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]*$') {
        $nl = [Environment]::NewLine
        Write-Error ( @(
                "Invalid -Version: '$v' (do NOT paste a path on the same line as -Version).",
                "Good example:",
                "  .\scripts\make_release_overlay.ps1 -Version '1.0.1' -OutDir 'D:\releases'"
            ) -join $nl )
    }
}

function Get-CpwVersion {
    param([string]$Root)
    $vf = Join-Path $Root $(Join-Path "cpwpro" "_version.py")
    if (-not (Test-Path -LiteralPath $vf)) {
        Write-Error "Missing _version.py: $vf"
    }
    $dq = [char]34
    $tab = [char]9
    # Build regex without `cpwpro\_*` double-quoted path fragments (\v escapes) or tricky here-string indentation.
    $verPat = '^[ ' + $tab + ']*__version__[ ' + $tab + ']*=[ ' + $tab + ']*' + $dq + '([^' + $dq + ']+)' + $dq
    $line = Select-String -LiteralPath $vf -Pattern $verPat | Select-Object -First 1
    if (-not $line) { Write-Error "Could not parse __version__ double-quoted string in _version.py" }
    return $line.Matches[0].Groups[1].Value
}

function Invoke-RobocopyOk {
    param([string[]]$RoboArgs)
    $null = & robocopy @RoboArgs
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Write-Error ("robocopy failed exit {0}. Args: {1}" -f $code, ($RoboArgs -join ' '))
    }
}

if (-not $Version) {
    $Version = Get-CpwVersion -Root $RepoRoot
}
Assert-CpwOverlayVersionFormat -v $Version

if (-not $OutDir) {
    $OutDir = Join-Path $RepoRoot "dist"
}

$zipName = "CPW-Pro-overlay-$Version.zip"
$zipPath = Join-Path $OutDir $zipName

# Safe temp folder slug (hyphen-last char class avoids PowerShell `\v`/quote parse issues).
$hyphenSlugPat = '[a-zA-Z0-9._-]'
$safeStageTag = ''
foreach ($ch in $Version.ToCharArray()) {
    if ([regex]::IsMatch("$ch", $hyphenSlugPat)) {
        $safeStageTag += $ch
    } else {
        $safeStageTag += '_'
    }
}
while ($safeStageTag.Length -gt 0 -and @('.', '_', '-') -contains $safeStageTag[0]) {
    $safeStageTag = $safeStageTag.Substring(1)
}
while ($safeStageTag.Length -gt 0 -and @('.', '_', '-') -contains $safeStageTag[$safeStageTag.Length - 1]) {
    $safeStageTag = $safeStageTag.Substring(0, $safeStageTag.Length - 1)
}
if (-not $safeStageTag) { $safeStageTag = "ver" }

$stagingName = "cpwpro-overlay-stage-$safeStageTag-" + ([guid]::NewGuid().ToString("n").Substring(0, 8))
$staging = Join-Path ([System.IO.Path]::GetTempPath()) $stagingName

. (Join-Path $PSScriptRoot "_cpw_overlay_manifest.ps1")
$dirsToMirror = $CpwOverlayDirs
$rootFiles = $CpwOverlayRootFiles

Write-Host "Repo root: $RepoRoot"
Write-Host "CPW-Pro version label: $Version"
Write-Host "Output zip: $zipPath"

if ($DryRun) {
    Write-Host "`n[DryRun] Mirror dirs:"
    foreach ($d in $dirsToMirror) {
        $sp = Join-Path $RepoRoot $d.Src
        if (Test-Path -LiteralPath $sp) {
            Write-Host "  $($d.Src) -> $($d.Dst)"
        } else {
            Write-Host "  (skip missing) $($d.Src)"
        }
    }
    Write-Host "`n[DryRun] Root files (if present):"
    foreach ($f in $rootFiles) {
        $fp = Join-Path $RepoRoot $f
        if (Test-Path -LiteralPath $fp) { Write-Host "  $f" }
    }
    Write-Host "`n[DryRun] Done (no zip created)."
    return
}

if (-not (Test-Path -LiteralPath $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
}

if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Path $staging | Out-Null

foreach ($d in $dirsToMirror) {
    $srcFull = Join-Path $RepoRoot $d.Src
    $dstFull = Join-Path $staging $d.Dst
    if (-not (Test-Path -LiteralPath $srcFull)) {
        Write-Warning "Skip missing folder: $($d.Src)"
        continue
    }
    New-Item -ItemType Directory -Path (Split-Path $dstFull -Parent) -Force | Out-Null
    # SCRIPTS_RUNBOOK.zh.md: dev-only runbook; keep in git, omit from end-user overlay zip.
    Invoke-RobocopyOk -RoboArgs @(
        $srcFull, $dstFull,
        "/MIR",
        "/NFL", "/NDL", "/NJH", "/NJS", "/nc", "/ns", "/np",
        "/XD", "__pycache__", ".pytest_cache", ".mypy_cache",
        "/XF", "*.pyc", "SCRIPTS_RUNBOOK.zh.md"
    )
}

foreach ($f in $rootFiles) {
    $srcF = Join-Path $RepoRoot $f
    if (-not (Test-Path -LiteralPath $srcF)) { continue }
    Copy-Item -LiteralPath $srcF -Destination (Join-Path $staging $f) -Force
}

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force

Remove-Item -LiteralPath $staging -Recurse -Force

$len = (Get-Item -LiteralPath $zipPath).Length
$mb = [math]::Round($len / 1MB, 2)
Write-Host "`nDone: $zipPath"
Write-Host "Size: $len bytes (~$mb MB)"
