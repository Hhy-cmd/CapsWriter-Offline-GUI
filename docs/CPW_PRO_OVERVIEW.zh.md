# CPW-Pro 产品与能力说明（详版）

本文档面向**想全面了解本扩展的人在读什么**：目标用户、核心价值、功能边界、典型流程与配置线索。底层类图与文件名级说明仍以 **`PROJECT_DOCUMENTATION.md`** 为准。

---

## 1. 我们为什么做 CPW-Pro

**CapsWriter-Offline（官方）**把「离线 ASR」做到了极致：服务端推理 + 客户端快捷键听写或文件丢给 client 转写，适合**日常语音输入**与**单文件转写**。

现实里还有一大类需求——**从网络课程、播客、直播回放、会议录像到「可编辑字幕 + 校对 + 归档」**。这类链路往往涉及：

- 链接解析与下载、多格式容器与音轨；
- 统一转成引擎友好的 **16 kHz WAV**；
- **批量队列**与**可视化进度**，而不是只靠黑窗口日志；
- 转写结果在桌面端**试听、拖动时间轴、改字幕**，再按需交给 **LLM** 生成笔记或摘要；
- （可选）**长音频切段**以降低单次推理压力——作为**旁路策略**，不修改官方 ASR 模型本身。

**CPW-Pro** 就是盖在官方引擎之上的 **CustomTkinter 图形壳 + 工作流编排**：  
**不替代**官方 `start_server` / `start_client` 的协议与模型，而是把它们**摆进一条对内容创作者更顺手的流水线**。

---

## 2. 定位（一句话）

| 组件 | 角色 |
|------|------|
| **官方 CapsWriter** | 语音识别「发动机」：WebSocket 服务、模型、听写与文件转写 exe。 |
| **CPW-Pro** | 「驾驶舱」：媒体入口、格式准备、转写调度、字幕/波形/播放、AI 笔记与配置管理。 |

---

## 3. 功能清单（按模块）

### 3.1 输入与下载

- **链接框**：支持常见视频平台 URL / BV 号等（由 **yt-dlp** 执行解析与下载，需网络）。
- **拖放区**：将本地 **mp4 / mp3 / wav / m4a / mkv** 等拖入（**tkinterdnd2**；若环境冲突可用环境变量关闭，见下文）。
- **输出目录**：可选择转写与中间产物落盘位置（与 `config.json` 持久化）。

### 3.2 转写前准备与调度

- 使用 **ffmpeg** 将音/视频规范为 **16 kHz 单声道 WAV**，与官方 client 文件转写路径对齐。
- **自动转写**、**快速下载**等开关（按 UI 实际选项）控制是否下载后立即进入转写等行为。
- **worker 层**（`cpwpro.worker`）负责：依赖检测、必要时拉起 **`start_server.exe`**、以子进程方式调用 **`start_client.exe`**，并将日志回流到界面。

### 3.3 标准转写与 VAD 长音频旁路

- **整段转写**：单 WAV 直接走官方 client，得到 **SRT / TXT / JSON** 等惯用产物。
- **VAD 切片转写**（可选）：对特别长的素材，用 **静音检测切段**（`cpwpro/support/vad_utils.py`，依赖 **pydub** 等），逐段转写后再 **拼回时间轴**上的整段 SRT——**不改服务端模型**，只是输入形态上的「侧车」。

### 3.4 播放、波形与字幕编辑

- 内置 **AudioEngine**（**sounddevice + soundfile**）：非阻塞播放、暂停、**进度条 seek**。
- **波形示波**与**卡拉 OK 式高亮行**：便于对照听感改字。
- **字幕分页编辑**、**Ctrl+S 保存 SRT**；播放与「结束编辑并播放」类操作用 **F9 / Ctrl+F9**（避免与系统输入法快捷键冲突）。
- 转写完成后可跑 **时间戳质量启发式**（`timestamp_quality`），在日志中提示时间轴是否像「均匀估算」而非声学对齐，方便判断后续精修或重转策略。

### 3.5 AI 笔记（与官方 LLM 角色系统区分）

- 基于 **`prompts.json`** 的模板库，在设置中维护模板名称与正文。
- 使用 **OpenAI 兼容 Chat Completions + SSE**（`cpwpro/support/llm_client.py`，标准库 **urllib**），支持 DeepSeek / Kimi / OpenAI / Ollama（本地 `/v1`）等常见端点。
- **流式**输出到独立笔记窗口，可复制或 **导出 Markdown**。
- 注意：这是 **CPW-Pro 自用 HTTP 客户端**；官方 CapsWriter 里另有一套 **`util/llm/` 角色管线**（语音前缀触发、Toast 等），二者**并存、职责不同**。

### 3.6 设置与配置

- **`config.json`**：API Base、模型名、提供商预设、输出目录等（勿提交仓库，参见 `.gitignore`）。
- **`config.example.json`**：可进 Git 的模板。
- 设置 UI 内可 **拉取模型列表**（视 API 支持）、管理 Prompt 模板等（以当前 `app.py` 实现为准）。

### 3.7 系统托盘与启动方式

- 安装 **pystray + Pillow** 后，关主窗可 **缩小到托盘**；Windows 下**左键**单击图标会触发菜单里 **`default=True`** 的项（通常为「打开主界面」）；也可托盘菜单退出进程。
- 环境变量：**`CPW_TRAY_NO_HIDE=1`** 关窗即真退出；**`CPW_TRAY_DISABLE=1`** 不创建托盘；**`CPW_TRAY_ICON`** 自定义图标路径。
- 启动：**`python -m cpwpro`**、**`launcher/Launch_CPW-Pro.bat`**、**`launcher/Launch_CPW-Pro-quiet.vbs`**（静默）。

---

## 4. 典型工作流（示例）

1. 安装官方绿色包与模型 → 启动 **`start_server.exe`**。  
2. 合并本仓库 overlay → **`pip install -r requirements.txt`** → **`python -m cpwpro`**。  
3. **粘贴 URL** 或 **拖入文件** → 选择输出目录 → **开始 AI 转写**（视选项决定是否 VAD）。  
4. 在右侧 **播放 + 改字幕** → **Ctrl+S** 保存。  
5. 需要时打开 **AI 笔记**，选模板 → 流式生成 → 复制或存 **.md**。

---

## 5. 依赖与环境提示

- **Python 3.11+** 推荐（以你本机验证为准）。  
- **ffmpeg** 须在 PATH 或官方包已配好（与 worker 调用方式一致）。  
- **yt-dlp**、**pydub**、**CustomTkinter** 等见根目录 **`requirements.txt`**。  
- 与 **tkinterdnd2** 冲突时，可设 **`CPW_DISABLE_TKDND=1`** 关闭拖放（仅保留「选择文件」等路径）。

---

## 6. 文档地图

| 文档 | 用途 |
|------|------|
| **`readme.md`** | 对外总览：愿景与功能摘要、安装、兼容版本、维护者与权限简述。 |
| **`docs/CPW_PRO_OVERVIEW.zh.md`** | 本文：产品级能力说明书。 |
| **`GITHUB_CLONE_SETUP.md`** | 小仓库 / 克隆后如何补齐 exe 与模型。 |
| **`PROJECT_DOCUMENTATION.md`** | 技术架构、目录、协议与引擎侧说明。 |
| **`docs/RELEASE_GUIDE.zh.md`** | 打 ZIP、GitHub Release、合规自检。 |

---

## 7. 非目标（避免预期偏差）

- CPW-Pro **不包含** ASR 权重与 **`internal/` 运行时** 的再分发；这些仍须从 **官方 Release** 获取。  
- **不保证**与官方未来任意大版本「零修改即兼容」——若 exe 名、目录约定或协议变更，可能需要更新 overlay；发版时请写明 **已验证的官方版本或日期**。  
- **实时麦克风听写上屏**仍以官方 **`start_client`** 体验为主；CPW-Pro 强项在 **媒体批量工作流与字幕后期**。

---

*README 偏「第一印象与装机步骤」，本文偏「产品与边界说明书」。*
