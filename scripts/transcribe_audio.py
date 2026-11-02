#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio-to-text —— 本地离线音频/视频转文字（faster-whisper，CPU int8）。

特点：
  * 完全本地运行：不联网、不需要 API key、不上传音频。
  * 多语种：默认 large-v3 模型，中/英/日/韩等自动检测；纯英文可用 --fast。
  * 任意音视频格式：mp3/wav/m4a/flac/ogg/opus/wma/amr/mp4/mkv/mov/webm...
  * 批量与目录：可一次传入多个文件、目录（--recursive）或通配符。
  * 输出：txt（纯文本）/ srt（字幕）/ md（带时间码）/ json（段级结构化）。
  * 长音频：边转写边输出（.partial.txt 兜底），中断不丢已转内容。

依赖放置在工作区的 asr/ 目录：
  asr/pylibs            faster-whisper 及其依赖（用 DSH 自带 Python 3.12 运行）
  asr/models/<name>     本地模型（默认 large-v3，另备 medium.en）

路径可用环境变量覆盖：
  AUDIO2TEXT_LIBS   指向 asr/pylibs
  AUDIO2TEXT_MODELS 指向 asr/models
"""

from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
DEFAULT_MODEL = "large-v3"
FAST_MODEL = "medium.en"

FORMATS = ("txt", "srt", "md", "json")
AUDIO_VIDEO_EXTS = {
    # audio
    ".mp3", ".wav", ".m4a", ".m4b", ".m4p", ".aac", ".flac", ".ogg", ".oga",
    ".opus", ".wma", ".amr", ".aiff", ".aif", ".caf", ".au", ".ac3", ".dts",
    ".mp2", ".mka", ".mpga",
    # video (只取音轨)
    ".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".ts",
    ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp", ".3g2", ".vob", ".asf", ".rm",
    ".rmvb", ".ogv", ".divx",
}
CJK_RANGE = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
CJK_ONLY_SPACE = re.compile(rf"(?<=[{CJK_RANGE}])\s+(?=[{CJK_RANGE}])")
MULTI_SPACE = re.compile(r"[ \t]{2,}")

LANG_NAMES = {
    "zh": "中文", "en": "英语", "ja": "日语", "ko": "韩语", "fr": "法语",
    "de": "德语", "es": "西班牙语", "ru": "俄语", "pt": "葡萄牙语",
    "it": "意大利语", "ar": "阿拉伯语", "th": "泰语", "vi": "越南语",
    "id": "印尼语", "yue": "粤语", "hi": "印地语", "tr": "土耳其语",
}
LANG_ALIASES = {
    "auto": None, "detect": None, "": None,
    "zh": "zh", "cn": "zh", "chi": "zh", "chinese": "zh", "中文": "zh",
    "zh-cn": "zh", "zh_cn": "zh", "zh-hans": "zh", "普通话": "zh", "汉语": "zh",
    "en": "en", "eng": "en", "english": "en", "英文": "en", "en-us": "en",
    "en-gb": "en",
    "ja": "ja", "jp": "ja", "japanese": "ja", "日文": "ja", "日语": "ja",
    "ko": "ko", "korean": "ko", "韩语": "ko",
    "yue": "yue", "cantonese": "yue", "粤语": "yue",
}

SKILL_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# 输出工具
# --------------------------------------------------------------------------
def log(msg: str = "") -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fmt_clock(seconds: float) -> str:
    """02:03 / 1:02:03"""
    seconds = max(0.0, float(seconds or 0))
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_srt_time(seconds: float) -> str:
    ms = max(0, int(round(float(seconds or 0) * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def clean_text(text: str) -> str:
    """去掉多余空白，并清理中文之间的空格；英文单词间空格保留。"""
    text = (text or "").strip()
    text = CJK_ONLY_SPACE.sub("", text)
    text = MULTI_SPACE.sub(" ", text)
    return text


def join_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if re.search(rf"[{CJK_RANGE}]$", left) and re.match(rf"[{CJK_RANGE}]", right):
        return left + right
    return left + " " + right


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------
# 引擎（asr/pylibs + asr/models）定位
# --------------------------------------------------------------------------
def _dedupe_paths(paths):
    seen, out = set(), []
    for p in paths:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _search_roots():
    roots = []
    roots.extend(Path(__file__).resolve().parents)
    cwd = Path.cwd()
    try:
        roots.extend([cwd, *cwd.parents])
    except OSError:
        pass
    return _dedupe_paths(roots)


def _load_skill_config() -> dict:
    cfg_path = SKILL_ROOT / "config.json"
    if not cfg_path.is_file():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 配置损坏时不应挡住主流程
        warn(f"提示：无法解析 {cfg_path}（{exc}），忽略该配置。")
        return {}


def resolve_engine() -> dict:
    """返回 {'libs': Path|None, 'models': Path|None, 'workspace': Path|None}。"""
    config = _load_skill_config()

    cfg_libs = config.get("libs") or config.get("libs_dir")
    cfg_models = config.get("models") or config.get("models_dir")
    if cfg_libs and not Path(str(cfg_libs)).is_absolute():
        cfg_libs = (SKILL_ROOT / str(cfg_libs)).resolve()
    if cfg_models and not Path(str(cfg_models)).is_absolute():
        cfg_models = (SKILL_ROOT / str(cfg_models)).resolve()

    env_libs = os.environ.get("AUDIO2TEXT_LIBS")
    env_models = os.environ.get("AUDIO2TEXT_MODELS")

    roots = _search_roots()

    lib_candidates = []
    for item in (env_libs, cfg_libs):
        if item:
            lib_candidates.append(Path(str(item)).expanduser())
    for root in roots:
        lib_candidates.append(root / "asr" / "pylibs")
        lib_candidates.append(root / "pylibs")
    libs = None
    for cand in _dedupe_paths(lib_candidates):
        if (cand / "faster_whisper" / "__init__.py").is_file():
            libs = cand
            break

    model_candidates = []
    for item in (env_models, cfg_models):
        if item:
            model_candidates.append(Path(str(item)).expanduser())
    for root in roots:
        model_candidates.append(root / "asr" / "models")
        model_candidates.append(root / "models")
    models = None
    for cand in _dedupe_paths(model_candidates):
        if cand.is_dir():
            models = cand
            break

    workspace = None
    if libs is not None:
        for root in roots:
            if root / "asr" / "pylibs" == libs:
                workspace = root
                break
        if workspace is None and libs.name.lower() == "pylibs" and libs.parent.name.lower() == "asr":
            workspace = libs.parent.parent
    if workspace is None and models is not None:
        for root in roots:
            if root / "asr" / "models" == models:
                workspace = root
                break
        if workspace is None and models.name.lower() == "models" and models.parent.name.lower() == "asr":
            workspace = models.parent.parent

    return {"libs": libs, "models": models, "workspace": workspace}


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------
def list_local_models(models_dir: Path | None):
    models = []
    if not models_dir or not models_dir.is_dir():
        return models
    for entry in sorted(models_dir.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir() and (entry / "model.bin").is_file():
            models.append(entry)
    return models


def normalize_model_spec(spec: str) -> str:
    spec = (spec or "").strip()
    if not spec:
        return DEFAULT_MODEL
    p = Path(spec).expanduser()
    if p.is_dir() and (p / "model.bin").is_file():
        return str(p)
    mapping = {
        "large": "large-v3", "large_v3": "large-v3", "largev3": "large-v3",
        "large-v3": "large-v3", "large-v2": "large-v2",
        "medium": "medium", "medium.en": "medium.en", "medium-en": "medium.en",
        "medium_en": "medium.en", "small": "small", "base": "base", "tiny": "tiny",
        "默认": DEFAULT_MODEL,
    }
    return mapping.get(spec.lower(), spec)


def resolve_model(spec: str, models_dir: Path | None) -> Path | None:
    spec = normalize_model_spec(spec)
    p = Path(spec).expanduser()
    if p.is_dir() and (p / "model.bin").is_file():
        return p.resolve()
    if models_dir and models_dir.is_dir():
        cand = models_dir / spec
        if cand.is_dir() and (cand / "model.bin").is_file():
            return cand
        lowered = spec.lower()
        for entry in models_dir.iterdir():
            if entry.is_dir() and entry.name.lower() == lowered and (entry / "model.bin").is_file():
                return entry
    return None


def pick_fast_model(models_dir: Path | None, requested: str) -> str:
    """--fast：纯英文优先 medium.en，其次 small；都没有则用原模型。"""
    available = {m.name.lower() for m in list_local_models(models_dir)}
    for name in ("medium.en", "small.en", "small", "base", "tiny"):
        if name in available:
            return name
    return requested


# --------------------------------------------------------------------------
# 输入与语言
# --------------------------------------------------------------------------
def is_audio_like(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_VIDEO_EXTS


def expand_inputs(raw_inputs, recursive: bool):
    files, missing = [], []
    for item in raw_inputs:
        item = str(item)
        if any(ch in item for ch in "*?["):
            matches = sorted(globlib.glob(item, recursive=recursive))
            if not matches:
                missing.append(item)
            candidates = [Path(m) for m in matches]
        else:
            p = Path(item).expanduser()
            if not p.exists():
                missing.append(item)
                continue
            candidates = [p]

        for cand in candidates:
            if cand.is_dir():
                pattern = "**/*" if recursive else "*"
                for f in sorted(cand.glob(pattern)):
                    if f.is_file() and is_audio_like(f):
                        files.append(f)
                if not recursive:
                    warn(
                        f"提示：{cand} 是目录，只扫描了第一层；"
                        f"如需包含子目录请加 --recursive。"
                    )
            elif cand.is_file():
                files.append(cand)
                if not is_audio_like(cand):
                    warn(f"提示：{cand.name} 不是常见音视频扩展名，将尝试直接解码。")
    for m in missing:
        warn(f"找不到输入：{m}")

    seen, unique = set(), []
    for f in files:
        try:
            key = str(f.resolve()).lower()
        except OSError:
            key = str(f).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique, missing


def normalize_language(value) -> str | None:
    if value is None:
        return None
    key = str(value).strip().lower()
    if key in LANG_ALIASES:
        return LANG_ALIASES[key]
    return key or None


# --------------------------------------------------------------------------
# 文本渲染
# --------------------------------------------------------------------------
def build_paragraphs(rows, gap: float = 1.5, max_chars: int = 420):
    paras = []
    cur = None
    for row in rows:
        text = clean_text(row["text"])
        if not text:
            continue
        if (
            cur is not None
            and row["start"] - cur["end"] <= gap
            and len(cur["text"]) + len(text) <= max_chars
        ):
            cur["text"] = join_text(cur["text"], text)
            cur["end"] = row["end"]
        else:
            if cur is not None:
                paras.append(cur)
            cur = {"start": row["start"], "end": row["end"], "text": text}
    if cur is not None:
        paras.append(cur)
    return paras


def render_txt(paras) -> str:
    body = "\n\n".join(p["text"] for p in paras if p["text"])
    return body + "\n" if body else ""


def render_srt(rows) -> str:
    blocks = []
    index = 0
    for row in rows:
        text = clean_text(row["text"])
        if not text:
            continue
        index += 1
        blocks.append(
            f"{index}\n{fmt_srt_time(row['start'])} --> {fmt_srt_time(row['end'])}\n{text}\n"
        )
    return "\n".join(blocks)


def render_md(meta: dict, paras) -> str:
    lang_code = meta.get("language") or "auto"
    if "+" in lang_code:
        lang_label = "多语种"
    else:
        lang_label = LANG_NAMES.get(lang_code, lang_code)
    prob = meta.get("language_probability")
    prob_text = f"{prob:.0%}" if isinstance(prob, (int, float)) else "-"
    lines = [
        f"# {meta['stem']} 转写稿",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 源文件 | `{meta['audio']}` |",
        f"| 时长 | {fmt_clock(meta['duration'])} |",
        f"| 识别语言 | {lang_label}（{lang_code}，{prob_text}） |",
        f"| 模型 | {meta['model']}（{meta['device']}/{meta['compute_type']}） |",
        f"| 生成时间 | {meta['created_at']} |",
        "",
        "## 正文",
        "",
    ]
    for para in paras:
        if not para["text"]:
            continue
        lines.append(f"**[{fmt_clock(para['start'])}]** {para['text']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# 转写
# --------------------------------------------------------------------------
def load_whisper(libs: Path | None):
    if libs is not None:
        sys.path.insert(0, str(libs))
    import faster_whisper  # noqa: PLC0415 - 延迟导入，先定位 pylibs

    return faster_whisper.WhisperModel, faster_whisper.__version__


SR = 16000
LOW_CONFIDENCE = 0.62


def decode_audio_file(path, seconds: float | None = None, sampling_rate: int = SR):
    """用 PyAV 解码为 16k 单声道 float32；seconds 限制只解码开头部分。"""
    import numpy as np

    import av

    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=sampling_rate
    )
    want = int(seconds * sampling_rate) if seconds else None
    chunks, total = [], 0
    with av.open(str(path), mode="r") as container:
        try:
            frames = container.decode(audio=0)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"无法读取音轨：{exc}") from exc
        for frame in frames:
            frame.pts = None
            for out in resampler.resample(frame):
                arr = out.to_ndarray().reshape(-1)
                chunks.append(arr)
                total += arr.size
            if want is not None and total >= want:
                break
        try:
            for out in resampler.resample(None):
                arr = out.to_ndarray().reshape(-1)
                chunks.append(arr)
        except Exception:  # noqa: BLE001 - 冲刷失败不影响已解码部分
            pass
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(chunks).astype(np.float32) / 32768.0
    return audio[:want] if want is not None else audio


def _cjk_ratio(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
    return cjk / len(text)


def _ascii_letter_ratio(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    letters = len(re.findall(r"[A-Za-z]", text))
    return letters / len(text)


def _short_transcribe(model, audio, language):
    """低置信度校验用：对一小段音频快速转写，返回 (文本, 平均对数概率)。"""
    try:
        segments, _ = model.transcribe(
            audio,
            language=language,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            word_timestamps=False,
            vad_filter=False,
            condition_on_previous_text=False,
            no_speech_threshold=1.0,
            log_prob_threshold=-100.0,
        )
        rows = list(segments)
    except Exception:  # noqa: BLE001 - 校验失败则退回检测结果
        return "", -9.0
    if not rows:
        return "", -9.0
    text = "".join(r.text for r in rows)
    avg = sum(float(r.avg_logprob) for r in rows) / len(rows)
    return text, avg


def _detect_language(model, audio):
    try:
        language, probability, all_probs = model.detect_language(audio, vad_filter=True)
        return language, float(probability), list(all_probs or [])
    except Exception:  # noqa: BLE001
        return None, 0.0, []


def _heuristic_language(model, audio, detected, probability):
    """低置信度兜底：用中/英短转写判断更可能是中文还是英文。

    纯中文音频强制 zh 会输出汉字；纯英文音频强制 zh 通常仍输出英文单词。
    """
    zh_text, _ = _short_transcribe(model, audio, "zh")
    cjk = _cjk_ratio(zh_text)
    detail = {"zh_cjk_ratio": round(cjk, 3)}
    if cjk >= 0.30:
        return "zh", detail
    en_text, _ = _short_transcribe(model, audio, "en")
    ascii_ratio = _ascii_letter_ratio(en_text)
    detail["en_ascii_ratio"] = round(ascii_ratio, 3)
    if ascii_ratio >= 0.45:
        return "en", detail
    return detected, detail


def choose_language(model, src: Path, args):
    """决定单个文件的语言与模式（single / multilingual）。"""
    if args.language:
        return {"mode": "single", "language": args.language, "probability": None,
                "note": "forced"}
    if not getattr(model.model, "is_multilingual", True):
        return {"mode": "single", "language": "en", "probability": 1.0,
                "note": "monolingual-model"}
    if args.multilingual:
        log("    按 --multilingual 使用分段多语种识别")
        return {"mode": "multilingual", "language": None, "probability": None,
                "note": "forced-multilingual"}

    detected, probability = None, 0.0
    try:
        head = decode_audio_file(src, 30.0)
    except Exception as exc:  # noqa: BLE001
        warn(f"    警告：语言预检测失败（{exc}），交给模型自行判断。")
        head = None
    if head is not None and head.size >= SR // 2:
        detected, probability, _ = _detect_language(model, head)
        if detected:
            log(f"    语言检测: {LANG_NAMES.get(detected, detected)}"
                f"（{detected}，置信度 {probability:.0%}）")

    if detected and probability >= LOW_CONFIDENCE:
        return {"mode": "single", "language": detected, "probability": probability,
                "note": "detect"}
    if detected is None:
        return {"mode": "single", "language": None, "probability": 0.0,
                "note": "no-detect"}

    log(f"    ⚠ 语言检测置信度较低（{detected} {probability:.0%}），"
        f"自动改用分段多语种识别（可用 --language 强制指定语言）")
    return {"mode": "multilingual", "language": None, "probability": probability,
            "note": "auto-multilingual", "detected": detected}


def build_initial_prompt(args, language):
    prompt = (args.initial_prompt or "").strip()
    if args.simplified:
        if language == "zh":
            hint = "以下是普通话的句子，请使用简体中文。"
            prompt = f"{prompt} {hint}".strip() if prompt else hint
        elif language is not None:
            warn("提示：--simplified 只对中文（zh）有效，已忽略。")
    return prompt or None


def transcribe_kwargs(args, language, prompt=None, allow_clip=True) -> dict:
    kwargs = {
        "language": language,
        "beam_size": args.beam_size,
        "best_of": args.beam_size,
        "temperature": [0.0, 0.2, 0.4],
        "condition_on_previous_text": bool(args.condition_on_previous_text),
        "vad_filter": bool(args.vad),
        "word_timestamps": bool(args.words),
        "no_speech_threshold": 0.6,
        "log_prob_threshold": -1.0,
    }
    if args.vad:
        kwargs["vad_parameters"] = {"min_silence_duration_ms": 500}
    if prompt:
        kwargs["initial_prompt"] = prompt
    if allow_clip and args.max_seconds and args.max_seconds > 0:
        kwargs["clip_timestamps"] = f"0,{int(round(args.max_seconds))}"
    return kwargs


def unique_output_stem(src: Path, used: set) -> str:
    stem = src.stem or "audio"
    if stem in used:
        stem = f"{src.parent.name}_{stem}"
    base, i = stem, 2
    while stem in used:
        stem = f"{base}_{i}"
        i += 1
    used.add(stem)
    return stem


def _segment_row(seg, start_offset: float = 0.0, word_ts: bool = True) -> dict:
    row = {
        "id": 0,
        "start": round(float(seg.start) + start_offset, 3),
        "end": round(float(seg.end) + start_offset, 3),
        "text": clean_text(seg.text),
        "avg_logprob": round(float(seg.avg_logprob), 4),
        "no_speech_prob": round(float(seg.no_speech_prob), 4),
    }
    if word_ts and getattr(seg, "words", None):
        row["words"] = [
            {
                "w": w.word,
                "s": round(float(w.start) + start_offset, 3),
                "e": round(float(w.end) + start_offset, 3),
                "p": round(float(w.probability), 3),
            }
            for w in seg.words
        ]
    return row


def stream_segments(segments_iter, partial_file, args, start_offset: float = 0.0):
    """边接收边打印并写 partial 草稿，返回段列表。"""
    rows = []
    last_report = time.time()
    for seg in segments_iter:
        row = _segment_row(seg, start_offset, args.words)
        row["id"] = len(rows)
        rows.append(row)
        partial_file.write(f"[{fmt_clock(row['start'])}] {row['text']}\n")
        partial_file.flush()
        if not args.quiet:
            log(f"[{fmt_clock(row['start'])} ~ {fmt_clock(row['end'])}] {row['text']}")
        else:
            now = time.time()
            if now - last_report >= 30:
                log(f"    …已转写 {len(rows)} 段（至 {fmt_clock(row['end'])}）")
                last_report = now
    return rows


def _build_windows(speech_chunks, duration, target=30.0, min_gap=1.0):
    """把 VAD 语音片段合并成约 30 秒的转写窗口（在长停顿处切分）。"""
    windows, cur = [], None
    for ch in speech_chunks:
        start = max(0.0, ch["start"] / SR - 0.2)
        end = min(duration, ch["end"] / SR + 0.2)
        if cur is None:
            cur = [start, end]
            continue
        gap = start - cur[1]
        if (
            (gap >= min_gap and cur[1] - cur[0] >= 2.0)
            or gap >= 1.2
            or (end - cur[0]) > target
        ):
            windows.append((cur[0], cur[1]))
            cur = [start, end]
        else:
            cur[1] = max(cur[1], end)
    if cur is not None:
        windows.append((cur[0], cur[1]))
    merged = []
    for window in windows:
        if merged and (window[1] - window[0]) < 1.5:
            merged[-1] = (merged[-1][0], max(merged[-1][1], window[1]))
        else:
            merged.append(window)
    return merged or [(0.0, min(duration, target))]


def transcribe_multilingual(model, src: Path, args, partial_file):
    """按停顿切窗、逐窗识别语言并转写（适合中英混说/多语种音频）。"""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    log("")
    t_decode = time.time()
    audio = decode_audio_file(src)
    duration = len(audio) / SR
    if duration <= 0:
        raise RuntimeError("音频为空或无法解码")
    if args.max_seconds and args.max_seconds > 0 and duration > args.max_seconds:
        audio = audio[: int(args.max_seconds * SR)]
        duration = len(audio) / SR
    log(f"    分段模式：解码 {fmt_clock(duration)}，用时 {time.time() - t_decode:.1f}s")

    speech_chunks = []
    if args.vad:
        try:
            speech_chunks = get_speech_timestamps(
                audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=100)
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"    警告：VAD 失败（{exc}），改用 30 秒固定切窗")
    if speech_chunks:
        windows = _build_windows(speech_chunks, duration)
    else:
        windows = []
        step = 30.0
        start = 0.0
        while start < duration:
            windows.append((start, min(duration, start + step)))
            start += step

    rows, languages = [], []
    for idx, (start, end) in enumerate(windows, start=1):
        chunk = audio[int(start * SR): int(end * SR)]
        if chunk.size < SR // 4:
            continue
        detected, probability, _ = _detect_language(model, chunk)
        if not getattr(model.model, "is_multilingual", True):
            language, source = "en", "英文模型"
        elif detected and probability >= LOW_CONFIDENCE:
            language, source = detected, f"检测 {probability:.0%}"
        elif detected:
            language, detail = _heuristic_language(model, chunk, detected, probability)
            source = f"校验 {detail}"
        else:
            language, source = "en", "检测失败"
        prompt = build_initial_prompt(args, language)
        kwargs = transcribe_kwargs(args, language, prompt, allow_clip=False)
        segments_iter, _info = model.transcribe(chunk, **kwargs)
        log("")
        log(f"--- 片段 {idx}/{len(windows)} [{fmt_clock(start)} ~ {fmt_clock(end)}]"
            f" 语言 {language}（{source}）")
        rows.extend(stream_segments(segments_iter, partial_file, args, start_offset=start))
        languages.append(language)
    # 跨窗口统一重编号，保证 JSON 段 id 全局唯一
    for row_index, row in enumerate(rows):
        row["id"] = row_index
    return rows, duration, languages


def transcribe_one(model, whisper_version, src: Path, outdir: Path, stem: str, args, formats):
    """转写单个文件并写出所需格式，返回结果字典。"""
    outdir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    partial_path = outdir / f"{stem}.partial.txt"
    t_transcribe = time.time()

    choice = choose_language(model, src, args)
    mode = choice["mode"]
    languages = None
    language_probability = None

    try:
        with open(partial_path, "w", encoding="utf-8", newline="\n") as pf:
            if mode == "multilingual":
                rows, duration, languages = transcribe_multilingual(model, src, args, pf)
                language = "+".join(sorted(set(languages))) if languages else "auto"
                log("")
                log(f"    识别语言: 多语种分段（{language}）  音频时长: {fmt_clock(duration)}")
            else:
                language = choice["language"]
                prompt = build_initial_prompt(args, language)
                kwargs = transcribe_kwargs(args, language, prompt)
                segments_iter, info = model.transcribe(str(src), **kwargs)
                duration = float(info.duration or 0.0)
                language = info.language or language
                language_probability = float(info.language_probability or 0.0)
                log("")
                log(f"    识别语言: {LANG_NAMES.get(language, language)}"
                    f"（{language}，置信度 {language_probability:.0%}）"
                    f"  音频时长: {fmt_clock(duration)}")
                if duration >= 600:
                    log("    提示：音频较长，CPU 转写约需 1 倍音频时长，请耐心等待。")
                rows = stream_segments(segments_iter, pf, args)
    except BaseException:
        # 中断/报错时保留 partial 文件，便于查看已转内容
        raise

    t_elapsed = time.time() - t_transcribe
    total_elapsed = time.time() - t_start

    if duration <= 0 and rows:
        duration = max(float(r["end"]) for r in rows)

    partial_kept = False
    if args.keep_partial:
        partial_kept = True
    else:
        try:
            partial_path.unlink()
        except OSError:
            pass

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    paras = build_paragraphs(rows)
    meta = {
        "stem": stem,
        "audio": str(src.resolve()),
        "duration": duration,
        "language": language,
        "language_probability": language_probability,
        "model": args.model_name,
        "device": args.device,
        "compute_type": args.compute_type,
        "created_at": created_at,
    }

    outputs = {}
    outdir.mkdir(parents=True, exist_ok=True)
    if "txt" in formats:
        path = outdir / f"{stem}.txt"
        write_text(path, render_txt(paras))
        outputs["txt"] = str(path)
    if "srt" in formats:
        path = outdir / f"{stem}.srt"
        write_text(path, render_srt(rows))
        outputs["srt"] = str(path)
    if "md" in formats:
        path = outdir / f"{stem}.md"
        write_text(path, render_md(meta, paras))
        outputs["md"] = str(path)
    if "json" in formats:
        path = outdir / f"{stem}.json"
        processed_duration = duration
        if args.max_seconds and args.max_seconds > 0:
            processed_duration = min(duration, float(args.max_seconds))
        language_name = LANG_NAMES.get(language, language)
        if languages and len(set(languages)) > 1:
            language_name = "多语种"
        payload = {
            "audio": str(src.resolve()),
            "audio_name": src.name,
            "audio_size": src.stat().st_size if src.is_file() else None,
            "duration_sec": round(duration, 3),
            "processed_duration_sec": round(processed_duration, 3),
            "language": language,
            "language_name": language_name,
            "language_probability": (
                round(language_probability, 4) if language_probability is not None else None
            ),
            "languages": languages,
            "mode": mode,
            "detection": choice,
            "model": args.model_name,
            "model_dir": str(args.model_dir) if args.model_dir else None,
            "device": args.device,
            "compute_type": args.compute_type,
            "params": {
                "beam_size": args.beam_size,
                "vad": bool(args.vad),
                "word_timestamps": bool(args.words),
                "max_seconds": args.max_seconds,
                "language_arg": args.language,
                "simplified": bool(args.simplified),
            },
            "created_at": created_at,
            "elapsed_sec": round(total_elapsed, 2),
            "real_time_factor": round(
                total_elapsed / max(
                    min(duration, args.max_seconds) if args.max_seconds else duration, 0.01
                ),
                3,
            ),
            "faster_whisper_version": whisper_version,
            "segment_count": len(rows),
            "segments": rows,
        }
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        outputs["json"] = str(path)

    speed = 0.0
    processed_duration = duration
    if args.max_seconds and args.max_seconds > 0:
        processed_duration = min(duration, float(args.max_seconds))
    if t_elapsed > 0:
        speed = processed_duration / t_elapsed
    clipped_note = ""
    if args.max_seconds and args.max_seconds > 0:
        clipped_note = f"（仅转写前 {fmt_clock(processed_duration)}）"
    log("")
    log(f"    完成：{len(rows)} 段 | 用时 {fmt_clock(total_elapsed)} | "
        f"{speed:.1f}x 实时{clipped_note}")
    log(f"    输出目录: {outdir}")
    for fmt in FORMATS:
        if fmt in outputs:
            log(f"      - {fmt.upper():4s} {outputs[fmt]}")
    if partial_kept:
        log(f"      - 中间结果（保留） {partial_path}")

    return {
        "audio": str(src),
        "stem": stem,
        "duration_sec": round(duration, 3),
        "processed_duration_sec": round(processed_duration, 3),
        "language": language,
        "language_probability": language_probability,
        "mode": mode,
        "languages": languages,
        "segment_count": len(rows),
        "elapsed_sec": round(total_elapsed, 2),
        "speed": round(speed, 2),
        "status": "ok",
        "outputs": outputs,
        "partial": str(partial_path) if partial_kept else None,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_formats(value: str):
    value = (value or "").strip().lower()
    if value in ("all", "*", ""):
        return list(FORMATS)
    parts = [p.strip() for p in value.split(",") if p.strip()]
    bad = [p for p in parts if p not in FORMATS]
    if bad:
        raise argparse.ArgumentTypeError(
            f"未知格式 {bad}，可选：all 或 {','.join(FORMATS)} 的组合"
        )
    return [fmt for fmt in FORMATS if fmt in parts]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transcribe_audio.py",
        description="本地离线音频/视频转文字（faster-whisper，CPU int8）。"
                    "默认输出 txt+srt+md+json 到工作区 transcripts/ 目录。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python transcribe_audio.py 录音.mp3\n"
            "  python transcribe_audio.py 录音.m4a --language zh --format txt,srt\n"
            "  python transcribe_audio.py 会议录音.mp4 --model large-v3 --output-dir out\n"
            "  python transcribe_audio.py 音频目录 --recursive --skip-existing\n"
            "  python transcribe_audio.py --list-models\n"
        ),
    )
    parser.add_argument("inputs", nargs="*", help="音频/视频文件、目录或通配符（可多个）")
    parser.add_argument("--output-dir", default=None,
                        help="输出目录（默认：<工作区>/transcripts；需可写）")
    parser.add_argument("--format", type=parse_formats, default=list(FORMATS),
                        help="输出格式：all 或逗号组合 txt,srt,md,json（默认 all）")
    parser.add_argument("--model", default=None,
                        help=f"模型名或模型目录（默认 {DEFAULT_MODEL}；中英多语种）")
    parser.add_argument("--fast", action="store_true",
                        help="纯英文加速：需配合 --language en，自动改用 medium.en（若本地存在）")
    parser.add_argument("--language", default="auto",
                        help="语言：auto（默认自动检测）/ zh / en / ja ...")
    parser.add_argument("--multilingual", action="store_true",
                        help="分段多语种模式：按停顿切窗、逐窗识别语言，适合中英混说")
    parser.add_argument("--simplified", action="store_true",
                        help="中文输出偏好简体（用提示词引导，配合中文音频使用）")
    parser.add_argument("--device", default="cpu", help="cpu（默认）或 cuda（需自行装 GPU 依赖）")
    parser.add_argument("--compute-type", default="int8",
                        help="int8（默认，CPU 推荐）/ float32 / float16(cuda)")
    parser.add_argument("--threads", type=int, default=0,
                        help="CPU 线程数（0=自动）")
    parser.add_argument("--beam-size", type=int, default=5, help="beam size（默认 5）")
    parser.add_argument("--vad", dest="vad", action="store_true", default=True,
                        help="启用 Silero VAD 过滤静音（默认启用）")
    parser.add_argument("--no-vad", dest="vad", action="store_false",
                        help="关闭 VAD 过滤")
    parser.add_argument("--words", dest="words", action="store_true", default=True,
                        help="生成词级时间戳（默认启用，json 中更详细）")
    parser.add_argument("--no-words", dest="words", action="store_false",
                        help="不生成词级时间戳（更快、json 更小）")
    parser.add_argument("--condition-on-previous-text", action="store_true",
                        help="让上一段文本参与解码（长文更连贯，但可能带入误识别）")
    parser.add_argument("--initial-prompt", default=None,
                        help="提示词：引导专有名词/术语的写法，如人名、产品名")
    parser.add_argument("--max-seconds", type=float, default=0,
                        help="只转写开头 N 秒（默认 0=完整音频）")
    parser.add_argument("--recursive", action="store_true", help="递归扫描输入目录")
    parser.add_argument("--skip-existing", action="store_true",
                        help="输出文件已存在则跳过该输入")
    parser.add_argument("--keep-partial", action="store_true",
                        help="保留 .partial.txt 中间结果")
    parser.add_argument("--quiet", action="store_true", help="不逐段打印，只打印结果")
    parser.add_argument("--list-models", action="store_true", help="列出本地已安装模型后退出")
    parser.add_argument("--dry-run", action="store_true",
                        help="只显示将处理哪些文件、用哪个模型，不实际转写")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def write_run_summary(summary_path: Path, args, model_dir, formats, files,
                      missing_inputs, results, failures) -> None:
    summary = {
        "tool": "audio-to-text",
        "version": VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": args.model_name,
        "model_dir": str(model_dir) if model_dir else None,
        "device": args.device,
        "compute_type": args.compute_type,
        "language": args.language,
        "multilingual": bool(getattr(args, "multilingual", False)),
        "simplified": bool(getattr(args, "simplified", False)),
        "formats": formats,
        "file_count": len(files),
        "missing_inputs": missing_inputs,
        "ok": len([r for r in results if r.get("status") == "ok"]),
        "failed": failures,
        "skipped": len([r for r in results if r.get("status") == "skipped"]),
        "results": results,
    }
    write_text(summary_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    args.language = normalize_language(args.language)
    if args.multilingual and args.language:
        warn("提示：同时指定了 --language 与 --multilingual，将按单语种强制模式处理。")
        args.multilingual = False
    if args.simplified and args.language and args.language != "zh":
        warn("提示：--simplified 只对中文有效，已忽略。")
        args.simplified = False
    if args.simplified:
        warn("提示：--simplified 通过提示词引导简体输出，可能影响用词/断句；")
        warn("      短音频或安静录音建议只作参考，必要时人工核对。")

    engine = resolve_engine()
    libs = engine["libs"]
    models_dir = engine["models"]
    workspace = engine["workspace"]

    log("audio-to-text v" + VERSION)
    log(f"  引擎 pylibs : {libs if libs else '未找到'}")
    log(f"  模型目录    : {models_dir if models_dir else '未找到'}")

    if args.list_models:
        local = list_local_models(models_dir)
        if not local:
            warn("没有找到任何本地模型。")
            return 2
        log(f"本地模型（{len(local)} 个）：")
        for model_dir in local:
            size = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
            log(f"  - {model_dir.name:12s} {human_size(size):>9s}  {model_dir}")
        if any(m.name.lower() == DEFAULT_MODEL.lower() for m in local):
            log(f"推荐默认模型：{DEFAULT_MODEL}")
        return 0

    if libs is None:
        warn("")
        warn("错误：找不到转写引擎（faster-whisper）。")
        warn("  期望位置：<工作区>/asr/pylibs/faster_whisper")
        warn("  如引擎在别处，请设置环境变量 AUDIO2TEXT_LIBS 指向 asr/pylibs 目录，")
        warn(f"  或在本 skill 目录写 config.json：{SKILL_ROOT / 'config.json'}"
             '（内容示例 {"libs": "D:/.../asr/pylibs", "models": "D:/.../asr/models"}）。')
        return 2

    if not args.inputs:
        warn("错误：没有输入文件。用法示例：transcribe_audio.py 录音.mp3")
        warn("可用 --list-models 查看本地模型，--help 查看全部参数。")
        return 2

    files, missing_inputs = expand_inputs(args.inputs, args.recursive)
    if missing_inputs:
        warn(f"注意：{len(missing_inputs)} 个输入不存在，已忽略：")
        for name in missing_inputs:
            warn(f"  - {name}")
    if not files:
        warn("错误：没有找到可处理的音频/视频文件。")
        return 2

    model_requested = args.model is not None
    model_spec = normalize_model_spec(args.model or DEFAULT_MODEL)
    if args.fast and not model_requested:
        if args.language == "en":
            # 纯英文且未显式指定模型时，用更快的英文模型
            model_spec = pick_fast_model(models_dir, model_spec)
        else:
            warn("提示：--fast 仅适用于纯英文（需同时指定 --language en），本次继续使用默认模型。")
    log(f"  使用模型    : {model_spec}")
    if args.language is None:
        log("  识别语言    : 自动检测")
    else:
        log(f"  识别语言    : {args.language}（强制）")

    model_dir = resolve_model(model_spec, models_dir)
    if model_dir is None:
        warn("")
        warn(f"错误：找不到模型 “{model_spec}”。")
        local = list_local_models(models_dir)
        if local:
            warn("  本地可用模型：" + "、".join(m.name for m in local))
        warn(f"  模型目录：{models_dir}")
        warn("  默认请使用 large-v3；如需英文加速用 --fast（medium.en）。")
        warn("  如确实需要新模型，请让维护者把模型放进上述目录（或运行 asr/download_models.py）。")
        return 2
    args.model_dir = model_dir
    args.model_name = model_dir.name

    outdir = Path(args.output_dir).expanduser() if args.output_dir else None
    if outdir is None:
        outdir = (workspace or Path.cwd()) / "transcripts"
    outdir = outdir.resolve()
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        probe = outdir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        warn(f"错误：输出目录不可写：{outdir}（{exc}）")
        warn("  请用 --output-dir 指定一个工作区内的可写目录。")
        return 2

    log(f"  输出目录    : {outdir}")
    log(f"  待处理文件  : {len(files)} 个")
    for f in files:
        log(f"      - {f}")

    if args.dry_run:
        log("（--dry-run：不实际转写）")
        return 0

    formats = args.format
    used_stems: set = set()
    planned = []
    for src in files:
        stem = unique_output_stem(src, used_stems)
        existing = [outdir / f"{stem}.{fmt}" for fmt in formats]
        skip = bool(args.skip_existing and all(p.is_file() for p in existing))
        planned.append({"src": src, "stem": stem, "existing": existing, "skip": skip})

    results: list = []
    failures = 0
    summary_path = outdir / "run-summary.json"

    if all(item["skip"] for item in planned):
        log("")
        log("所有文件均已有输出（--skip-existing），跳过转写（不加载模型）。")
        for item in planned:
            log(f"  = {item['src'].name} —— 已存在，跳过")
            results.append({
                "audio": str(item["src"]),
                "stem": item["stem"],
                "status": "skipped",
                "elapsed_sec": 0.0,
                "outputs": [str(p) for p in item["existing"]],
            })
        write_run_summary(summary_path, args, model_dir, formats, files,
                          missing_inputs, results, failures)
        log("")
        log(f"批量完成：成功 0，跳过 {len(results)}，失败 0，共 {len(files)} 个文件")
        log(f"运行摘要：{summary_path}")
        return 0

    try:
        WhisperModel, whisper_version = load_whisper(libs)
    except Exception as exc:  # noqa: BLE001
        warn("")
        warn(f"错误：无法加载 faster-whisper（{exc}）。")
        warn(f"  pylibs 目录：{libs}")
        warn("  请确认使用 DSH 自带 Python 3.12 运行，且 asr/pylibs 完整。")
        return 2

    threads = args.threads if args.threads and args.threads > 0 else 0
    log("")
    log(f"加载模型 {model_dir}（device={args.device}, compute_type={args.compute_type}）...")
    t_load = time.time()
    try:
        model = WhisperModel(
            str(model_dir),
            device=args.device,
            compute_type=args.compute_type,
            cpu_threads=threads,
            num_workers=1,
        )
    except Exception as exc:  # noqa: BLE001
        warn(f"错误：模型加载失败：{exc}")
        traceback.print_exc()
        return 2
    log(f"模型加载完成（{time.time() - t_load:.1f}s）")

    for idx, item in enumerate(planned, start=1):
        src = item["src"]
        stem = item["stem"]
        existing = item["existing"]
        if item["skip"]:
            log("")
            log(f"=== [{idx}/{len(files)}] {src.name} —— 已存在输出，跳过")
            for p in existing:
                log(f"      - {p}")
            results.append({
                "audio": str(src),
                "stem": stem,
                "status": "skipped",
                "elapsed_sec": 0.0,
                "outputs": [str(p) for p in existing],
            })
            continue

        log("")
        log(f"=== [{idx}/{len(files)}] {src.name}  ({src})")
        try:
            result = transcribe_one(
                model, whisper_version, src, outdir, stem, args, formats
            )
            results.append(result)
            log("    状态：成功")
        except KeyboardInterrupt:
            warn("")
            warn("已中断：已转写的内容保存在 .partial.txt 中。")
            raise
        except Exception as exc:  # noqa: BLE001
            failures += 1
            traceback.print_exc()
            warn(f"    状态：失败（{exc}）")
            results.append(
                {
                    "audio": str(src),
                    "stem": stem,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    write_run_summary(summary_path, args, model_dir, formats, files,
                      missing_inputs, results, failures)

    log("")
    log("=" * 64)
    ok = len([r for r in results if r.get("status") == "ok"])
    skipped = len([r for r in results if r.get("status") == "skipped"])
    log(f"批量完成：成功 {ok}，跳过 {skipped}，失败 {failures}，共 {len(files)} 个文件")
    for r in results:
        if r.get("status") == "ok":
            clip_note = ""
            if args.max_seconds and args.max_seconds > 0:
                clip_note = f"（仅转写前 {fmt_clock(args.max_seconds)}）"
            log(f"  ✓ {r['stem']:32s} {r['segment_count']:5d} 段  "
                f"{fmt_clock(r['duration_sec'])}  {r['language']}  "
                f"{r['speed']:.1f}x 实时{clip_note}")
        elif r.get("status") == "failed":
            log(f"  ✗ {r['stem']:32s} {r.get('error', '')}")
    log(f"运行摘要：{summary_path}")
    if missing_inputs:
        warn(f"注意：{len(missing_inputs)} 个输入不存在，已忽略（详见上方）。")
    return 0 if (failures == 0 and not missing_inputs) else 1


if __name__ == "__main__":
    try:
        exit_code = main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # 输出被管道提前关闭（例如 | Select-Object -First N），安静退出
        try:
            sys.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        sys.exit(0)
    else:
        sys.exit(exit_code)
