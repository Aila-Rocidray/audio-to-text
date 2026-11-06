# audio-to-text（DSH 技能包）

本地离线的音频/视频转文字技能：装进 DSH 后，在会话里说“把这个音频转成文字”，
agent 会自动调用 faster-whisper（CPU int8）完成转写，不联网、不需要 API key、音频不上传。

- 支持中文、英文及多语种自动检测；中英混说可用分段多语种模式
- 输入可以是音频或视频（自动只取音轨），支持批量、目录、通配符
- 输出 `txt` 纯文本、`srt` 字幕、`md` 带时间码文稿、`json` 段/词级结构化数据

---

## 安装（DSH 技能方式）

1. 解压本包，把 `audio-to-text/` 整个目录放到项目的技能目录：
   ```
   <WORKSPACE>/.dsh/skills/audio-to-text/
   ```
2. 把包内文档中的**占位符**替换成你的实际路径（只需改两个 md 文件：

   `SKILL.md`、`references/usage.md`）：

   | 占位符 | 含义 | 示例 |
   | --- | --- | --- |
   | `<DSH_PYTHON>` | DSH 自带 Python 3.12 路径 | `C:\Users\<you>\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe` |
   | `<SKILL_DIR>` | 技能目录 | `D:\my-workspace\.dsh\skills\audio-to-text` |
   | `<WORKSPACE>` | DSH 工作区 | `D:\my-workspace` |
   | `<MODELS>` | 模型目录 | `D:\my-workspace\asr\models` |

   （也可以不改文档，在会话里直接让 agent 用实际路径运行。）
3. 准备引擎和模型（二选一）：

   **A. 复用 DSH 工作区里已有的引擎/模型（推荐，不用 pip）**
   ```
   <WORKSPACE>/asr/pylibs            # faster-whisper 及其依赖
   <WORKSPACE>/asr/models/large-v3   # 至少 1 个 CTranslate2 模型
   ```
   脚本会自动向上查找 `asr/pylibs`、`asr/models`，无需配置。

   **B. 普通 Python 环境**
   ```
   pip install -r requirements.txt
   python scripts/download_models.py     # 默认下载到 <SKILL_DIR>/models/large-v3、medium.en
   ```
   脚本找不到本地 `pylibs` 时会自动使用当前 Python 环境里安装的 faster-whisper；
   模型也可用环境变量 `AUDIO2TEXT_MODELS` 指定。

4. 验证：
   ```powershell
   <DSH_PYTHON> <SKILL_DIR>/scripts/transcribe_audio.py --list-models
   ```
5. 使用：新开 DSH 会话，直接把音频拖进去或给路径，说
   “把这个音频转成文字：D:\audio\xxx.mp3”；也可以显式调用 `/audio-to-text`。
   结果默认输出到 `<WORKSPACE>/transcripts/`。

---

## 命令行用法

```bash
# 自动检测语言（默认）
python scripts/transcribe_audio.py "D:\audio\a.mp3"

# 中文
python scripts/transcribe_audio.py "D:\audio\会议.m4a" --language zh --simplified

# 中英混说
python scripts/transcribe_audio.py "D:\audio\mixed.m4a" --multilingual

# 纯英文提速（medium.en）
python scripts/transcribe_audio.py "D:\audio\lesson.mp3" --fast --language en

# 视频提取音轨 + 批量
python scripts/transcribe_audio.py "D:\audio\video.mp4"
python scripts/transcribe_audio.py "D:\audio\2026-11" --recursive --skip-existing
```

| 参数 | 说明 |
| --- | --- |
| `--output-dir DIR` | 输出目录（默认 `transcripts/`） |
| `--format all\|txt,srt,md,json` | 输出格式，默认 all |
| `--model NAME/PATH` | 模型名或目录（默认 large-v3） |
| `--fast` | 纯英文加速：配合 `--language en` 自动用 medium.en |
| `--language auto\|zh\|en\|…` | 语言，默认自动检测 |
| `--multilingual` | 分段多语种（中英混说） |
| `--simplified` | 中文优先输出简体（提示词引导，可能影响用词） |
| `--vad / --no-vad` | Silero VAD 静音过滤，默认开启 |
| `--initial-prompt` | 引导专有名词/术语写法 |
| `--max-seconds N` | 只转写开头 N 秒 |
| `--recursive` / `--skip-existing` | 目录递归 / 已有输出跳过 |
| `--list-models` / `--dry-run` | 列出模型 / 预览将处理的文件 |

完整说明见 [`references/usage.md`](references/usage.md)。

---

## 包内容

```
audio-to-text/
├─ SKILL.md                    # 技能定义（agent 读取）
├─ README.md                   # 本文件
├─ CHANGELOG.md
├─ scripts/
│  ├─ transcribe_audio.py      # 转写 CLI（主程序）
│  └─ download_models.py       # 从 ModelScope 下载 CTranslate2 模型
├─ references/usage.md         # 完整手册：原理/参数/排错/加模型
├─ transcribe_audio.bat        # Windows 拖拽/双击启动器（DSH 环境）
├─ config.example.json         # 引擎路径覆盖示例
├─ requirements.txt            # 普通 Python 环境依赖（可选）
├─ LICENSE                     # MIT
└─ .gitignore
```

## 许可证

[MIT](LICENSE)。模型与依赖遵循各自上游许可（faster-whisper / CTranslate2 / Whisper / Silero VAD / PyAV）。
