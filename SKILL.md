---
name: audio-to-text
description: 本地离线把音频/视频转成文字（中英及多语种），支持批量、时间戳、txt/srt/md/json 输出。用户要求转写音频、提取语音文字、生成字幕或整理录音原文时使用。
whenToUse: 用户提供音频或视频文件（mp3/wav/m4a/mp4/mkv 等），要求转成文字、提取原文、生成字幕、整理录音/会议/课程内容时。
---

# 音频转文字（audio-to-text）

本地离线转写引擎（faster-whisper + 已下载的 large-v3 模型），不联网、不需要 API key。
输入可以是音频、视频（自动只取音轨）、目录或通配符，支持中文/英文/多语种自动检测。

## 一条命令完成

```powershell
& "<DSH_PYTHON>" `
  "<SKILL_DIR>/scripts/transcribe_audio.py" `
  "D:\path\to\audio.mp3"
```

默认输出到 `<WORKSPACE>/transcripts\<文件名>.{txt,srt,md,json}`。
**必须用上面这个 DSH 自带 Python 3.12**，系统里的其他 Python 没有转写依赖。

## 工作流程

1. **确认输入**：先确认音频文件存在（`Test-Path`）。用户只给文件名时，在工作区、`D:\audio`、用户指定目录中查找。
2. **选择参数**（默认值通常够用）：
   - 语言未知 → 保持 `--language auto`（默认，自动检测；置信度低时会自动按停顿分段逐窗识别）。
   - 已知纯中文 → `--language zh`；纯英文 → `--language en`。
   - 中英混说/多语种 → 加 `--multilingual`（按停顿切窗、逐窗识别语言）。
   - 中文希望输出简体 → 加 `--simplified`（配合中文音频；默认可能输出繁体。注意：提示词引导可能影响用词，重要内容需核对）。
   - 纯英文且想更快 → 加 `--fast --language en`（用 medium.en，约快 2 倍）。
   - 长音频（>10 分钟）→ 保持默认 large-v3，**必须后台运行**（见下文）。
   - 长音频想更快 → 加 `--batch-size 16`（批量推理：VAD 切段后并行解码；本机 CPU 实测 2 分钟音频 95.7s → 77.2s，约 1.2 倍；需 VAD，会自动启用）。
   - 专有名词/人名多 → `--initial-prompt "张三, 产品名, XXX"`。
   - 只要开头 N 秒预览 → `--max-seconds 60`。
   - 批量目录 → 传目录并加 `--recursive --skip-existing`。
3. **运行**：估计耗时 ≈ 音频时长 × 0.6–1.0（large-v3 CPU；实测 2 分钟中文约 1.5 分钟）或 × 0.35（`--fast` 英文）。
   - ≤ 3 分钟音频：直接同步运行（超时设 600000ms）。
   - 更长音频：用后台 job 运行，轮询 `job_output` 查看逐段进度，不要重复启动。
   - 音频文件可以在工作区外（读取通常允许），但输出目录必须在工作区内（沙箱限制）。
4. **读取结果**：读 `<output-dir>/<stem>.txt`（纯文本）向用户汇报摘要；文件路径全部给出。
   - 用户要字幕 → `.srt`；要带时间码的文稿 → `.md`；要程序处理 → `.json`（段/词级时间戳）。
   - 多个文件会有 `run-summary.json` 汇总（路径、时长、语言、段数、耗时、输出文件）。
5. **汇报格式**：文件名、识别语言、时长、段数、耗时、输出文件路径；正文过长时给摘要 + 首段，不要整篇贴出（除非用户要求）。

## 常用命令示例

```powershell
$py = "<DSH_PYTHON>"
$tool = "<SKILL_DIR>/scripts/transcribe_audio.py"

# 单个中文音频，输出到工作区 transcripts/
& $py $tool "D:\audio\会议.m4a" --language zh

# 中英混说/多语种音频
& $py $tool "D:\audio\mixed.m4a" --multilingual

# 中文希望简体输出
& $py $tool "D:\audio\中文录音.mp3" --language zh --simplified

# 纯英文提速
& $py $tool "D:\audio\lesson.mp3" --fast --language en

# 视频提取音频转文字
& $py $tool "D:\audio\interview.mp4" --format txt,srt

# 批量：目录 + 递归 + 跳过已有结果
& $py $tool "D:\audio\2026-11" --recursive --skip-existing

# 只转写前 2 分钟做预览
& $py $tool "D:\audio\long.mp3" --max-seconds 120 --format txt

# 长音频批量推理加速（约 1.2 倍）
& $py $tool "D:\audio\long.mp3" --batch-size 16
```

## 输出文件

| 文件 | 内容 |
| --- | --- |
| `<stem>.txt` | 纯文本正文，按自然停顿分段（最常用） |
| `<stem>.srt` | SRT 字幕（段级时间戳） |
| `<stem>.md` | 带元信息和时间码的文稿 |
| `<stem>.json` | 完整结构化数据：语言、时长、逐段文本、词级时间戳、置信度 |
| `run-summary.json` | 本批次汇总（成功/失败/跳过、耗时、输出路径） |

## 关键参数

| 参数 | 说明 |
| --- | --- |
| `--output-dir DIR` | 输出目录（默认工作区 `transcripts/`；必须可写，不要指向工作区外） |
| `--format all\|txt,srt,md,json` | 输出格式，默认 all |
| `--model large-v3` | 默认多语种模型；`--fast` 用 medium.en（仅英文） |
| `--language auto\|zh\|en\|ja...` | 语言；默认自动检测 |
| `--multilingual` | 分段多语种：按停顿切窗逐窗识别语言，适合中英混说 |
| `--simplified` | 中文优先输出简体（提示词引导，可能影响用词） |
| `--vad / --no-vad` | 静音过滤，默认开启；纯音乐/噪声大时试 `--no-vad` |
| `--initial-prompt "词1, 词2"` | 引导专有名词写法 |
| `--batch-size N` | 批量推理加速（建议 8–16；CPU 约快 1.1–1.25 倍）；0=关闭，默认 0；`--multilingual` 下不生效 |
| `--max-seconds N` | 只处理开头 N 秒 |
| `--no-words` | 不要词级时间戳（更快、json 更小） |
| `--skip-existing` | 已有输出则跳过 |
| `--dry-run` | 只显示将要处理什么 |
| `--list-models` | 列出本地模型 |

## 常见问题

- **报“找不到转写引擎”**：`asr/pylibs` 被移动/改名。设置环境变量 `AUDIO2TEXT_LIBS` 指向 `asr/pylibs`，或在 skill 目录写 `config.json`（`{"libs": "...", "models": "..."}`）；独立 Python 环境也可 `pip install -r requirements.txt` 后直接使用已安装的包。
- **报“找不到模型”**：默认需要 `asr/models/large-v3`；用 `--list-models` 查看。不要联网下载。
- **输出目录不可写**：DSH 沙箱只允许写工作区，`--output-dir` 必须放在 `<WORKSPACE>` 下（默认即可）。
- **自动识别语言不准（短音频/安静录音常见）**：已知语言就强制 `--language zh` / `--language en`；中文输出繁体时加 `--simplified`。
- **中英混说**：加 `--multilingual`；若结果里英文部分被翻成中文，说明整段被判成单一中文，可改用 `--language en` 或分段处理。
- **识别出重复句/幻觉**：可试 `--no-vad`（去掉 VAD 拼接影响）或反之；`--condition-on-previous-text` 一般别开；仍不行时用 `--max-seconds` 分段排查。
- **`--batch-size` 更慢或报内存错误**：批越大内存占用越高；报 `mkl_malloc`/内存不足就去掉参数或降到 8（程序会自动回退普通转写并告警）。CPU 上收益约 1.1–1.25 倍，短音频（<1 分钟）不建议开。
- **要加新模型**：只能由用户确认后下载到 `asr/models/<名>`（见 `references/usage.md`），不要在任务中擅自联网。
- **不要改 `asr/pylibs` 里的包**（其中 `faster_whisper/audio.py` 已针对新版 PyAV 打过兼容补丁）。

语言模式说明：默认先检测整段语言，置信度高（≥62%）就单语种转写；置信度低会自动切到“按停顿分段、逐窗识别语言”的多语种模式。听到“中英混说”时优先主动加 `--multilingual`。

## 交付习惯

- 不要粘贴超长转写正文；给出摘要 + 关键段落 + 文件路径。
- 提供可直接打开的文件链接/路径（txt 或 md）。
- 用户确认后，再决定是否需要 srt / 校对 / 翻译等后续动作。
