# CapsWriter-Offline + CPW-Pro — 项目技术文档（全景版）

> **说明**：本文档在官方 CapsWriter-Offline 架构说明基础上，并入 **CPW-Pro**（`cpw_pro_ui.py`）当前实现、侧车模块与推荐的代码分层，便于二次开发与 GitHub 开源发布。

## 一、项目概述

CapsWriter-Offline 是一款**完全离线的语音输入与音视频转录工具**，采用 C/S 架构，支持 Windows/Linux/macOS。核心能力包括：

1. **实时语音输入**：按下 CapsLock 讲话，松开后识别结果自动上屏
2. **音视频文件转录**：将 mp4/mp3/wav/m4a 等文件转录为 srt 字幕 + txt 文本 + json 时间戳
3. **AI 润色与翻译**：模块化 LLM 角色系统（`util/llm/` + `LLM/` 角色脚本），支持 OpenAI/DeepSeek/智谱/Ollama 等多厂商
4. **CPW-Pro GUI**：基于 CustomTkinter 的 **独立工作台**，负责链接/文件输入、下载与 **16k WAV 准备**、拉起/检测 `start_server`、`start_client.exe` 文件转写、**字幕编辑 + 波形示波 + 播放**、基于 `prompts.json` 的 **流式 AI 笔记**（根目录 `llm_client.py`，与引擎侧 `util/llm/` 并存但职责不同）

---

## 二、项目架构

### 2.1 整体拓扑

```
┌───────────────────────────────┐     ┌────────────────────────────────┐
│  cpw_pro_ui.py + cpw_worker   │     │     CapsWriter-Offline 引擎     │
│  (CustomTkinter GUI 壳)       │     │                                │
│                               │     │  ┌──────────┐  ┌───────────┐  │
│  · 链接下载 (yt-dlp API)      │     │  │  Client  │  │  Server   │  │
│  · ffmpeg → 16k mono WAV      │     │  │ start_   │  │ start_    │  │
│  · tkinterdnd2 拖放媒体       │ subprocess │ client   │  │ server    │  │
│  · 字幕编辑器 + 波形 + 播放器  │─────│──▶ .exe    │──│▶ .exe     │  │
│  · AI 笔记 (llm_client)       │     │  │ 或       │  │           │  │
│  · timestamp_quality / vad    │     │  │ core_    │  │ core_     │  │
│                               │     │  │ client    │  │ server    │  │
│  CPW-Pro 不 import ASR 模块   │     │  └──────────┘  └───────────┘  │
│  仅子进程 + 根目录侧车 .py     │     │  WebSocket ◀──▶ ASR 推理      │
└───────────────────────────────┘     └────────────────────────────────┘
```

**发布形态注意**：发行包中 CPW-Pro 通过 **`start_client.exe` / `start_server.exe`** 与引擎交互；开发环境可用 `python core_client.py <wav>` 等价调试，但 GUI 代码路径写死为 exe（与根目录 `cpw_worker.py` 中 `_ROOT` 一致）。

### 2.2 进程模型

| 进程 | 入口 | 职责 |
|------|------|------|
| **cpw_pro_ui.py** | `python cpw_pro_ui.py` | GUI 壳程序，管理 CapsWriter 子进程生命周期，提供视频下载→转写→AI润色完整工作流 |
| **core_server.py** | `python core_server.py` | WebSocket 服务端，加载 ASR 模型，等待客户端连接并进行语音识别推理 |
| **core_client.py** | `python core_client.py [file]` | 两种模式：(1) 默认麦克风模式，CapsLock 快捷键语音输入；(2) 给定文件路径则转录文件 |
| **models/AI-Polish.py** | `python models/AI-Polish.py` | 独立的 AI 润色助手（备用入口，非主要流程） |

**CPW-Pro GUI 侧车模块（与引擎解耦、不 import `util/client`）**

| 模块 | 行规模（约） | 职责 |
|------|-------------|------|
| `cpw_pro_ui.py` | ~2200+ | `App` 主窗体：布局、状态、线程调度、日志与进度、字幕 UI、设置/笔记弹窗 |
| `cpw_worker.py` | ~280 | `CPWWorker`：yt-dlp、`ffmpeg`、探测/启动 `start_server.exe`、拉起 `start_client.exe` 并流式读 stdout |
| `cpw_progress.py` | ~150 | 下载日志 %、`[核心]` 发送/转录秒数 → 进度条比例（纯函数）；UI 仅做节流与 `CTkProgressBar` |
| `cpw_textutil.py` | ~70 | `strip_ansi`、`normalize_url`（Bilibili BV/av、日志中 URL 抽取） |
| `cpw_theme.py` | ~35 | `apply_ctk_defaults`、`scope_canvas_bg_and_stroke`（CTk 外观与示波 Canvas 色，与布局解耦） |
| `cw_transcribe.py` | ~230 | 后台线程：`run_download_then_extract`、`run_extract_all`、普通/VAD 转写编排（hooks → UI） |
| `config_manager.py` | ~200 | `config.json` / `prompts.json` 读写合并 |
| `media_utils.py` | ~475 | `AudioEngine`（sounddevice/soundfile）、`parse_srt`、波形数据 `get_scope_mono_tail` |
| `llm_client.py`（根目录） | ~150 | CPW-Pro 专用：OpenAI 兼容流式 `stream_chat`、`build_messages`（ urllib，无引擎依赖） |
| `timestamp_quality.py` | ~270 | 加载后对 WAV/SRT/JSON 做**只读**时间戳质量启发式报告 |
| `vad_utils.py` | ~340 | pydub 静音切段 + 多段 SRT 时间轴拼回（长音频侧车策略，**不改** ASR 服务端） |

**快捷键（Windows）**：全局与字幕框内 **播放/暂停、结束编辑** 使用 **F9** / **Ctrl+F9**（避免 Ctrl+Space 被输入法占用）。

**拖放**：`tkinterdnd2`，首帧后延迟 `TkinterDnD._require`；环境变量 `CPW_DISABLE_TKDND=1` 可关闭。

### 2.3 目录结构（含 CPW-Pro 与引擎）

```
CapsWriter-Offline/
├── cpw_pro_ui.py          # ★ CPW-Pro GUI 主程序 (~2200+ 行，持续拆分为包，见第八节)
├── cpw_worker.py          # CPW-Pro：下载 / ffmpeg / start_server / start_client 子进程
├── cpw_progress.py        # 日志解析 → 任务进度条比例（与 UI 解耦）
├── cpw_textutil.py       # ANSI 剔除、下载链接规范化（BV/av/URL）
├── cpw_theme.py          # CTk 外观与示波配色（含可选 darkdetect）
├── cw_transcribe.py       # 下载/批量抽轨/VAD·多文件转写线程管线
├── core_client.py         # ★ CapsWriter 客户端入口 (~235行)
├── core_server.py         # ★ 服务端入口 (~124行)
├── config_client.py       # 客户端配置
├── config_server.py       # 服务端配置 + 模型路径 + 模型参数
├── config_manager.py      # CPW：`config.json` + `prompts.json`
├── media_utils.py         # 音频引擎 + SRT 解析 + 示波取样 (~475行)
├── llm_client.py          # CPW-Pro 专用 LLM 流式客户端（与 util/llm/llm_client 不同）
├── timestamp_quality.py   # 时间戳质量只读诊断
├── vad_utils.py           # pydub 切段与 SRT 拼轴（可选长音频路径）
├── requirements.txt       # CPW-Pro：`customtkinter`, `tkinterdnd2`, `yt-dlp`, `pydub` 等
├── config.example.json    # CPW-Pro 配置模板（可进仓库，`api_key` 为空）
├── config.json            # CPW-Pro 应用配置（**本地生成**，`.gitignore` 忽略，勿提交）
├── prompts.json           # AI Prompt 模板库
├── hot-server.txt         # 服务端热词 (349行，Fun-ASR-Nano 专用)
├── hot.txt / hot-rule.txt / hot-rectify.txt  # 客户端热词文件
├── LLM/                   # LLM 角色定义
│   ├── __init__.py / default.py
│   ├── 大助理.py / 小助理.py
│   ├── 翻译.py / 高级翻译.py
├── models/                # ASR 模型文件目录 (需下载)
│   ├── Fun-ASR-Nano/      # Fun-ASR-Nano-GGUF
│   ├── Qwen3-ASR/         # Qwen3-ASR-1.7B
│   ├── SenseVoice-Small/  # SenseVoice
│   ├── Paraformer/        # Paraformer
│   └── AI-Polish.py       # 独立 AI 润色助手
├── util/
│   ├── protocol.py        # WebSocket 通信协议定义
│   ├── constants.py       # 常量定义
│   ├── common/
│   │   └── lifecycle.py   # 生命周期管理器 (单例)
│   ├── client/
│   │   ├── state.py       # 客户端全局状态管理
│   │   ├── startup.py     # 客户端组件初始化
│   │   ├── websocket_manager.py  # WebSocket 连接管理
│   │   ├── clipboard/     # 剪贴板操作 + Ctrl+V 模拟
│   │   ├── diary/         # 日记归档写入
│   │   ├── shortcut/      # 快捷键管理
│   │   ├── ui/            # 托盘图标、Toast 弹窗
│   │   ├── audio.py       # 音频流管理
│   │   └── udp/           # UDP 广播/控制
│   ├── server/
│   │   ├── server_recognize.py    # ASR 识别核心逻辑
│   │   ├── server_ws_recv.py      # WebSocket 接收+分段
│   │   ├── server_ws_send.py      # WebSocket 发送结果
│   │   ├── text_merge.py          # 文本拼接 (简单+精确)
│   │   ├── error_handler.py       # 错误音频保存
│   │   └── server_cosmic.py       # 服务端全局状态
│   ├── hotword/
│   │   ├── rag_accu.py     # 精确 RAG 检索 (AccuRAG)
│   │   └── algo_calc.py    # 音素匹配算法核心 (520行)
│   ├── llm/
│   │   ├── llm_handler.py       # LLM 系统协调器
│   │   ├── llm_client.py        # 流式 SSE 客户端
│   │   ├── llm_role_config.py   # 角色配置 Dataclass
│   │   ├── llm_context.py       # 对话上下文管理
│   │   ├── llm_message_builder.py  # 消息构建
│   │   ├── llm_processor.py     # LLM 处理引擎
│   │   ├── llm_output_typing.py # 打字输出模式
│   │   ├── llm_output_toast.py  # Toast 弹窗输出模式
│   │   └── llm_interfaces.py    # 接口定义 (Protocol)
│   └── tools/              # 工具模块
│       ├── format_tools.py      # 中英空格调整
│       ├── chinese_itn.py       # 中文数字归一化
│       └── empty_working_set.py # 内存优化
├── assets/                 # 图标资源
├── output/                 # 输出目录
└── logs/                   # 日志目录
```

---

## 三、核心业务能力详解

### 3.1 能力一：实时语音输入（麦克风模式）

**触发方式**：按下 CapsLock 键不放 → 开始录音 → 松开 CapsLock → 结束录音 → 识别结果上屏

**完整流程**：

```
  用户按下 CapsLock
       │
       ▼
  pynput 监听器检测到按键
       │
       ▼
  ShortcutManager.handle_press()
  ├── 开始计时 (threshold=0.3s，用于区分短按/长按)
  ├── ClientState.start_recording()
  ├── AudioStreamManager.open() → sounddevice.InputStream(16kHz, mono, float32)
  └── 音频回调 → queue_in → WebSocket 分段发送
       │
       │  (持续录音中，每 seg_duration=60s 分段，seg_overlap=4s 重叠)
       │  音频数据: base64 编码 → AudioMessage.to_json() → WebSocket send
       │
       ▼
  用户松开 CapsLock
       │
       ▼
  ShortcutManager.handle_release()
  ├── 计算 duration (录音时长)
  ├── 如果 duration < threshold → 短按，补发 CapsLock 按键
  ├── 如果 duration >= threshold → 发送 is_final=True → 等待识别结果
  │
  └── WebSocket 接收 RecognitionResult
      ├── text (简单文本拼接) → set_output_text() → UDP 广播
      ├── 执行热词替换 (RAG AccuRAG)
      ├── LLM 润色处理 (如果匹配角色前缀)
      └── 最终输出:
          ├── typing 模式: pyclip copy → pynput Ctrl+V
          └── toast 模式: ToastWindow 弹窗显示 Markdown
```

**关键配置项** (`config_client.py`)：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `shortcuts` | CapsLock + X2 鼠标 | 可配置多个快捷键同时生效 |
| `threshold` | 0.3s | 短按阈值，低于此值视为单击并补发按键 |
| `mic_seg_duration` | 60s | 麦克风分段时长 |
| `mic_seg_overlap` | 4s | 分段重叠时长 |
| `paste` | False | 是否启用 Ctrl+V 粘贴模式 |
| `hot` | True | 热词替换开关 |
| `hot_thresh` | 0.85 | RAG 热词替换阈值 |
| `hot_similar` | 0.6 | RAG 相似热词检测阈值 |
| `save_audio` | True | 是否保存录音文件 |

### 3.2 能力二：音视频文件转录

**触发方式**：`python core_client.py video.mp4` 或在 CPW-Pro GUI 中拖入文件/粘贴链接

**完整流程**（以 CPW-Pro GUI 为例）：

```
  输入处理
  ├── 本地文件 → 直接创建 FileTranscriber
  └── URL 链接 (B站/YouTube/其他)
      ├── _normalize_url() → 正则提取 URL + 去除中文杂质
      ├── yt-dlp 下载视频到 tmp_download/
      ├── ffprobe 检测格式和时长 → 超过 1 小时提示确认
      ├── ffmpeg 提取音频: -ac 1 -ar 16000 → 16kHz mono WAV
      └── 立即删除原始视频文件 (节省磁盘)
      │
      ▼
  音频 → WebSocket 分段发送
  ├── ClientConfig.file_seg_duration = 60s
  ├── ClientConfig.file_seg_overlap = 4s
  └── 每段 AudioMessage(is_final=False) → 最后一段 is_final=True
      │
      ▼
  接收识别结果
  ├── text (简单拼接) → .txt 文件
  ├── text_accu (时间戳精确拼接) → .srt 字幕文件
  ├── tokens + timestamps → .json 文件
  └── merge text (未分句长文本) → _merge.txt (可选)
```

**支持的音频格式**：mp4, mp3, wav, m4a, webm, ogg, flac, aac, mov, avi, mkv, wmv, ts, m2ts, flv 等

**SRT 调整功能** (`SrtAdjuster`)：对于已有的 .srt 文件，可以进行二次精准调整（多编码兼容：utf-8-sig → utf-8 → gbk → latin-1）。

### 3.3 能力三：AI 润色与翻译（LLM 角色系统）

**设计理念**：模块化角色系统，每个角色是一个独立的 Python 文件，用户可自由新增。

**角色加载流程**：

```
  init_llm_system()
      │
      ▼
  LLMHandler.__init__()
  ├── RoleLoader.load_all_roles()
  │   └── 扫描 LLM/ 目录 → 解析每个 .py 文件 → 构建 RoleConfig 对象
  │       字段包括:
  │       ├── name: 角色名称 (如 "翻译", "大助理")
  │       ├── match: 是否启用前缀匹配
  │       ├── provider: API 提供商 (ollama/openai/deepseek/moonshot/zhipu/claude/gemini)
  │       ├── model: 模型名称
  │       ├── enable_history: 是否保留对话历史
  │       ├── enable_read_selection: 是否读取鼠标选中文字
  │       ├── output_mode: 'typing' 或 'toast'
  │       ├── system_prompt: 系统提示词
  │       └── temperature / max_tokens 等生成参数
  ├── ContextManager 池: 为每个 enable_history 的角色创建会话上下文
  ├── ClientPool: 按 provider 管理 HTTP 连接
  ├── MessageBuilder: 构建 OpenAI 标准 messages (system/user/assistant)
  └── LLMProcessor: 执行实际的流式 API 调用
```

**处理流程** (`llm_process_text`):

```
  输入文本
      │
      ▼
  RoleDetector.detect()
  ├── 检查是否匹配角色前缀 (如 "翻译 "、空角色匹配所有)
  ├── 匹配 → 获取 RoleConfig, 去除前缀 → content
  └── 不匹配 → 直接输出原文 (打字或剪贴板)
      │
      ▼
  MessageBuilder.build_messages()
  ├── 获取 selection_text (Ctrl+C 选中文字，如果启用)
  ├── 获取 matched_hotwords (热词 RAG 结果)
  ├── 获取 rectify_records (纠错历史)
  └── 拼接完整 messages:
      [system_prompt + hotwords_prefix + selection_prefix + rectify_prefix + input_prefix]
      │
      ▼
  LLMProcessor.process()
  ├── ClientPool 获取对应 provider 的客户端
  ├── stream_chat(base_url, api_key, model, messages) → SSE 流式 yield
  ├── 逐 token 回调:
  │   ├── typing 模式: 逐字 Ctrl+V 粘贴
  │   └── toast 模式: ToastWindow 逐块更新 Markdown
  ├── esc 键可中断输出 (llm_stop_key='esc')
  └── enable_history → ContextManager.add_message() 保存对话
```

**各角色默认配置**：

| 角色 | Provider | 模型 | 输出模式 | 历史 | 选中文字 |
|------|----------|------|----------|------|----------|
| 默认 | Ollama | gemma3:4b | typing | 否 | 否 |
| 翻译 | Ollama | gemma3:12b | toast | 是 | 是(2K) |
| 高级翻译 | DeepSeek | deepseek-chat | toast | 是 | 是(2K) |
| 大助理 | 智谱 | glm-4.5-air | toast | 是 | 是(1K) |
| 小助理 | Ollama | gemma3:4b | typing | 否 | 否 |

**LLM 流式客户端（引擎侧）**：

- 实现位置：[util/llm/llm_client.py](util/llm/llm_client.py)（及连接池、角色管线等）
- 纯标准库或轻依赖，兼容 OpenAI 格式 API
- 供 **麦克风/听写结果润色、翻译、Toast** 等角色使用

**CPW-Pro 笔记流式客户端（GUI 侧）**：

- 实现位置：根目录 [llm_client.py](llm_client.py)
- 仅服务 **转写完成后的「AI 总结 / 复习笔记」**：读 `prompts.json` 模板 + `config.json` 中 `api_base_url` / `model_name` 等
- **与 `util/llm/` 无 import 关系**，避免 GUI 与听写进程强耦合

---

### 3.4 能力四：AI 笔记生成（CPW-Pro GUI）

**流程**：

```
  转录完成后 (有 .srt / .txt 文件)
      │
      ▼
  用户在 CPW-Pro 中选择 Prompt 模板 (如 "精炼复习笔记")
      │
      ▼
  _on_summarize()
  ├── 读取转录文本
  ├── 构建 messages = [{"role": "system", "content": 模板内容},
  │                     {"role": "user", "content": 转录文本}]
  ├── stream_chat() → SSE 流式 yield
  └── 结果写入:
      ├── Toast 窗口实时显示生成过程
      ├── 保存为 _summary.md 文件
      └── 支持随时 ESC 中断
```

**Prompt 模板库** (`prompts.json`)：

| 模板名称 | 用途 |
|----------|------|
| 精炼复习笔记 | 学习助手，输出主题概述+核心要点+关键术语+行动建议 |
| 全量知识库归档 | 知识管理，输出分层标题+概念索引+关键论据+结论摘要 |
| 会议纪要整理 | 会议记录，输出会议主题+参与方观点+决议事项+待办清单 |

模板可自由增删改，UI 中提供模板管理界面。

---

## 四、关键技术实现

### 4.1 通信协议 (WebSocket)

**协议定义**：[util/protocol.py](util/protocol.py)

```python
# 客户端 → 服务端
@dataclass
class AudioMessage:
    task_id: str          # 任务 UUID
    source: 'mic'|'file'  # 音频来源
    data: str             # base64 编码的 float32 音频 (16kHz, mono)
    is_final: bool        # 是否最后一个包
    time_start: float     # 录音开始时间戳
    seg_duration: float   # 分段时长 (默认 60s)
    seg_overlap: float    # 重叠时长 (默认 4s)

# 服务端 → 客户端
@dataclass
class RecognitionResult:
    task_id: str
    is_final: bool
    duration: float       # 已处理音频总时长
    time_start/submit/complete: float
    text: str             # 简单文本拼接结果 (主要输出)
    text_accu: str        # 时间戳精确拼接结果 (字幕用)
    tokens: List[str]     # 字级 token 列表
    timestamps: List[float] # 字级时间戳
```

**服务端分段策略**：

音频数据在服务端缓存到 `AudioCache`，当缓冲时长 ≥ `seg_duration + 2 * seg_overlap` 时，切割出一个 `seg_duration + seg_overlap` 的片段提交识别，缓冲区保留 `overlap` 部分作为下一段的上下文（左移 `stride = seg_duration`）。

```
 缓冲: [.......................]
       |← seg_duration+overlap →|← 保留 overlap →|
       提交识别                   保留到下一个片段
```

### 4.2 ASR 模型适配

**四种模型架构及参数**：

| 模型 | 架构 | 参数量 | 标点 | 时间戳 | GPU加速 | 热词上下文 |
|------|------|--------|------|--------|---------|------------|
| **Qwen3-ASR** | GGUF + ONNX | 1.7B | ✓ | ✓ | Vulkan+DML | ✗ |
| **Fun-ASR-Nano** | GGUF + ONNX | ~300M | ✓ | ✓ | Vulkan+DML | ✓ LLM Decoder |
| **SenseVoice** | ONNX | Small | ✓ | ✗ | DML | ✗ |
| **Paraformer** | ONNX | Large | 需额外模型 | ✗ | DML | ✗ |

**Qwen3-ASR 模型结构**：

```
  音频输入 (16kHz mono)
      │
      ▼
  Encoder Frontend (fp16.onnx) — ONNX Runtime, 特征提取
      │
      ▼
  Encoder Backend (fp16.onnx) — ONNX Runtime, 声学编码
      │
      ▼
  LLM Decoder (q4_k.gguf) — llama.cpp Vulkan, 解码生成
      │
      ▼
  文本输出 (含标点+时间戳)
```

**Fun-ASR-Nano 模型结构**：

```
  音频输入
      │
      ▼
  Encoder-Adaptor (int4.onnx) — ONNX Runtime
      │
      ▼
  CTC (int4.onnx) — ONNX Runtime, 热词检索
      │      │
      │      └→ CTC 热词检索结果
      │
      ▼
  LLM Decoder (q5_k.gguf) — llama.cpp Vulkan
      │      ↑
      │      └─ hot-server.txt 上下文 (context 参数)
      ▼
  文本输出 (含标点+时间戳)
```

### 4.3 音素 RAG 热词系统

**核心算法**：[util/hotword/algo_calc.py](util/hotword/algo_calc.py) (520行)

**功能**：基于音素的模糊匹配，解决 ASR 模型对专业术语/人名/地名的识别错误。

**音素相似度矩阵** (`get_phoneme_cost`)：

```
  前后鼻音: an↔ang, en↔eng, in↔ing, ... (代价 0.5)
  平翘舌:   z↔zh, c↔ch, s↔sh (代价 0.5)
  鼻音边音: n↔l (代价 0.5)
  完全匹配: 代价 0
  完全不同: 代价 1.0
```

**两阶段检索**：

1. **粗筛 (FastRAG)**：快速过滤候选热词
2. **精筛 (AccuRAG)**：使用 DP 编辑距离 + 音素代价矩阵，在文本窗口内寻找最佳匹配位置 (`find_best_match`)

**匹配阈值分级**：

| 阈值 | 用途 |
|------|------|
| `hot_thresh = 0.85` | 高于此值直接替换识别结果 |
| `hot_similar = 0.6` | 高于此值作为 LLM 上下文传入（建议性） |
| `hot_rectify = 0.6` | 纠错历史匹配阈值 |

**热词文件**：

- `hot.txt`：普通热词 (RAG 匹配)
- `hot-rule.txt`：正则规则替换 (如 `\bASR\b → 语音识别`)
- `hot-rectify.txt`：纠错历史记录 (用户手动添加)
- `hot-server.txt`：服务端热词 (349行，Fun-ASR-Nano 的 LLM Decoder 上下文)

### 4.4 文本拼接算法

**文件**：[util/server/text_merge.py](util/server/text_merge.py)

#### 4.4.1 简单文本拼接 (`merge_by_text`)

用于 `RecognitionResult.text` 字段（主要输出）。

**算法**：
1. 在 `prev_text` 末尾窗口（`overlap_chars=30` 字）内搜索
2. 优先精确匹配，失败则容错匹配（`error_tolerance=2` 字）
3. 允许跳过新片段开头的噪音字（`max_skip_new=10`）
4. 找到匹配 → 以匹配点截断拼接，丢弃 prev 尾部噪音
5. 未找到匹配 → 安全兜底：直接拼接

```
  prev: "...这是之前的文本结"
  new:         "文本结尾部分，接着新的内容"
                    ↑ 匹配点
  结果: "...这是之前的文本结尾部分，接着新的内容"
```

#### 4.4.2 时间戳精确拼接 (`merge_tokens_by_sequence_matcher`)

用于 `RecognitionResult.text_accu` 字段（字幕生成用）。

**算法**：
1. 提取 prev 和 new 在重叠时间区域的 tokens
2. 使用 `difflib.SequenceMatcher` 找最长公共子序列
3. 在匹配点截断 prev，拼接 new 从匹配点开始的部分
4. 后处理：清理连续重复标点

**兜底策略**：如果匹配长度 < 2，回退到时间戳硬拼接（跳过 new 中时间戳 ≤ prev 最后一个时间戳+0.1s 的 tokens）。

### 4.5 音频引擎

**文件**：[media_utils.py](media_utils.py) (446行)

**`AudioEngine` 类**：
- 基于 `sounddevice.OutputStream` 的非阻塞播放引擎
- 支持 play/pause/resume/seek/stop
- 帧级 seek：`soundfile` 全量缓冲 + `_current_frame`（与旧版描述一致）
- **`get_scope_mono_tail(n)`**（主线程只读）：以当前播放头为结尾取近 *n* 个采样，混单声道并做局部峰值归一，供 CPW-Pro `tk.Canvas` 低频绘波形（不修改音频回调线程逻辑）
- 历史说明中的「波形提取 extract_waveform」若仍存在，可用于其它可视化；示波条优先走 `get_scope_mono_tail`

**`parse_srt()` 函数**：
- 多编码兼容：utf-8-sig → utf-8 → gbk → latin-1
- 返回字典列表：`index`, `start_sec`, `end_sec`, `time_str`, `text`（供 CPW-Pro 字幕栅格使用）

### 4.6 生命周期管理

**文件**：[util/common/lifecycle.py](util/common/lifecycle.py)

**`LifecycleManager` 单例**：
- **信号处理**：注册 SIGINT(Ctrl+C) / SIGTERM，防手抖逻辑（首次按下提示，1秒内再次按下确认退出）
- **资源清理**：回调注册机制（LIFO 执行顺序：后注册先执行）
- **异步事件**：`asyncio.Event` 通知主循环退出
- **atexit 兜底**：确保异常退出时清理资源
- **强制退出**：`exit_on_signal=True` 时调用 `os._exit(0)`（Client 端）

**使用模式**：
```python
lifecycle.initialize(logger=logger, exit_on_signal=True)
lifecycle.register_on_shutdown(cleanup_func)

# 主循环中
while not lifecycle.is_shutting_down:
    await lifecycle.wait_for_shutdown()  # 阻塞等待退出信号

lifecycle.cleanup()  # 执行所有清理回调
```

### 4.7 剪贴板系统

**文件**：[util/client/clipboard/clipboard.py](util/client/clipboard/clipboard.py)

**核心功能**：
- **多编码安全读取**：utf-8 → gbk → utf-16 → latin1 级联尝试
- **保存/恢复上下文管理器**：`save_and_restore_clipboard()` 保证操作后恢复原内容
- **Ctrl+V 模拟粘贴**：`paste_text()` 通过 pynput 模拟键盘 (Windows: Ctrl+V, macOS: Cmd+V)，支持粘贴后恢复原剪贴板
- **UDP 广播**：识别结果通过 UDP 广播到 `127.255.255.255:6017`，供外部程序消费

### 4.8 日记归档系统

**文件**：[util/client/diary/diary_writer.py](util/client/diary/diary_writer.py)

**功能**：将每次语音识别结果写入按日期组织的 Markdown 日记文件。

```
  output/日记/
  ├── 2026/
  │   ├── 01/
  │   │   ├── 01.md   ← [HH:MM:SS](audio.wav) 识别文本
  │   │   ├── 02.md
  │   │   ...
  │   ├── 02/
  │   ...
```

每个 .md 文件顶部包含正则替换说明，用于将文件链接和 HTML audio 控件互换。

### 4.9 系统托盘 (Windows)

**文件**：[util/ui/tray.py](util/ui/tray.py) (368行)

**功能**：
- pystray 图标 + 右键菜单
- 菜单项：复制最近结果、添加上下文、添加热词、添加纠错、清除LLM记忆、重启音频、退出
- 隐藏/恢复窗口

### 4.10 Toast 弹窗系统

**文件**：[util/ui/toast.py](util/ui/toast.py) (250行)

**功能**：
- Tkinter TopLevel 浮动窗口
- 支持 **Markdown 渲染** (自定义 Markdown 渲染器)
- **流式更新**：逐 token/chunk 追加显示
- 可编辑模式 (`toast_editable=True`)
- 配置项：字体/颜色/大小/背景色/显示时长/初始尺寸
- 支持 ESC 关闭

### 4.11 时间戳质量诊断（`timestamp_quality.py`）

对**已生成**的 WAV + SRT +（可选）JSON 做启发式检查：片段起止是否单调、与音频总长是否离谱、JSON 字级时间是否异常均匀等。CPW-Pro 在 `load_transcript` 后把报告打印到日志，**不重写**转写产物。

### 4.12 长音频侧车切片（`vad_utils.py`）

基于 **pydub** `detect_nonsilent` 生成无静音 WAV 切片，切片分别调用既有 `start_client.exe`；转写结束后用 **`stitch_srt_chunks`** 将各段 SRT 时间轴对齐回整条音频。这是对 CPW-Pro **调度层** 的补充，**不替代**引擎内 WebSocket 分段。

---

## 五、配置体系

### 5.1 配置文件层级

| 文件 | 用途 | 管理者 |
|------|------|--------|
| `config.json` | CPW-Pro GUI 应用配置 (API / 模型 / 输出目录) | AppConfigManager |
| `prompts.json` | AI Prompt 模板库 | PromptLibraryManager |
| `config_client.py` | CapsWriter Client 端配置 (快捷键 / 热词 / 分段) | 代码内常量 |
| `config_server.py` | CapsWriter Server 端配置 (模型选择 / GPU加速) | 代码内常量 |
| `LLM/*.py` | 各 LLM 角色独立配置 | RoleLoader |
| `hot*.txt` | 热词文件 | HotwordManager |

### 5.2 CPW-Pro 应用配置 (`config.json`)

**协作者克隆仓库后**：`config.json` 不在版本库内。首次运行 `cpw_pro_ui.py` 时，`AppConfigManager.ensure_file()` 会在根目录**自动创建**默认 `config.json`（`api_key` 为空、输出目录为相对路径 `output`），随后在 GUI **「设置」** 中填写 `api_base_url`、`api_key`、`model_name` 等即可。**无需**必须从 `config.example.json` 复制；若你希望手写文件，可复制 `config.example.json` 为 `config.json` 再按需修改。

示例（占位符示意，切勿将真实密钥写入仓库）：

```json
{
  "provider": "自定义",
  "api_base_url": "https://api.example.com/v1",
  "api_key": "",
  "model_name": "gpt-4o-mini",
  "template_name": "精炼复习笔记",
  "output_dir": "output"
}
```

### 5.3 CapsWriter 服务端模型配置

**模型选择** (`ServerConfig.model_type`)：`qwen_asr` | `fun_asr_nano` | `sensevoice` | `paraformer`

**GPU 加速选项**：
| 选项 | 默认值 | 说明 |
|------|--------|------|
| `use_dml` / `dml_enable` | False | DirectML 加速 ONNX，AMD 显卡实测会变慢，建议 N 卡开启 |
| `vulkan_enable` | True | Vulkan 加速 GGUF (llama.cpp) |
| `vulkan_force_fp32` | False | Intel 集显精度溢出时可开启 |

---

## 六、开发约束与注意事项

来源：[prompt.cursorrules](prompt.cursorrules)

1. **不要直接 import CapsWriter 引擎内部**：CPW-Pro 通过 **`start_client.exe`** 子进程调转写，不共享 `util.client` 运行时。
2. **UI 框架**：CPW-Pro 仅使用 CustomTkinter + 少量原生 `tk`（示波 `Canvas`、DnD），不引入 PyQt/wxPython。
3. **视频下载**：
   - **CapsWriter 原生约束**（若仍适用其它脚本）：可保持「命令行调用 yt-dlp」风格；
   - **CPW-Pro**：当前使用 **`yt-dlp` Python API**（`YoutubeDL`），便于进度回调与 SSL 重试；与「仅 CLI」并不矛盾，属 GUI 层实现选择。
4. **音频处理**：通过 **ffmpeg 命令行** (`-ac 1 -ar 16000`) 生成引擎所需 WAV。
5. **LLM**：
   - **CPW-Pro 笔记**：根目录 `llm_client.py`；
   - **听写管线润色**：优先 `util/llm/` 体系。
6. **日志**：引擎侧通过 `logger`；CPW-Pro 主要写界面 `CTkTextbox` + `[核心]` 前缀转发子进程 stdout。
7. **多编码兼容**：SRT/文本读写需 utf-8-sig → utf-8 → gbk 等回退（`parse_srt` 已实现）。
8. **线程安全**：GUI 更新必须 `after(0, ...)` 回到主线程。

---

## 七、数据流总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         完整数据流                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  [麦克风]                  [视频URL]          [本地文件]              │
│     │                    yt-dlp ─► ffmpeg        │                  │
│     │                         │                    │                  │
│     ▼                         ▼                    ▼                  │
│  sounddevice            16kHz WAV             16kHz WAV              │
│  InputStream             mono float32          mono float32          │
│  16kHz mono float32                                                 │
│     │                         │                    │                  │
│     └─────────────────────────┼────────────────────┘                  │
│                               │                                      │
│                    分段 (60s + 4s overlap)                           │
│                    Base64 编码                                       │
│                    WebSocket ──► ASR 推理                            │
│                                    │                                 │
│                              text_merge                              │
│                         ┌──────┴───────┐                             │
│                         │              │                             │
│                    text (简单)   text_accu (精确)                     │
│                         │              │                             │
│                    热词替换          .srt/.json                      │
│                    LLM 润色                                         │
│                         │                                           │
│                    ┌────┴────┐                                       │
│                    │         │                                       │
│                typing     toast                                      │
│              (Ctrl+V)   (浮动窗口)                                    │
│                    │         │                                       │
│                    └────┬────┘                                       │
│                         │                                           │
│                    💾 日记归档                                       │
│                    📡 UDP 广播                                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**CPW-Pro 增补数据流**：URL/拖放 → `cpw_worker` 下载或本地 pick → ffmpeg 16k WAV → （可选）`vad_utils` 切段 → `start_client.exe` 逐段 → 产出 srt/txt/json → `load_transcript` → `timestamp_quality` 诊断日志 → UI 播放/字幕/LLM 笔记。

---

## 八、模块化整理与开源发布建议

### 8.0 模块化原则（本地部署与协作）

- **目标**：他人 clone 后能在本机用虚拟环境 + `requirements.txt` 跑通；改主题、改下载/转写逻辑时找得到文件，而不是为「文件多」而拆。
- **何时拆模块**：有清晰边界且会被复用或单测（子进程、日志→进度纯函数、URL/ANSI、转写线程管线、主题色）；若仅一处使用且不足约数十行，优先留在 `App` 附近，避免协作者跳文件成本上升。
- **主入口**：`python cpw_pro_ui.py` → `main()` 内调用 `apply_ctk_defaults()` 再 `App().mainloop()`；import 模块本身不隐含改全局 CTk 主题（便于将来 `python -m` 或测试里先配环境）。
- **与引擎**：侧车脚本不 import `util/client`，避免与 CapsWriter 核心升级冲突；文档与目录树保持同步。

### 8.1 当前技术债（`cpw_pro_ui.py` 体量）

单一 `App` 类串联：**布局**、**任务与线程**、**日志驱动的进度条**（下载百分比、`[核心]` 发送/转录秒数）、**字幕分页与快捷键**（F9）、**波形示波 Canvas**（`after` 轮询）、**设置/笔记子窗**。适合迭代，但需要继续拆文件以便测试与协作。

### 8.2 渐进式拆分（已完成）

| 模块 | 说明 |
|------|------|
| `cpw_worker.py` | yt-dlp、ffmpeg、`start_server` / `start_client` 子进程与 stdout 行解码 |
| `cpw_progress.py` | `[下载]` 百分比、`[核心]` 发送/转录日志 → 条上比例；`App` 负责节流与 indeterminate |
| `cpw_textutil.py` | `strip_ansi`、`normalize_url`（与 Tk 无关） |
| `cpw_theme.py` | `apply_ctk_defaults`、`scope_canvas_bg_and_stroke`（示波 Canvas 与 CTk 主题一致） |
| `cw_transcribe.py` | 后台线程：`run_*` 管线 + hooks |

### 8.3 建议的下一阶段包结构（不改动引擎 `util/`）

占位命名可按仓库改为 `cpwpro/` 等，避免与 ASR **`models/`** 目录语义冲突：

```text
cpwpro/
  __main__.py          # python -m cpwpro
  worker.py            # 或由根目录 cpw_worker 迁移并 re-export 保持兼容
  ui/
    app.py             # App 外壳：生命周期、全局 bind
    layouts/           # header / left_panel / media / subtitles
    widgets/           # 波形 Canvas、封装型 Progress 等
    dialogs/           # 设置弹窗、_NoteWindow
  services/
    transcription.py    # （已实现根目录 cw_transcribe.py）
    progress_parse.py    # （已实现为根目录 cpw_progress.py）日志 → 进度比例
```

**迁移顺序建议**：纯函数（URL 规整、ANSI 剔除、正则）→ 线程任务入口 → Tk 控件工厂 → 最后再拆巨型 `App.__init__`。

### 8.4 GitHub / 打包 checklist

- 根目录 **`.gitignore`**（瘦身远程仓库）：忽略 `config.json`、`logs/`、`output/`、虚拟环境；另忽略 **`models/**/*.gguf`**、**`models/**/*.onnx`**、`internal/`、根目录 **`start_client.exe`** / **`start_server.exe`**、以及 **`util/llama/bin/*.dll`** / **`*.exe`**（大模型与内嵌运行时改由官方 Release 自备，clone 后从本机发行目录补齐即可）。仓库内保留 **`config.example.json`**。**他人拉仓库后**：首启 GUI 会自动生成 `config.json`（空密钥），在设置里填写即可；也可自行复制示例文件为 `config.json`。**切勿**把含真实密钥的 `config.json` 推送到远程。
- 若历史上曾提交过含密钥的 `config.json`：在云平台**吊销/轮换**该密钥，并视情况用 `git filter-repo` / BFG 清历史（仅改 `.gitignore` 不能从旧 commit 里抹去密钥）。
- `readme.md` 链到本文档；单列 CPW-Pro 依赖（`requirements.txt`）
- 许可证：区分上游 CapsWriter、本 GUI 补丁、第三方模型权重
- 截图含：拖放区、日志进度条、`start_client` 输出、字幕区、波形条
- （可选）CI：`py_compile` / 静态检查，不要求无头跑 GUI

---

*文档版本：2026-05-02 · 合并 CapsWriter 引擎说明与 CPW-Pro 当前实现、模块化路线*  
*工作区：`cpw_pro_ui.py`、`cpw_worker.py`、`cpw_progress.py`、`cpw_textutil.py`、`cpw_theme.py`、`cw_transcribe.py`、引擎 `core_*` / `util/*`*
