"""Getting video: live recording, remote stream resolution, and accurate section extraction."""
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BIN = Path(sys.executable).parent  # yt-dlp / streamlink live in the venv


def _exe(name):
    return str(BIN / name) if (BIN / f"{name}.exe").exists() else shutil.which(name) or name


def clean(url):
    return re.sub(r"[?&]chatroom=\d+", "", url)  # our Kick override param; media tools must not see it


def info(url):
    """yt-dlp metadata: streamer name + direct media URLs (video[, audio])."""
    out = subprocess.run([_exe("yt-dlp"), "-J", "--no-warnings", "--js-runtimes", "node", "--remote-components",
                          "ejs:github", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", clean(url)],
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[-500:])
    d = json.loads(out.stdout)
    urls = [f["url"] for f in d.get("requested_formats") or []] or [d["url"]]
    name = d.get("uploader") or d.get("channel") or d.get("uploader_id") or "streamer"
    return {"name": name, "urls": urls, "title": d.get("title", ""), "is_live": bool(d.get("is_live")),
            "heatmap": d.get("heatmap") or []}  # YouTube "most replayed"; empty elsewhere


class Recording:
    """Live stream recorded as 4s .ts chunks so old footage can be deleted while recording.
    seg.csv (written by ffmpeg as each chunk closes) maps chunk -> start/end seconds on the recording clock."""

    def __init__(self, url, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sl = subprocess.Popen([_exe("streamlink"), "--stdout", "--twitch-low-latency", "--hls-live-edge", "2",
                                    clean(url), "1080p60,1080p,936p60,720p60,720p,best"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0",
                                    "-c", "copy", "-f", "segment", "-segment_time", "4",
                                    "-segment_list", "seg.csv", "-segment_list_type", "csv", "seg_%06d.ts"],
                                   stdin=self.sl.stdout, cwd=self.dir, stderr=subprocess.DEVNULL)
        self.sl.stdout.close()  # ffmpeg owns the pipe now

    def alive(self):
        return self.ff.poll() is None

    def started(self):
        return (self.dir / "seg_000000.ts").exists() and (self.dir / "seg_000000.ts").stat().st_size > 0

    def segments(self):
        """[(name, start, end)] of finished chunks still on disk."""
        try:
            rows = (self.dir / "seg.csv").read_text().split()
        except OSError:
            return []
        out = []
        for r in rows:
            name, a, b = r.split(",")
            if (self.dir / name).exists():
                out.append((name, float(a), float(b)))
        return out

    def cut(self, start, dur, out, timeout=60):
        """Wait until [start, start+dur] is on disk, then frame-accurately cut it out."""
        deadline = time.time() + timeout
        while True:
            segs = [s for s in self.segments() if s[2] > start and s[1] < start + dur]
            if segs and segs[-1][2] >= start + dur - 0.5:
                break
            if time.time() > deadline or not self.alive():
                if not segs:
                    raise RuntimeError("footage for this clip is not on disk")
                break  # stream ended mid-clip: use what we have
            time.sleep(1)
        joined = "concat:" + "|".join(str(self.dir / s[0]) for s in segs)  # .ts chunks concat byte-wise
        extract([joined], start - segs[0][1], dur, out)

    def prune(self, keep_from):
        """Delete chunks that end before keep_from (recording-clock seconds)."""
        for name, _, end in self.segments():
            if end < keep_from:
                (self.dir / name).unlink(missing_ok=True)

    def stop(self):
        for p in (self.sl, self.ff):
            p.terminate()
        for p in (self.sl, self.ff):
            try:
                p.wait(10)  # Windows can't delete chunks ffmpeg still has open
            except subprocess.TimeoutExpired:
                p.kill()


def extract(inputs, start, dur, out):
    """Frame-accurate cut of [start, start+dur] from one or more inputs (video[, audio]) into an mp4."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for i in inputs:
        cmd += ["-ss", f"{max(start, 0):.2f}", "-i", str(i)]
    cmd += ["-t", f"{dur:.2f}", "-map", "0:v:0", "-map", f"{len(inputs) - 1}:a:0",
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18", "-c:a", "aac", "-b:a", "192k", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode or not Path(out).exists():
        raise RuntimeError(r.stderr.strip()[-500:])
