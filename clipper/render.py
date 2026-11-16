"""One ffmpeg pass: 9:16 layout + burned ASS captions, NVENC."""
import random
import subprocess
import threading
from pathlib import Path

ASSETS = Path(__file__).parent.parent / "assets"
BG_DIR = ASSETS / "bg"
FONT_DIR = ASSETS / "fonts"
_gpu = threading.BoundedSemaphore(2)  # consumer NVENC caps concurrent sessions; "apply to all" would exceed it

# family name (what ASS/CSS ask for) -> file in assets/fonts
FONTS = {"Arial Black": "ariblk.ttf", "Impact": "impact.ttf", "Segoe UI Black": "seguibl.ttf",
         "Anton": "Anton-Regular.ttf", "Bebas Neue": "BebasNeue-Regular.ttf", "Poppins Black": "Poppins-Black.ttf",
         "Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Luckiest Guy": "LuckiestGuy-Regular.ttf",
         "Bangers": "Bangers-Regular.ttf", "Archivo Black": "ArchivoBlack-Regular.ttf"}


def split_top(style):
    return int(1920 * min(max(style.get("split", 0.6), 0.4), 0.75)) // 2 * 2


def _probe_dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def bg_files():
    return sorted(p for p in BG_DIR.glob("*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov"))


def bg_thumb(p):
    """Small jpg of a background loop for the picker, cached next to it."""
    t = BG_DIR / ".thumbs" / (p.stem + ".jpg")
    if not t.exists() or t.stat().st_mtime < p.stat().st_mtime:
        t.parent.mkdir(exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "3", "-i", str(p), "-frames:v", "1",
                        "-vf", "scale=360:-2", str(t)], capture_output=True)
    return t


def frame(src, at, out):
    """Full-res still from the clip source, for the style editor preview."""
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(src),
                        "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True)
    return out


def render(src, offset, dur, ass_name, out, style):
    """src: padded source mp4. ass_name: subtitle file inside out's folder (ffmpeg runs there, so no drive-letter escaping)."""
    cwd = Path(out).parent
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{offset:.2f}", "-t", f"{dur:.2f}", "-i", str(src)]
    bgs = bg_files()
    layout = style.get("layout", "full")
    if layout == "split" and not bgs:
        layout = "full"  # no background loops supplied yet
    fontsdir = Path("..", "..", "assets", "fonts").as_posix()  # relative to out/<job>/
    subs = f"ass={ass_name}:fontsdir={fontsdir}"
    if layout == "split":
        pick = next((p for p in bgs if p.name == style.get("bg")), None) or random.choice(bgs)
        bg_start = random.uniform(0, max(_probe_dur(pick) - dur - 1, 0))
        top = split_top(style)
        bot = 1920 - top
        cmd += ["-ss", f"{bg_start:.2f}", "-stream_loop", "-1", "-i", str(pick)]
        fg = (f"[0:v]crop=ih*1080/{top}:ih,scale=1080:{top},setsar=1[top];"
              f"[1:v]scale=1080:{bot}:force_original_aspect_ratio=increase,crop=1080:{bot},setsar=1[bot];"
              f"[top][bot]vstack,fps=30,{subs}[v]")
    else:
        z = min(max(style.get("zoom", 0.6), 0.35), 1.0)
        fg = (f"[0:v]split[a][b];"
              f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,eq=brightness=-0.15,"
              f"scale=1080:1920[bg];"  # blur at 1/4 res: same look, ~10x faster
              f"[b]crop=iw*{z}:ih,scale=1080:-2,setsar=1[g];"
              f"[bg][g]overlay=(W-w)/2:(H-h)/2-80,fps=30,{subs}[v]")
    cmd += ["-filter_complex", fg, "-map", "[v]", "-map", "0:a:0", "-t", f"{dur:.2f}",
            "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", Path(out).name]
    with _gpu:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[-800:])
    return layout
