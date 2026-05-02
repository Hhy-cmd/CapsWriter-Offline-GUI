# CPW-Pro / CapsWriter — 脚本与启动方式备忘

面向维护者与本地开发：**不随 `CPW-Pro-overlay-*.zip` 分发**（见 `scripts/make_release_overlay.ps1` 对本文的排除）；Git 仓库保留即可。

---

## 一、CPW-Pro（工作台 GUI）

| 方式 | 命令 / 操作 | 说明 |
|------|-------------|------|
| **推荐** | `python -m cpwpro` | 包入口 `cpwpro/__main__.py`，最终调用 `cpwpro.ui.app.main` |
| **虚拟环境** | `myenv\Scripts\python.exe -m cpwpro` | `launcher\Launch_CPW-Pro.bat` 优先使用该解释器 |
| **兼容 shim** | `python cpw_pro_ui.py` | 与上一行等价；文档建议统一用 `-m cpwpro` |
| **带控制台** | 双击 `launcher\Launch_CPW-Pro.bat` | 工作目录为项目根；失败时暂停显示退出码 |
| **静默（无黑窗）** | 双击 `launcher\Launch_CPW-Pro-quiet.vbs` | `pythonw -m cpwpro`，依赖已就绪 |

**与官方引擎配合**：先启动发行根目录的 **`start_server.exe`**，再开 CPW-Pro；批量/文件链路由工作台调度 **`start_client.exe`**。

---

## 二、CapsWriter 上游引擎（源码调试）

| 角色 | 入口 | 说明 |
|------|------|------|
| 服务端 | `python core_server.py` | WebSocket 服务与 ASR 加载 |
| 客户端 | `python core_client.py` | 麦克风听写默认模式 |
| 文件转写 | `python core_client.py <音频或媒体路径>` | 与 exe 模式的文件转写类似 |

发行用户侧通常为根目录 **`start_server.exe` / `start_client.exe`**（来自官方 Release，非本 overlay 附带）。

---

## 三、Release / 仓库同步（PowerShell 5.1+）

在项目根目录或指定路径执行；若受限可：`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\...\ps1`。

| 脚本 | 用途 |
|------|------|
| `scripts\sync_to_github_clone.ps1` | **默认**仅叠加面（清单见 `_cpw_overlay_manifest.ps1`）；`-FullMirror` 为旧版整树；`-DryRun` 预演 |
| `scripts\make_release_overlay.ps1` | 生成 **`CPW-Pro-overlay-<版本>.zip`**；`-OutDir`、`-Version`、`-RepoRoot`、`-DryRun` |

本文档文件名在打包脚本中被 **`/XF SCRIPTS_RUNBOOK.zh.md`** 排除，ZIP 内不会出现。

---

## 四、根目录 `cpw_*.py` / `cw_*.py`

多为**兼容转发**（例如 `cpw_worker.py` 导出 `cpwpro.worker`），**不是要单独作为主程序长期使用的入口**。日常以 **`python -m cpwpro`** 为准。

---

## 五、其他脚本（非 CPW-Pro 主线）

仓库内 **`models\`**、`**LLM\**` 等目录下另有实验或辅助脚本（如 Ollama 相关），不属于标准「先 server 后 CPW-Pro」流程；需要时再单独标注用途即可。

---

## 六、文档交叉引用

- 装机与权限：`readme.md`、`GITHUB_CLONE_SETUP.md`
- 架构与模块：`PROJECT_DOCUMENTATION.md`
- 产品说明：`docs/CPW_PRO_OVERVIEW.zh.md`
- 发布与合规：`docs/RELEASE_GUIDE.zh.md`
