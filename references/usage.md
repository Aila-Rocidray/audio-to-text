# audio-to-text 完整手册

本文件是 `audio-to-text` 技能的详细说明：参数全集、目录结构、引擎定位规则、
输出格式细节、性能数据、故障排查、添加模型与改动记录。
日常使用只看 `README.md` 即可。

---

## 1. 工作原理

```
音频/视频文件
   │  PyAV（av 19，自带 FFmpeg 解码库，无需系统 ffmpeg）
   ▼
16 kHz 单声道 float32 波形
   │  Silero VAD 切出人声片段（可 --no-vad 关闭）
   ▼
CTranslate2 int8 量化的 Whisper large-v3（CPU）
   │  逐段解码 + 词级时间戳
   ▼
分段文本 → 段落合并（停顿 >1.5s 或 >420 字）→ txt / srt / md / json
```

- 全部本地：模型、依赖都在工作区 `asr/`，转写时不联网。
- 引擎版本：faster-whisper 1.2.1 / CTranslate2 4.8.4 / PyAV 19.0.2 / numpy 2.5.4。
- Python：DSH 自带 3.12（`<DSH_PYTHON>`）。
- `asr/pylibs/faster_whisper/audio.py` 打过一次兼容补丁：PyAV 17+ 移除了
  `metadata_errors` 参数，补丁让新旧版本都可用。**不要覆盖这个包。**

## 2. 目录结构

```
<WORKSPACE>\
├─ .dsh\skills\audio-to-text\
│  ├─ SKILL.md                     # DSH 技能定义（会话里自动加载）
│  ├─ scripts\transcribe_audio.py  # CLI 主程序
│  ├─ references\usage.md          # 本文件
│  └─ config.json                  # 可选：引擎路径覆盖
├─ transcribe_audio.bat            # 拖拽/双击启动器
├─ transcripts\                    # 默认输出目录
├─ audio-to-text\                  # 本说明 + 演示
└─ asr\
   ├─ pylibs\                      # faster-whisper 及其依赖（cp312）
   ├─ models\large-v3\             # 多语种模型（默认）
   ├─ models\medium.en\            # 英文快速模型（--fast）
   ├─ download_models.py           # 模型下载脚本（ModelScope，需联网+用户同意）
   └─ out\, tmp\, wheels\ ...      # 历史实验/下载残留，可忽略
```

## 3. 引擎定位规则（找不到 asr 时看这里）

`transcribe_audio.py` 依次查找：

1. 环境变量 `AUDIO2TEXT_LIBS`（指向包含 `faster_whisper/` 的目录）
2. skill 目录下 `config.json` 的 `"libs"` 字段
3. 从脚本位置向上每一级目录下的 `asr/pylibs`、`pylibs`
4. 从当前工作目录向上每一级目录下的 `asr/pylibs`、`pylibs`

模型目录同理（`AUDIO2TEXT_MODELS` / `config.json` 的 `"models"` / `asr/models`）。

`config.json` 示例（路径可写相对 skill 目录的路径）：

```json
{
  "libs": "<WORKSPACE>/asr/pylibs",
  "models": "<WORKSPACE>/asr/models"
}
```

## 4. 参数全集

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `inputs...` | — | 文件、目录、通配符（`"D:\audio\*.mp3"`），可多个 |
| `--output-dir DIR` | `<工作区>/transcripts` | 输出目录，必须可写 |
| `--format` | `all` | `all` 或 `txt,srt,md,json` 组合 |
| `--model` | `large-v3` | 模型名或模型目录绝对路径 |
| `--fast` | 关 | 纯英文加速：需配合 `--language en`，自动选 `medium.en` |
| `--language` | `auto` | `auto`/`zh`/`en`/`ja`/`ko`/`fr`… 也接受 `chinese`、`中文` 等别名 |
| `--multilingual` | 关 | 强制分段多语种：按停顿切窗、逐窗识别语言（中英混说用） |
| `--simplified` | 关 | 中文输出偏好简体（提示词引导，可能影响用词，见 5 节） |
| `--device` | `cpu` | 有 GPU 且装了 CUDA 版 ct2 时可 `cuda` |
| `--compute-type` | `int8` | CPU 推荐 `int8`；`float32` 更准但慢 2–3 倍 |
| `--threads` | 0（自动） | CPU 线程数 |
| `--beam-size` | 5 | 越大越准越慢 |
| `--vad` / `--no-vad` | 开 | Silero VAD 过滤静音；纯音乐/噪声大时关闭 |
| `--words` / `--no-words` | 开 | 词级时间戳（json 里 `words` 字段） |
| `--condition-on-previous-text` | 关 | 长文更连贯，但可能放大误识别 |
| `--initial-prompt` | 无 | 用提示词引导专有名词写法 |
| `--max-seconds N` | 0（全部） | 只转写开头 N 秒 |
| `--recursive` | 关 | 递归扫描输入目录 |
| `--skip-existing` | 关 | 目标输出已存在则跳过该文件 |
| `--keep-partial` | 关 | 保留 `.partial.txt` 中间草稿 |
| `--quiet` | 关 | 不逐段打印（长文件后台运行时可用） |
| `--list-models` | — | 列出 `asr/models` 下的模型 |
| `--dry-run` | — | 只打印将处理哪些文件、用哪个模型 |
| `--version` / `--help` | — | 版本 / 帮助 |

## 5. 语言识别策略

默认（`--language auto`）分两步：

1. **整段预判**：解码开头 30 秒，用 Whisper 的语言检测；置信度 ≥ 62% 时按单语种
   整段转写（最快、上下文最连贯）。
2. **低置信度兜底**：置信度 < 62% 时自动切换到「分段多语种模式」——
   - Silero VAD 找出语音段，在 ≥0.8–1.2 秒的停顿处切窗（每窗 ≤30 秒）；
   - 逐窗检测语言；若置信度仍低，用「强制中文/英文各转写一遍」校验：
     中文会输出汉字、英文音频强制中文通常仍输出英文单词，据此判定 zh / en；
   - 每窗用判定的语言转写，时间戳按窗口偏移合并。

其他模式：

| 需求 | 用法 |
| --- | --- |
| 已知语言（最稳） | `--language zh` / `--language en`，跳过检测 |
| 中英混说/多语种 | `--multilingual`（强制分段）；默认低置信度时也会自动进入 |
| 中文输出简体 | `--language zh --simplified`（提示词引导；见下方注意） |
| 纯英文提速 | `--language en --fast`（medium.en，约快 2 倍） |

**关于 `--simplified` 的注意**：它靠改写提示词引导模型输出简体，会同时影响用词和
断句。实测在短音频/安静录音上，开启前后同一句话可能识别成不同内容（例如
「希望這個視頻錄出來是有聲音的」→「希望这个视频录取了一些有趣的内容。」）。
因此建议：只在需要简体时开启；重要内容人工核对；对稳定性要求高时更推荐先按
原文转写、再用 OpenCC 等工具做繁简转换（本工具不内置转换）。

**关于 `--skip-existing`**：若本次所有输入的目标输出都已存在，脚本会直接跳过、
不加载模型（秒级返回）；只有部分存在时仍需加载模型。

**已知局限**：单语种模式对同一句话里频繁中英切换的支持有限（可能把英文部分翻成
中文）；此时用 `--multilingual`，或按语言分段处理。仅 1–2 秒的短语音检测容易不稳，
已知语言时请直接 `--language`。

## 6. 输出细节

### txt
逐段文本按停顿合并成自然段；中文之间的多余空格会被清理，英文单词之间的空格保留。
不包含时间码，适合直接阅读、二次编辑。

### srt
```
1
00:00:04,380 --> 00:00:06,580
Hello. Come in and take a seat.
```
段级时间戳；空文本段会被跳过。

### md
文件头表格（源文件、时长、识别语言与置信度、模型、生成时间）+ 正文，
每个自然段前有 `[时:分:秒]`。

### json
```json
{
  "audio": "D:\\rec\\a.mp3",
  "duration_sec": 195.2,
  "processed_duration_sec": 195.2,
  "language": "en",
  "language_name": "英语",
  "language_probability": 0.99,
  "languages": null,
  "mode": "single",
  "detection": {"mode": "single", "language": "en", "probability": 0.99, "note": "detect"},
  "model": "large-v3",
  "elapsed_sec": 214.6,
  "real_time_factor": 1.1,
  "segments": [
    {"id": 0, "start": 4.38, "end": 6.58, "text": "Hello. Come in and take a seat.",
     "avg_logprob": -0.23, "no_speech_prob": 0.05,
     "words": [{"w": " Hello", "s": 4.38, "e": 4.7, "p": 0.98}]}
  ]
}
```

多语种模式下 `language` 形如 `en+zh`（语言代码按字母排序），`languages` 是各窗口
实际语言列表，`mode` 为 `multilingual`；`segments[].id` 为全局唯一序号。
`processed_duration_sec` 是实际转写的音频长度（使用 `--max-seconds` 时小于总时长）。

`run-summary.json` 汇总一次运行：`ok/failed/skipped`、`missing_inputs`、每个
成功文件的时长/语言/段数/耗时/输出路径。`skipped` 条目只含音频路径、输出文件与
跳过状态（没有耗时段落信息）。

## 7. 性能参考（本机 CPU，无 GPU，int8 量化）

| 任务 | 模型/模式 | 音频时长 | 实际耗时 | 速度 |
| --- | --- | --- | --- | --- |
| 真实中文讲解视频 `zh_real.wav` | large-v3 单语种 | 122s | 77s | 1.6x 实时 |
| 英文 CD 音轨 `CD1-Track01.mp3` | medium.en（`--fast`） | 195s | 71s | 2.7x 实时 |
| 中英混说 `mixed_split.wav` | large-v3 分段多语种 | 27s | 43s | 0.6x 实时 |
| 中文短音频 `zh_sample.wav` | large-v3 强制 zh | 6s | 10s | 0.6x 实时 |

规律：
- 长音频约 **1–1.6x 实时**（large-v3 int8），`--fast` 英文约 **2.5–3x 实时**。
- 短音频/分段模式有固定的检测与加载开销，倍率会显得慢；模型加载约 5–10s，批量只加载一次。
- 分段多语种模式每窗多一次语言检测（+10–30% 时间）；只在混说或低置信度时使用。
- 超过 10 分钟的音频请用后台任务运行，脚本会逐段输出进度，并写 `.partial.txt` 兜底。

## 8. 故障排查

| 现象 | 原因/处理 |
| --- | --- |
| `找不到转写引擎（faster-whisper）` | `asr/pylibs` 被移动；设 `AUDIO2TEXT_LIBS` 或写 `config.json` |
| `找不到模型` | `asr/models/<模型名>` 不存在；`--list-models` 查看；默认需要 large-v3 |
| `无法加载 faster-whisper` + `ModuleNotFoundError` | 用了错误 Python（如系统 3.13）；改用 DSH 自带 3.12 |
| `模型加载失败` / `Could not load library ctranslate2.dll` | `asr/pylibs` 不完整；重装 faster-whisper 到该目录 |
| `输出目录不可写 PermissionError` | DSH 沙箱限制；`--output-dir` 指到工作区内 |
| 语言检测错误（中文被判成英文） | 已知语言时直接 `--language zh`；自动模式下低置信度会自动分段识别 |
| 中文输出是繁体字 | 加 `--simplified`（可能影响用词，重要内容需核对）；或用 OpenCC 后处理 |
| 中英混说时英文被翻成中文 | 加 `--multilingual`；或 `--language en` 以英文为主处理 |
| 1–2 秒短语音识别乱 | 短音频语言检测不稳；给 `--language zh/en` 并确保音频有足够语音 |
| 静音段出现重复句子/幻觉 | 可试 `--no-vad`（去掉 VAD 的拼接影响）或反之；`--condition-on-previous-text` 一般别开 |
| 专有名词总写错 | `--initial-prompt "张三, XX产品"`；或在后处理里替换 |
| 音频含歌曲/音乐，识别乱 | 正常现象；VAD 只能过滤静音，不能过滤音乐 |
| 视频没有声音 → 报错或无输出 | 视频确实无音轨；确认源文件 |
| 处理时间过长 | 用 `--fast --language en`（纯英文）、`--no-words`、`--max-seconds` 预览 |
| 想中断 | Ctrl+C；已转内容在 `<stem>.partial.txt`，不会丢 |

## 9. 添加模型（需用户确认并联网）

模型目录格式：CTranslate2 模型，至少包含 `model.bin` + `config.json` +
`tokenizer.json`/`vocabulary.*`。现有下载脚本 `asr/download_models.py` 从
ModelScope 拉取 `pengzhendong/faster-whisper-*`：

```powershell
$py = "...\python.exe"
& $py -X utf8 "<WORKSPACE>/asr\download_models.py"
```

加新模型时：

1. 在 `asr/download_models.py` 的 `REPOS` 里加 `"名字": "pengzhendong/faster-whisper-名字"`；
2. 下载到 `asr/models/名字/`；
3. `--list-models` 确认后，`--model 名字` 使用。

可用 CTranslate2 模型的命名参考：`tiny`、`base`、`small`、`medium`、
`large-v2`、`large-v3`、`medium.en`、`small.en` 等。
CPU 上 `small`/`medium` 更快但中文准确率明显低于 `large-v3`。

## 10. 说话人分离

当前 CLI **不做**说话人分离。`asr/diarize.py`、`asr/chunkize.py` 是早期实验：
基于音高+频谱质心的 2 人聚类，只适合干净的双人录音（如教材对话）。
会议/采访等场景建议用 pyannote-audio 或云端说话人分离服务，再接本工具转写。

## 11. 修改与维护记录

- 2026-11-01：基于 `asr/` 里 CD 转写实验的资产，封装为 `audio-to-text` 技能 +
  `transcribe_audio.py` 统一 CLI（批量、多格式、部分输出、运行摘要）+
  `transcribe_audio.bat` 启动器。
- 2026-11-01（同日增强）：语言检测低置信度自动切换分段多语种模式；新增
  `--multilingual`（按停顿切窗逐窗识别语言）与 `--simplified`（简体中文引导）；
  修复 `--max-seconds` 下的速度统计；补充边界错误提示。
- 2026-11-01（独立验收后修复）：多语种 JSON 段 id 全局唯一；`language_name`
  不再把单语种分段结果标成「多语种」；`--max-seconds` 增加
  `processed_duration_sec` 并在摘要标注；`--skip-existing` 全量命中时不再加载模型；
  部分输入缺失时以 exit 1 结束；补 `.m4b` 等扩展名；`--simplified` 增加风险提示。
- `asr/pylibs/faster_whisper/audio.py` 的 PyAV 兼容补丁不要覆盖。
- 输出目录默认跟随工作区 `transcripts/`；更换工作区时用 `--output-dir` 或 `config.json`。
