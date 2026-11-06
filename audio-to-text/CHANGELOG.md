# Changelog

## 2026-11-07

- 代码冗余清理：删除未使用常量/死分支/空 try、重复的有效时长公式、重复的去重与
  语言标签逻辑；统一 CJK 字符范围与比率计算；多语种段 id 只统一编号一次。
- 独立环境支持：找不到本地 `asr/pylibs` 时自动使用当前 Python 环境已安装的
  faster-whisper（适配 `pip install -r requirements.txt` 的用法）。
- 文档同步：修正模型下载脚本路径、补充独立仓库/技能包两种部署布局说明。
- 验证：47 项回归检查（中/英/混说、批量 skip、错误路径、bat、JSON/SRT 结构等）全部通过。

## 2026-11-01

- 首个版本：基于 faster-whisper + Whisper large-v3（CPU int8）的本地转写 CLI，
  封装为 DSH 技能 `audio-to-text`。
- 支持中英多语种自动检测、`--multilingual` 分段多语种、`--simplified` 简体引导、
  `--fast` 英文加速、批量/目录/通配符、txt/srt/md/json 输出、`.partial.txt` 兜底。
- 输出语言检测低置信度时自动切换分段识别；`--skip-existing` 全量命中时不加载模型。
