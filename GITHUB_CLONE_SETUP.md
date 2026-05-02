# 从 GitHub 拉取后如何跑起来（CPW-Pro + CapsWriter）

与根目录 **`readme.md`**（含 **CPW-Pro 专章**：目的、痛点、功能表；另有 **原版 CapsWriter README 附录**）、**`docs/CPW_PRO_OVERVIEW.zh.md`**（产品说明书）、**`PROJECT_DOCUMENTATION.md`**（架构）、**`docs/RELEASE_GUIDE.zh.md`**（发布自检）互为补充。

---

## 一、为什么「小仓库」不能独立跑 GUI？

CPW-Pro（入口 **`python -m cpwpro`**）会调用发行根目录下的 **`start_server.exe`、`start_client.exe`**，并依赖 **CapsWriter 服务端** 加载的 GGUF/ONNX 等权重。为控制仓库体积，这些内容在 **`.gitignore`** 中排除，**不会出现在 GitHub**：

| 缺失项 | 作用 |
|--------|------|
| `internal/` | 官方绿色包内嵌的 Python 运行时与大量依赖 |
| `models/**/*.gguf`、`*.onnx` | 各 ASR 路线的模型权重 |
| `start_server.exe` / `start_client.exe` | 听写服务端与文件转写客户端入口 |
| `util/llama/bin/*.dll`、`*.exe` | llama.cpp / Vulkan 等相关原生库（按需） |

因此：**只 `git clone` 本仓库 ≠ 可双击即用的完整离线包**。这是刻意设计，避免数 GB 二进制把 GitHub 推送撑爆。

Python 依赖（`requirements.txt`）可 `pip` 安装；**上述 exe / internal / 权重**需从 **官方 Release** 或其它说明渠道补齐。

---

## 二、给他人用的推荐路径（尽量少踩坑）

### 路径 A — 推荐：**以官方绿色包为底板，再叠加本仓库源码**

适合大多数 Windows 用户。

1. 下载 [CapsWriter-Offline Latest Release](https://github.com/HaujetZhao/CapsWriter-Offline/releases/latest) 的软件本体并解压到例如 `D:\CapsWriter\`
2. 再下载 [Models Release](https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models) ，按官方说明解压到 `models\` 各子目录
3. **克隆本仓库**到临时目录，或直接下载 ZIP 解压
4. 将本仓库中的 **CPW-Pro 与侧车脚本**复制到**官方解压根目录**（合并覆盖），至少包括例如：
   - `launcher/`（`Launch_CPW-Pro.bat` / 静默 `.vbs`）、`cpwpro/`（GUI 与服务封装包）、以及根目录 **兼容 shim**：`cpw_worker.py`、`cpw_progress.py`、`cw_transcribe.py` 等（指向 `cpwpro.*`，旧脚本仍可 import）。
   - `cpwpro/support/`（`config_manager`、`llm_client`、`media_utils`、`vad_utils`、`timestamp_quality`）、`config.example.json`、`requirements.txt`
   - 以及你对引擎侧若有修改过的 `core_*.py`、`util\` 下对应文件（按需）
5. 在该根目录新建或使用虚拟环境：`pip install -r requirements.txt`
6. 复制 **`config.example.json`** 为 **`config.json`**（或在 GUI 首启自动生成），在 **设置**里填写 LLM 的 API（若使用 AI 笔记等功能）
7. 运行：**先启动 `start_server.exe`**，再 **`python -m cpwpro`**（或双击 **`launcher\Launch_CPW-Pro.bat`**）

这样 **exe / internal / models** 全部由官方包保证齐全，你只叠加 GUI 与脚本更新。

### 路径 B：**只克隆本仓库**，再手动补齐二进制

适合熟悉目录结构、想目录干净的用户。

1. `git clone <你的仓库 URL>`  
2. 单独下载并解压 **官方 Latest Release**、**Models Release**  
3. 从官方解压目录**复制**到克隆根目录：
   - 整个 `internal/`（若你与官方一致使用内嵌运行时）
   - `start_server.exe`、`start_client.exe`
   - `models/` 下各模型的 **`.gguf` / `.onnx`** 等（与官方目录结构一致）
   - 若服务端需要：将官方包里的 `util\llama\bin\` 下 **`*.dll` / `*.exe`** 拷入你克隆目录的同名路径（若克隆里没有这些文件）
4. 在该根目录安装依赖并启动 GUI：
   ```text
   pip install -r requirements.txt
   python -m cpwpro
   ```
   Windows 也可双击 **`launcher/Launch_CPW-Pro.bat`**（推荐使用项目内 `myenv` 时需先建好虚拟环境并安装依赖）。

---

## 三、本机「大仓库开发 / 小仓库上云」怎么用脚本同步？

在 **日常开发目录**（含完整 `internal`、模型，不用于直接 push 大历史）下执行：

```powershell
cd "...\CapsWriter-Offline"
.\scripts\sync_to_github_clone.ps1           # 默认：仅同步叠加面（与 overlay ZIP 同源清单，外加 .gitignore / LICENSE）
.\scripts\sync_to_github_clone.ps1 -DryRun   # 预演（robocopy /L）
# 若曾有过「整仓库」同步，目标里残留的 util\models\core_*.py 等不会自动删，请按需手工删掉再 push
.\scripts\sync_to_github_clone.ps1 -FullMirror # 不推荐：沿用旧逻辑，近似整棵树 robocopy /E（易把小仓库灌满）
```

清单统一定义在 **`scripts/_cpw_overlay_manifest.ps1`**（与 **`make_release_overlay.ps1`** 共用）。

同步完成后，在 **`CapsWriter-Offline-GitHub`** 里：`git add` → `commit` → `push`。

若你的小仓库目录名不同：

```powershell
.\scripts\sync_to_github_clone.ps1 -DestName "你的文件夹名"
```

---

## 四、与你的稳定目录关系（小结）

| 目录 | 用途 |
|------|------|
| `CapsWriter-Offline` | 本机完整开发/运行；体积大、可有私有 Git 历史 |
| `CapsWriter-Offline-GitHub` | 仅源码 + 小文件；与 GitHub 联动；**不能直接当绿色包发给不懂配置的人** |

对外发布时：在 **readme** 或 **Release** 里写清「先装官方包 + 覆盖本仓库脚本」或「克隆后从官方复制 internal/exe/models」，并链到本文。

---

*与 `PROJECT_DOCUMENTATION.md`、根目录 `readme.md` 互为补充。*
