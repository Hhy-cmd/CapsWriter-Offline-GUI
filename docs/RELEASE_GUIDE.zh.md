# GitHub 发布与合规自检（CPW-Pro）

面向**维护者**：发 Release、写 README、对外宣传前过一遍本文。

---

## 一、Release 资产建议（别人怎么「一次下对」）

### 1. 不要放进你方 Release 的东西（体积/版权）

- `internal/`、预编译 `start_server.exe` / `start_client.exe`、`.gguf` / `.onnx` 模型  
  → **让用户从 [官方 CapsWriter-Offline Releases](https://github.com/HaujetZhao/CapsWriter-Offline/releases) 与 [models](https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models) 下载**（版权与体积属官方发行策略）。
- 用户个人 `config.json`（含 API Key）、`logs/`、`output/` 等。

### 2. 建议打成的 ZIP（示例名）

#### 2.1 一键打包（推荐）

在仓库根目录下执行 PowerShell：

```powershell
cd "你的\CapsWriter-Offline"
.\scripts\make_release_overlay.ps1           # 输出到 .\dist\CPW-Pro-overlay-<版本>.zip
.\scripts\make_release_overlay.ps1 -DryRun # 预演包含内容
.\scripts\make_release_overlay.ps1 -OutDir D:\releases -Version 1.0.1
```

版本号默认读取 `cpwpro\_version.py` 中的 `__version__`。输出默认在仓库 `dist\`（已 `.gitignore`）。

**常见错误**

- `The term ... make_release_overlay.ps1 is not recognized`：当前目录不对，或 `CapsWriter-Offline-GitHub` 里还没有该脚本——请先在**大仓库**执行 `.\scripts\sync_to_github_clone.ps1`（会同步 `scripts\` 等叠加面），或在大仓库运行：  
  `.\scripts\make_release_overlay.ps1 -RepoRoot '..\CapsWriter-Offline-GitHub'`
- 参数请**不要用注释粘在同一行**。错误示例：`-Version 1.0.1 .\scripts` 会变成非法版本字符串。应分两行或使用引号：  
  `-Version '1.0.1'` 与 `-OutDir 'D:\releases'` 分开写。

#### 2.2 ZIP 内容说明

例如 **`CPW-Pro-overlay-1.0.0.zip`**，仅含**可进 Git 的源码与配置模板**，解压后**合并覆盖**到用户已解压的官方绿色包根目录：

- `cpwpro/`（含 `support/`）
- `launcher/`
- `cpw_pro_ui.py` 及根目录兼容 shim（若仍保留）
- `requirements.txt`、`config.example.json`
- `readme.md`、`GITHUB_CLONE_SETUP.md`、`PROJECT_DOCUMENTATION.md`
- `docs/`（含 `RELEASE_GUIDE.zh.md`、`CPW_PRO_OVERVIEW.zh.md`；**不含** 仅仓库内维护的 `SCRIPTS_RUNBOOK.zh.md`）
- `scripts/`（含 `make_release_overlay.ps1` 与本同步脚本，便于他人复现打包）
- `assets/` 下图标等你允许再分发的资源

**不要**假设用户会 `git clone`；在 Release 说明里用**编号步骤**写：先官方包 → 再模型 → 再你的 ZIP → `pip install -r requirements.txt`。

### 3. Release 文案必备字段

- **CPW-Pro 版本**：与 `cpwpro/_version.py` 中 `__version__` 一致。
- **已测试的上游**：与 `UPSTREAM_CAPSWRITER_TESTED_WITH` 一致或更具体（推荐写明官方 **Release tag 或发布日期**）。
- **不兼容声明**：若官方将来改名 exe、改目录或改转写协议，本 overlay 可能需更新；请用户以本仓库 **Releases** 为准。

---

## 二、权限与隐私（面向最终用户说明 + 你自查）

在 README 或「用户须知」中建议明确：

| 项目 | 说明 |
|------|------|
| **麦克风** | CapsWriter 客户端录音；CPW-Pro 播放本地 WAV 不涉及上传录音至你方服务器（除非你另行实现）。 |
| **网络** | `yt-dlp` 解析下载、LLM API（OpenAI 兼容）、版本检查（若有）等；**仅在用户主动使用对应功能时**访问网络。 |
| **剪贴板** | AI 笔记「复制」等使用系统剪贴板。 |
| **托盘** | Windows 托盘图标；关主窗可缩小到托盘（需 `pystray` + `Pillow`）。 |
| **文件系统** | 写入 `output/`、临时下载与转写产物；路径由用户在配置中选择。 |

**密钥**：提醒用户勿将含 `api_key` 的 `config.json` 提交到公共空间；本仓库已通过 `.gitignore` 忽略常见配置文件。

---

## 三、法律与许可证（务必确认）

1. **上游 CapsWriter-Offline**  
   遵守其许可证与致谢要求；二进制与模型分发方式以官方为准，**不要随意把官方 exe/模型重新托管**除非你确认许可允许。

2. **你方改造的代码**  
   建议在仓库根目录增加 **`LICENSE`**（若上游有许可证，写明「整体以某许可证为准」或「补丁部分为 MIT」等，必要时咨询法务）。

3. **第三方 API**  
   在文档中写明：用户使用 DeepSeek / OpenAI / Kimi 等须遵守各自服务条款；你不代理其服务。

4. **商标与项目名**  
   readme 中标明：**非官方**或与官方关系（fork / 扩展 / 个人维护），避免暗示「CapsWriter 官方出品」除非你获得授权。

5. **`yt-dlp` 与各站点**  
   提醒用户解析下载符合当地法律与站点 ToS。

---

## 四、与技术兼容相关的发布习惯

1. **官方引擎升级后**  
   每季度或官方大版本发布后，在本机官方包上跑一遍：**启动 server → 启动 client → CPW-Pro 全流程**；通过后更新 `UPSTREAM_CAPSWRITER_TESTED_WITH` 与本 Release 说明。

2. **语义化版本（建议）**  
   - CPW-Pro **主版本**：与调用方式/API 破坏性变更对齐。  
   - **次版本**：功能增加。  
   - **修订号**：Bugfix。

3. **可选**：在仓库启用 **Security policy**（`Security.md`）、**Issues 模板**，便于收集「某官方版本下失效」的报告。

---

*与根目录 `readme.md`、`GITHUB_CLONE_SETUP.md` 配套使用。*
