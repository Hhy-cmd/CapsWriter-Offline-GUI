# Dot-source only. Shared by sync_to_github_clone.ps1 and make_release_overlay.ps1.
# "小仓库" / overlay ZIP 所应包含的树（不含上游 util、core_*、models 等）。
$CpwOverlayDirs = @(
    @{ Src = "cpwpro";   Dst = "cpwpro" },
    @{ Src = "launcher"; Dst = "launcher" },
    @{ Src = "docs";     Dst = "docs" },
    @{ Src = "scripts";  Dst = "scripts" },
    @{ Src = "assets";    Dst = "assets" }
)
$CpwOverlayRootFiles = @(
    "requirements.txt",
    "config.example.json",
    "readme.md",
    "README.md",
    "GITHUB_CLONE_SETUP.md",
    "PROJECT_DOCUMENTATION.md",
    "cpw_pro_ui.py",
    "cpw_worker.py",
    "cpw_theme.py",
    "cpw_textutil.py",
    "cpw_progress.py",
    "cw_transcribe.py"
)
# 仅同步到 Git 小仓库时额外带上（不参与 overlay ZIP）；LICENSE 可有可无
$CpwGithubExtraRootFiles = @(
    ".gitignore",
    ".gitattributes",
    "LICENSE",
    "LICENSE.md"
)
