# audio-to-text

本地离线音频/视频转文字工具，并附带一个 [DSH](https://deepseek-harness.com) 技能定义。
基于 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + Whisper large-v3，
在 CPU 上以 int8 量化运行：**不联网、不需要 API key、音频不上传**。

- 支持中文、英文及多语种自动检测；中英混说可用分段多语种模式
- 输入可以是音频或视频（自动只取音轨），支持批量、目录、通配符
- 输出 `txt` 纯文本、`srt` 字幕、`md` 带时间码文稿、`json` 段/词级结构化数据
- 长音频边转写边输出，中断会自动保留 `.partial.txt` 草稿

---

## 快速开始（普通 Python 环境）

### 1. 环境

- Python **3.10–3.12**（本机实测 3.12）
- 无需安装 ffmpeg：解码使用 PyAV 自带的 FFmpeg 库

```bash
pip install -r requirements.txt
```

### 2. 准备模型

需要 CTranslate2 格式的 Whisper 模型（默认 `models/large-v3`，至少包含
`model.bin`、`config.json`、`tokenizer.json`/`vocabulary.*`）：

```bash
# 方式一：从 ModelScope 下载（默认下载 large-v3 和 medium.en）
python scripts/download_models.py

# 方式二：自行下载/转换后放到 models/<模型名>/
#   large-v3 约 2.9 GB，medium.en 约 1.4 GB
```

下载目录可用环境变量 `AUDIO2TEXT_MODELS` 指定。

### 3. 运行

```bash
# 中文音频（默认自动检测语言）
python scripts/transcribe_audio.py "D:\audio\会议.m4a" --language zh

# 中英混说
python scripts/transcribe_audio.py "D:\audio\mixed.m4a" --multilingual

# 纯英文提速（medium.en）
python scripts/transcribe_audio.py "D:\audio\lesson.mp3" --fast --language en

# 视频提取语音
python scripts/transcribe_audio.py "D:\audio\video.mp4"

# 批量目录 + 跳过已有结果
python scripts/transcribe_audio.py "D:\audio\2026-11" --recursive --skip-existing
```

默认输出到 `./transcripts/<文件名>.{txt,srt,md,json}`，可用 `--output-dir` 指定。

---

## 在 DSH 中使用（技能方式）

把本仓库目录放到项目的技能根目录即可：

```bash
# 例如克隆到 DSH 工作区的项目级技能目录
git clone <this-repo> "<WORKSPACE>/.dsh/skills/audio-to-text"
```

之后在 DSH 会话里说“把这个音频转成文字：D:\audio\xxx.mp3”，agent 会自动加载
[`SKILL.md`](SKILL.md) 并调用本工具。

注意：
- [`SKILL.md`](SKILL.md) 与 [`references/usage.md`](references/usage.md) 中出现的
  `<DSH_PYTHON>`、`<SKILL_DIR>`、`<WORKSPACE>`、`<MODELS>` 是占位符，请按实际环境替换
  （DSH 自带 Python 3.12 路径、技能目录、工作区目录、模型目录）。
- 引擎路径默认按 `<工作区>/asr/pylibs`、`<工作区>/asr/models` 解析；也可通过环境变量
  `AUDIO2TEXT_LIBS` / `AUDIO2TEXT_MODELS`，或技能目录下 `config.json`（参考
  [`config.example.json`](config.example.json)）覆盖。
- DSH 沙箱只允许写工作区，输出目录要放在工作区内；长音频建议后台运行。

---

## 参数速查

| 参数 | 说明 |
| --- | --- |
| `--output-dir DIR` | 输出目录（默认 `./transcripts`） |
| `--format all\|txt,srt,md,json` | 输出格式，默认 all |
| `--model NAME/PATH` | 模型名或模型目录（默认 large-v3） |
| `--fast` | 纯英文加速：配合 `--language en` 自动用 medium.en |
| `--language auto\|zh\|en\|ja…` | 语言，默认自动检测 |
| `--multilingual` | 分段多语种：按停顿切窗、逐窗识别语言（中英混说） |
| `--simplified` | 中文优先输出简体（提示词引导，可能影响用词） |
| `--vad / --no-vad` | Silero VAD 静音过滤，默认开启 |
| `--initial-prompt "词1, 词2"` | 引导专有名词/术语写法 |
| `--max-seconds N` | 只转写开头 N 秒 |
| `--recursive` / `--skip-existing` | 目录递归 / 已有输出则跳过 |
| `--dry-run` / `--list-models` | 预览将处理的文件 / 列出本地模型 |

完整参数与内部原理见 [`references/usage.md`](references/usage.md)。

支持输入：mp3 / wav / m4a / m4b / aac / flac / ogg / opus / wma / amr / aiff …
以及 mp4 / mkv / mov / webm / avi / wmv / flv / ts …（视频自动取音轨）。

输出文件：

| 文件 | 内容 |
| --- | --- |
| `<stem>.txt` | 纯文本正文，按自然停顿分段 |
| `<stem>.srt` | SRT 字幕 |
| `<stem>.md` | 带元信息和时间码的文稿 |
| `<stem>.json` | 段级/词级结构化数据（语言、时长、时间戳、置信度） |
| `run-summary.json` | 本批次汇总（成功/失败/跳过、耗时、输出路径） |

---

## 性能参考

本机（CPU，无 GPU，int8）实测：

| 任务 | 模型/模式 | 音频时长 | 耗时 | 速度 |
| --- | --- | --- | --- | --- |
| 中文讲解视频 | large-v3 单语种 | 122s | 77–88s | 1.4–1.6x 实时 |
| 英文 CD 音轨 | medium.en（`--fast`） | 195s | 71–74s | 2.6–2.7x 实时 |
| 中英混说 | large-v3 分段多语种 | 27s | 35–43s | 0.6–0.8x 实时 |

规律：large-v3 约 **0.6–1 倍音频时长**，`--fast` 英文约 **2.5–3 倍实时**。
超过 10 分钟的音频建议后台运行。

---

## 目录结构

```
audio-to-text/
├─ SKILL.md                  # DSH 技能定义（agent 会读取并使用）
├─ scripts/
│  ├─ transcribe_audio.py    # 转写 CLI（主程序）
│  └─ download_models.py     # 从 ModelScope 下载 CTranslate2 模型
├─ references/
│  └─ usage.md               # 完整手册：原理、参数、排错、加模型
├─ transcribe_audio.bat      # Windows 拖拽/双击启动器（DSH 环境）
├─ config.example.json       # 引擎路径覆盖示例
├─ LICENSE
└─ requirements.txt
```

## 常见问题

- **找不到引擎 / 模型**：确认 `asr/pylibs`、`asr/models/<模型名>` 存在，或设置
  `AUDIO2TEXT_LIBS` / `AUDIO2TEXT_MODELS` / `config.json`。
- **语言识别不准（短音频、安静录音）**：已知语言时用 `--language zh/en`；
  中文输出繁体可加 `--simplified`。
- **中英混说**：加 `--multilingual`。
- **重复句/幻觉**：可试 `--no-vad` 或反之，`--condition-on-previous-text` 一般别开。
- **模型/依赖太大**：默认 large-v3 约 2.9 GB；纯英文可用 `medium.en`（1.4 GB）提速。
- **不要手动修改依赖包内部文件**（如 `faster_whisper/audio.py` 的 PyAV 兼容补丁）。

## 许可证

[MIT](LICENSE)

## 致谢

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [CTranslate2](https://github.com/OpenNMT/CTranslate2)
- [OpenAI Whisper](https://github.com/openai/whisper) large-v3 模型
- [Silero VAD](https://github.com/snakers4/silero-vad)
- PyAV（FFmpeg 解码）
