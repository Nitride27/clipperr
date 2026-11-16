"""Local faster-whisper word timestamps -> pop-style ASS captions."""
import os
import site
import threading
from pathlib import Path

_model, _lock = None, threading.Lock()


def _load(name):
    global _model
    if _model:
        return _model
    for sp in site.getsitepackages():  # pip-installed CUDA DLLs (nvidia-cublas-cu12 / nvidia-cudnn-cu12)
        for d in Path(sp, "nvidia").glob("*/bin"):
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]
    from faster_whisper import WhisperModel
    try:
        _model = WhisperModel(name, device="cuda", compute_type="float16")
    except Exception as e:
        print("whisper CUDA failed, falling back to CPU small:", e)
        _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def transcribe(media, model_name):
    """-> [{w, s, e}] with times relative to the start of `media`."""
    with _lock:
        segs, _ = _load(model_name).transcribe(str(media), word_timestamps=True, vad_filter=True,
                                               language="en", beam_size=1)
        return [{"w": w.word.strip(), "s": w.start, "e": w.end} for seg in segs for w in seg.words if w.word.strip()]


def snap(words, a, b, slack=3.0, gap=0.4):
    """Move clip edges [a, b] (seconds into the source) by at most `slack` to the nearest pause in speech,
    so clips don't open or close mid-sentence. No words / no pause in reach -> edge stays put."""
    starts = [words[0]["s"]] if words else []
    ends = [words[-1]["e"]] if words else []
    for x, y in zip(words, words[1:]):
        if y["s"] - x["e"] >= gap:
            ends.append(x["e"])
            starts.append(y["s"])
    na = min((t - 0.15 for t in starts if abs(t - 0.15 - a) <= slack), key=lambda t: abs(t - a), default=a)
    nb = min((t + 0.3 for t in ends if abs(t + 0.3 - b) <= slack), key=lambda t: abs(t - b), default=b)
    return (na, nb) if nb - na >= 3 else (a, b)


def _ts(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


DEFAULT_STYLE = {
    "layout": "split", "split": 0.6, "zoom": 0.6, "bg": "",
    "captions": True, "font": "Arial Black", "size": 92, "color": "#FFFFFF", "hl": "#FFE600",
    "outline": "#000000", "outline_w": 7, "shadow": 3, "y": None, "words": 3, "upper": True, "pop": True,
    "hook_color": "#000000", "hook_bg": "#FFFFFF", "hook_size": 64, "hook_secs": 3,
}


def clean_style(d):
    """Keep only known keys, coerced to the default's type (input comes from the browser)."""
    out = {}
    for k, v in (d or {}).items():
        if k not in DEFAULT_STYLE:
            continue
        ref = DEFAULT_STYLE[k]
        try:
            if k == "y":
                out[k] = None if v is None else min(max(float(v), 0.05), 0.95)
            elif isinstance(ref, bool):
                out[k] = bool(v)
            elif isinstance(ref, (int, float)):
                out[k] = type(ref)(v)
            elif k in ("color", "hl", "outline", "hook_color", "hook_bg"):
                v = str(v)
                out[k] = v if len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in v[1:]) else ref
            else:
                out[k] = str(v)[:80]
        except (TypeError, ValueError):
            pass
    return out


def _ass_color(hexcol, alpha=0):
    h = hexcol.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _clean(s, upper=True):
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return s.upper() if upper else s


def caption_y(style):
    """Caption centre in px: explicit y, else the split seam / lower third."""
    from .render import split_top
    if style.get("y") is not None:
        return int(style["y"] * 1920)
    return split_top(style) if style.get("layout") == "split" else 1480


def build_ass(words, offset, dur, style, hook=""):
    """words are relative to the source file; the clip starts at `offset` into it."""
    st = {**DEFAULT_STYLE, **style}
    up, y = st["upper"], caption_y(st)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{_ass_color(st['color'])},{_ass_color(st['color'])},{_ass_color(st['outline'])},&H80000000,0,0,0,0,100,100,0,0,1,{st['outline_w']},{st['shadow']},5,40,40,0,1
Style: Hook,{st['font']},{st['hook_size']},{_ass_color(st['hook_color'])},{_ass_color(st['hook_color'])},{_ass_color(st['hook_bg'])},{_ass_color(st['hook_bg'])},0,0,0,0,100,100,0,0,3,18,0,8,60,60,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if st["captions"]:
        ws = [dict(w, s=w["s"] - offset, e=w["e"] - offset) for w in words if offset <= w["s"] < offset + dur]
        groups, cur = [], []
        for w in ws:  # new line every N words, on a pause, or after punctuation
            if cur and (len(cur) >= st["words"] or w["s"] - cur[-1]["e"] > 0.6 or cur[-1]["w"][-1] in ".?!,"):
                groups.append(cur)
                cur = []
            cur.append(w)
        if cur:
            groups.append(cur)
        hl = _ass_color(st["hl"])[4:]  # "&H00BBGGRR" -> "BBGGRR"
        pop = r"\fscx100\fscy100\t(0,80,\fscx118\fscy118)" if st["pop"] else ""
        for g in groups:
            for i, w in enumerate(g):
                end = g[i + 1]["s"] if i + 1 < len(g) else w["e"] + 0.15
                parts = [r"{\c&H" + hl + "&" + pop + "}" + _clean(x["w"], up) + r"{\r}" if j == i
                         else _clean(x["w"], up) for j, x in enumerate(g)]
                lines.append(f"Dialogue: 0,{_ts(w['s'])},{_ts(end)},Cap,,0,0,0,,{{\\pos(540,{y})}}{' '.join(parts)}")
    if hook:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(st['hook_secs'])},Hook,,0,0,0,,{_clean(hook, up)}")
    return head + "\n".join(lines) + "\n"
