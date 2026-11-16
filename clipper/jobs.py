"""Job orchestration: VOD (chat replay -> top spikes -> fetch only those sections) and live (record + watch chat)."""
import gzip
import json
import queue
import re
import shutil
import threading
import time
import tomllib
import traceback
import uuid
from collections import deque
from pathlib import Path

from . import render, source, subs
from .chat import iter_chat
from .spikes import boost, chat_says, detect_series, series

ROOT = Path(__file__).parent.parent
OUT = ROOT / "out"
CFG = tomllib.loads((ROOT / "config.toml").read_text("utf-8"))
JOBS = {}
STYLE_FILE = OUT / "style.json"


def default_style():
    """Saved house style (from the editor's "Save as default"), seeded from config.toml."""
    base = {**subs.DEFAULT_STYLE, "layout": CFG["render"]["layout"], "font": CFG["render"]["font"],
            "size": CFG["render"]["font_size"], "words": CFG["render"]["words_per_line"],
            "zoom": CFG["render"]["game_zoom"]}
    try:
        return {**base, **subs.clean_style(json.loads(STYLE_FILE.read_text("utf-8")))}
    except (OSError, ValueError):
        return base


def save_default_style(style):
    OUT.mkdir(exist_ok=True)
    st = {**default_style(), **subs.clean_style(style)}
    STYLE_FILE.write_text(json.dumps(st), "utf-8")
    return st


def _name_from_url(url):
    m = re.search(r"(?:twitch\.tv|kick\.com)/([^/?#]+)", url)
    return m.group(1) if m and m.group(1) not in ("videos", "video") else "streamer"


class Job:
    def __init__(self, url=None, mode="vod", layout=None, meta=None):
        self.meta = meta or {"id": time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4], "url": url, "mode": mode,
                             "layout": layout or CFG["render"]["layout"], "status": "starting", "log": "",
                             "name": _name_from_url(url), "created": time.time()}
        self.dir = OUT / self.meta["id"]
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clips = {}
        for p in sorted(self.dir.glob("clip_[0-9][0-9][0-9].json")):  # not clip_001_words.json
            c = json.loads(p.read_text("utf-8"))
            c.setdefault("style", {**default_style(), "layout": c.get("layout", "full")})  # clips from before styles
            self.clips[c["id"]] = c
        self.stop, self.q, self.inputs, self.rec = threading.Event(), queue.Queue(), None, None
        self.deleted = False
        JOBS[self.meta["id"]] = self

    def delete(self):
        """Stop everything, then remove the folder. Workers mid-render finish their ffmpeg call but write nothing."""
        self.deleted = True
        self.stop.set()
        JOBS.pop(self.meta["id"], None)
        for _ in range(2):
            self.q.put(None)

        def rm():  # files a running ffmpeg or the browser still holds stay locked on Windows for a while
            for _ in range(120):
                shutil.rmtree(self.dir, ignore_errors=True)
                if not self.dir.exists():
                    return
                time.sleep(2)
        threading.Thread(target=rm, daemon=True).start()

    # --- state ---
    def save(self):
        if not self.deleted:
            (self.dir / "job.json").write_text(json.dumps(self.meta), "utf-8")

    def save_clip(self, c):
        if not self.deleted:
            (self.dir / f"{c['id']}.json").write_text(json.dumps(c), "utf-8")

    def log(self, msg, status=None):
        print(f"[{self.meta['id']}] {msg}")
        self.meta["log"] = msg
        if status:
            self.meta["status"] = status
        self.save()

    def view(self):
        return {**self.meta, "can_more": self.meta["mode"] == "vod" and (self.dir / "chat.json.gz").exists(),
                "clips": sorted(self.clips.values(), key=lambda c: c["created"], reverse=True)}

    # --- clip pipeline ---
    def add_clip(self, w, src_start, src_dur, activity):
        cid = f"clip_{max((int(k[5:]) for k in self.clips), default=0) + 1:03d}"  # never reuse a deleted id
        c = {"id": cid, "start": w["start"], "end": w["end"], "peak": w["peak"], "score": w["score"],
             "src_start": src_start, "src_dur": src_dur, "hook": "", "why": w.get("why", []), "replay": w.get("replay"),
             "style": {**default_style(), "layout": self.meta["layout"]},
             "status": "queued", "v": 0, "posted": False, "created": time.time(), "activity": activity,
             "caption": self._caption(w)}
        self.clips[cid] = c
        self.save_clip(c)
        self.q.put(cid)

    def _caption(self, w):
        top = (w.get("why") or [[""]])[0][0]
        vals = {"name": self.meta["name"], "tag": re.sub(r"\W", "", self.meta["name"].lower()),
                "title": self.meta.get("title", ""), "chat": top.upper()}
        try:
            return " ".join(CFG["render"]["caption"].format(**vals).split())  # collapse gaps from empty fields
        except (KeyError, ValueError, IndexError):
            return CFG["render"]["caption"]  # typo in the template: show it raw rather than fail the clip

    def worker(self):
        while True:
            cid = self.q.get()
            if cid is None or self.deleted:
                return
            c = self.clips.get(cid)
            if not c:
                continue
            try:
                src = self.dir / f"{cid}_src.mp4"
                c["status"] = "fetching"
                if self.rec:
                    self.rec.cut(c["src_start"], c["src_dur"], src)
                else:
                    source.extract(self.inputs, c["src_start"], c["src_dur"], src)
                c["status"] = "transcribing"
                words = subs.transcribe(src, CFG["render"]["whisper_model"])
                (self.dir / f"{cid}_words.json").write_text(json.dumps(words), "utf-8")
                a, b = subs.snap(words, c["start"] - c["src_start"], c["end"] - c["src_start"])
                others = [o for o in list(self.clips.values()) if o is not c]  # snapping must not cross a neighbour
                lo = max([c["src_start"]] + [o["end"] for o in others if o["end"] <= c["start"] + 0.5])
                hi = min([c["src_start"] + c["src_dur"]] + [o["start"] for o in others if o["start"] >= c["end"] - 0.5])
                na, nb = round(max(c["src_start"] + a, lo), 2), round(min(c["src_start"] + b, hi), 2)
                if nb - na >= 3:
                    c["start"], c["end"] = na, nb
                self.render_clip(c)
            except Exception as e:
                traceback.print_exc()
                c["status"], c["error"] = "error", str(e)[-300:]
                self.save_clip(c)

    def render_clip(self, c):
        c["status"] = "rendering"
        cid = c["id"]
        words = json.loads((self.dir / f"{cid}_words.json").read_text("utf-8"))
        off, dur = c["start"] - c["src_start"], c["end"] - c["start"]
        st = dict(c["style"])
        if st["layout"] == "split" and not render.bg_files():
            st["layout"] = "full"  # captions must follow the layout that actually renders
        ass = subs.build_ass(words, off, dur, st, c["hook"])
        (self.dir / f"{cid}.ass").write_text(ass, "utf-8")
        c["layout_used"] = render.render(self.dir / f"{cid}_src.mp4", off, dur, f"{cid}.ass",
                                         self.dir / f"{cid}.mp4", st)
        c["status"], c["v"] = "ready", c["v"] + 1
        c.pop("error", None)
        self.save_clip(c)

    def mark(self, cid, posted):
        self.clips[cid]["posted"] = bool(posted)
        self.save_clip(self.clips[cid])

    def edit(self, cid, d_start=0, d_end=0, layout=None, hook=None, style=None):
        c = self.clips[cid]
        c["start"] = max(c["src_start"], c["start"] + d_start)
        c["end"] = min(c["src_start"] + c["src_dur"], max(c["start"] + 3, c["end"] + d_end))
        if layout in ("split", "full"):
            c["style"]["layout"] = layout
        if style:
            c["style"].update(subs.clean_style(style))
        if hook is not None:
            c["hook"] = hook[:80]
        if c["status"] in ("queued", "fetching", "transcribing"):
            return self.save_clip(c)  # first render will pick the new settings up
        c["status"] = "rendering"

        def go():
            try:
                self.render_clip(c)
            except Exception as e:
                c["status"], c["error"] = "error", str(e)[-300:]
                self.save_clip(c)
        threading.Thread(target=go, daemon=True).start()

    def restyle_all(self, style):
        """Apply one style to every clip, re-rendering finished ones (2 at a time via render's GPU gate)."""
        st = subs.clean_style(style)
        for c in list(self.clips.values()):
            c["style"].update(st)
            if c["status"] in ("ready", "error") and (self.dir / f"{c['id']}_words.json").exists():
                self.edit(c["id"])
            else:
                self.save_clip(c)

    # --- runners ---
    def start(self):
        target = self.run_live if self.meta["mode"] == "live" else self.run_vod
        threading.Thread(target=self._guard, args=(target,), daemon=True).start()
        self._workers()

    def _workers(self):
        if getattr(self, "_w", False):
            return
        self._w = True
        for _ in range(2):  # one fetches the next clip while the other transcribes/renders
            threading.Thread(target=self.worker, daemon=True).start()

    def _pick(self, t0, s, events, heat, n, taken=()):
        """Best n spike windows not overlapping `taken` [(start, end)], relaxing thresholds until n are found."""
        sp = CFG["spike"]
        # VOD wants the best N moments of *this* stream; quiet chats rarely clear the live thresholds, so relax
        for f in (1, 0.75, 0.55, 0.4, 0.3):
            cfg = {**sp, "k_mad": sp["k_mad"] * f, "min_ratio": max(1.3, sp["min_ratio"] * f),
                   "min_rate": sp["min_rate"] * f}
            wins = [w for w in detect_series(t0, s, cfg) if not any(w["start"] < b and w["end"] > a for a, b in taken)]
            if len(wins) >= n:
                break
        wins = boost(wins, heat)[:n]  # rank before cutting to n
        for w in wins:
            w["why"] = chat_says(events, w["peak"] - 8, w["run_end"] + 3)  # the reaction, not the lead-in chatter
        return wins

    def _drain(self):
        while not self.q.empty() or any(c["status"] in ("queued", "fetching", "transcribing", "rendering")
                                        for c in list(self.clips.values())):
            time.sleep(1)

    def more(self, n=5):
        """VOD: render the next-best n moments from the saved chat, no re-download."""
        def go():
            self.log(f"finding {n} more moments", "running")
            if not self.inputs:  # reloaded job: stream URLs from the first run have expired
                self.inputs = source.info(self.meta["url"])["urls"]
            d = json.loads(gzip.decompress((self.dir / "chat.json.gz").read_bytes()))
            events = [tuple(e) for e in d["events"]]
            t0, s = series(events, CFG["weights"])
            wins = self._pick(t0, s, events, d["heatmap"], n, [(c["start"], c["end"]) for c in self.clips.values()])
            if not wins:
                return self.log("no more moments in this chat", "done")
            self._workers()
            pad = CFG["live"]["pad"]
            for w in wins:
                self.add_clip(w, w["start"] - pad, w["end"] - w["start"] + 2 * pad, self._activity(t0, s, w))
            self._drain()
            self.log(f"done: {len(self.clips)} clips", "done")
        threading.Thread(target=self._guard, args=(go,), daemon=True).start()

    def _guard(self, fn):
        try:
            fn()
        except Exception as e:
            traceback.print_exc()
            self.log(f"failed: {e}", "error")

    def _activity(self, t0, s, w):
        a, b = int(w["start"] - 30 - t0), int(w["end"] + 30 - t0)
        return {"t0": t0 + max(a, 0), "values": [round(x, 1) for x in s[max(a, 0):max(b, 0)]]}

    def run_vod(self):
        self.log("resolving video", "running")
        info = source.info(self.meta["url"])
        self.inputs, self.meta["name"], self.meta["title"] = info["urls"], info["name"], info["title"]
        events = []
        for t, text in iter_chat(self.meta["url"], False, self.stop):
            events.append((t, text))
            if len(events) % 2000 == 0:
                self.log(f"reading chat: {len(events)} msgs, at {int(t // 60)} min")
        if self.stop.is_set():
            return self.log("stopped", "stopped")
        sp, pad = CFG["spike"], CFG["live"]["pad"]
        # keep the chat so "More clips" can dig deeper later without re-downloading it
        (self.dir / "chat.json.gz").write_bytes(gzip.compress(json.dumps(
            {"events": events, "heatmap": info.get("heatmap")}).encode()))
        t0, s = series(events, CFG["weights"])  # weigh chat once; the relax loop only re-thresholds
        wins = self._pick(t0, s, events, info.get("heatmap"), sp["top_n"])
        step = max(1, len(s) // 600)
        self.meta["timeline"] = {"t0": t0, "step": step,
                                 "values": [round(max(s[i:i + step]), 1) for i in range(0, len(s), step)]}
        self.log(f"{len(events)} msgs, {len(wins)} spikes -> rendering", "running")
        for w in wins:
            self.add_clip(w, w["start"] - pad, w["end"] - w["start"] + 2 * pad, self._activity(t0, s, w))
        self._drain()
        self.log(f"done: {len(wins)} clips", "done")

    def run_live(self):
        url, lv, sp = self.meta["url"], CFG["live"], CFG["spike"]
        try:
            i = source.info(url)
            self.meta["name"], self.meta["title"] = i["name"], i["title"]
        except Exception:
            pass  # name from URL is fine
        rec = self.rec = source.Recording(url, self.dir / "rec")
        self.log("waiting for stream data", "running")
        while not rec.started():
            if not rec.alive():
                return self.log("recorder exited (is the channel live?)", "error")
            time.sleep(0.2)
        t0_wall = time.time()  # shortcut: recording time 0 = first chunk on disk; tune [live] sync_offset if clips land early/late

        events = deque()
        state = {"n": 0}

        def read_chat():
            try:
                for t, text in iter_chat(url, True, self.stop):
                    events.append((t, text))
                    state["n"] += 1
            except Exception as e:
                self.log(f"chat error: {e}")
        threading.Thread(target=read_chat, daemon=True).start()

        emitted, post_pad = [], 4
        while not self.stop.is_set() and rec.alive():
            time.sleep(2)
            now = time.time()
            while events and events[0][0] < now - 900:
                events.popleft()
            recent = list(events)
            # delete footage nobody can need: older than keep_s, and before every clip still waiting to be cut
            pending = [c["src_start"] for c in list(self.clips.values()) if c["status"] in ("queued", "fetching")]
            rec.prune(min([now - t0_wall - lv["keep_s"]] + pending))
            lag = f", chat lag {now - recent[-1][0]:.0f}s" if recent else ""
            self.log(f"live: {state['n']} msgs, recording {int(now - t0_wall)}s, {len(emitted)} clips{lag}")
            if not recent:
                continue
            # judge "spike finished" on the chat clock, not wall clock: YouTube chat arrives ~20s late
            chat_now = max(t for t, _ in recent[-200:])
            ts0, s = series(recent, CFG["weights"], t1=chat_now)
            base = sorted(s[-120:])[len(s[-120:]) // 2] if s else 0
            self.meta["pulse"] = [round(x, 1) for x in s[-180:]]
            self.meta["trigger"] = round(max(base, sp["min_rate"]) * sp["min_ratio"], 1)  # approx: detector also needs a MAD jump
            for w in detect_series(ts0, s, sp):
                settled = w["run_end"] < chat_now - 3 and w["end"] + post_pad < chat_now
                if not settled or any(w["start"] < e and w["end"] > s0 for s0, e in emitted):
                    continue
                emitted.append((w["start"], w["end"]))
                act = self._activity(ts0, s, w)
                w["why"] = chat_says(recent, w["peak"] - 8, w["run_end"] + 3)
                off = lv["sync_offset"] - t0_wall
                w = {**w, "start": w["start"] + off, "end": w["end"] + off, "peak": w["peak"] + off}
                act["t0"] += off
                pre = min(lv["pad"], w["start"])
                self.add_clip(w, w["start"] - pre, w["end"] - w["start"] + pre + post_pad, act)
        rec.stop()
        self.log("finishing clips, then deleting the recording")
        while any(c["status"] in ("queued", "fetching") for c in list(self.clips.values())):
            time.sleep(1)  # cut() reads the chunks; only delete once every clip has its own copy
        for _ in range(5):  # a just-closed chunk can stay locked a moment (antivirus) on Windows
            shutil.rmtree(rec.dir, ignore_errors=True)
            if not rec.dir.exists():
                break
            time.sleep(1)
        self.log("stopped" if self.stop.is_set() else "stream ended", "stopped")


def load_existing():
    for p in sorted(OUT.glob("*/job.json")):
        meta = json.loads(p.read_text("utf-8"))
        if meta["status"] in ("running", "starting"):
            meta["status"] = "stopped"
        Job(meta=meta)
