"""Unified chat reader: iter_chat(url, live) yields (t, text).
VOD: t = seconds into the video. Live: t = wall-clock epoch seconds."""
import asyncio
import json
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Kick rotates this Pusher key (old one died 2026-10). Symptom: 0 Kick msgs + "pusher:error 4001" -> update the key.
KICK_WS = "wss://ws-us2.pusher.com/app/34bf7a0ff419a2a775b9?protocol=7&client=js&version=8.4.0&flash=false"


def iter_chat(url, live, stop=None):
    if "kick.com" in url:
        yield from (_kick_live(url, stop) if live else _kick_vod(url))
        return
    if "youtube.com" in url or "youtu.be" in url:
        yield from _youtube(url, live, stop)
        return
    if not live and (m := re.search(r"twitch\.tv/videos/(\d+)", url)):
        yield from _twitch_vod(m.group(1))
        return
    from chat_downloader import ChatDownloader  # Twitch live (IRC) still works fine with it
    for m in ChatDownloader().get_chat(url, message_groups=["messages"]):
        if stop and stop.is_set():
            return
        text = m.get("message") or ""
        if live:
            ts = m.get("timestamp")
            yield (ts / 1e6 if ts else time.time()), text
        elif m.get("time_in_seconds") is not None:
            yield m["time_in_seconds"], text


def _yt_text(runs):
    return "".join(r.get("text") or (r.get("emoji", {}).get("shortcuts") or [""])[0] for r in runs)


def _youtube(url, live, stop):
    """chat-downloader can't parse YouTube anymore; yt-dlp's live_chat 'subtitle' is maintained and handles
    both replay (whole 5h VOD in <60s) and live (it polls and appends JSON lines to a .part file we tail)."""
    from .source import _exe
    d = Path(tempfile.mkdtemp(prefix="ytchat_"))
    p = subprocess.Popen([_exe("yt-dlp"), "--js-runtimes", "node", "--skip-download", "--write-subs",
                          "--sub-langs", "live_chat", "--no-progress", "-q", "-o", str(d / "c"), url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    files, pos, buf, got = [d / "c.live_chat.json.part", d / "c.live_chat.json"], 0, b"", 0
    try:
        while True:
            done = p.poll() is not None
            f, chunk = next((x for x in files if x.exists()), None), b""
            if f:
                try:
                    with open(f, "rb") as fh:  # .part gets renamed when finished; same bytes, so pos stays valid
                        fh.seek(pos)
                        chunk = fh.read()
                except OSError:
                    chunk = b""
                pos += len(chunk)
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    if not line.strip():
                        continue
                    ra = json.loads(line).get("replayChatItemAction", {})
                    for a in ra.get("actions", []):
                        it = a.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
                        if not it:
                            continue
                        got += 1
                        text = _yt_text(it.get("message", {}).get("runs", []))
                        if live:
                            yield int(it.get("timestampUsec", time.time() * 1e6)) / 1e6, text
                        else:
                            yield int(ra.get("videoOffsetTimeMsec", 0)) / 1000, text
            if stop and stop.is_set():
                return
            if done and not chunk:  # process exited and file fully drained
                break
            time.sleep(0.5 if live else 0.1)
        if not got:
            err = p.stderr.read().decode(errors="ignore").strip()
            raise RuntimeError("no YouTube chat (replay disabled by streamer, or members-only)" + (f": {err[-300:]}" if err else ""))
    finally:
        p.kill()
        shutil.rmtree(d, ignore_errors=True)


def _twitch_vod(vid):
    """chat-downloader's Twitch replay is broken; use TwitchDownloaderCLI (github.com/lay295/TwitchDownloader)."""
    exe = shutil.which("TwitchDownloaderCLI") or next(
        (str(p) for p in Path(__file__).parent.parent.glob("tools/TwitchDownloaderCLI*.exe")), None)
    if not exe:
        raise RuntimeError("Twitch VOD chat needs TwitchDownloaderCLI.exe in clipperr/tools/ (see README)")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "chat.json"
        r = subprocess.run([exe, "chatdownload", "--id", vid, "-o", str(out), "--compression", "None",
                            "--threads", "8", "--collision", "Overwrite"],
                           capture_output=True, text=True)
        if r.returncode or not out.exists():
            raise RuntimeError("TwitchDownloaderCLI failed: " + (r.stderr or r.stdout)[-300:])
        data = json.loads(out.read_text("utf-8"))
    for c in data["comments"]:
        yield c["content_offset_seconds"], c["message"]["body"]


def _kick_get(path, **params):
    from curl_cffi import requests  # kick.com sits behind Cloudflare; plain requests gets 403
    r = requests.get("https://kick.com" + path, params=params, impersonate="chrome", timeout=20)
    r.raise_for_status()
    return r.json()


def _kick_slug(url):
    return re.search(r"kick\.com/([^/?#]+)", url).group(1)


def _kick_live(url, stop):
    # ?chatroom=ID override: if Cloudflare blocks the lookup, open kick.com/api/v2/channels/<name> in your
    # browser, copy "chatroom":{"id":...}, and paste the URL as kick.com/<name>?chatroom=<id>
    m = re.search(r"[?&]chatroom=(\d+)", url)
    room = m.group(1) if m else _kick_get(f"/api/v2/channels/{_kick_slug(url)}")["chatroom"]["id"]
    q = queue.Queue()

    async def run():
        import websockets
        async with websockets.connect(KICK_WS) as ws:
            await ws.send(json.dumps({"event": "pusher:subscribe",
                                      "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
            async for raw in ws:
                if stop and stop.is_set():
                    return
                ev = json.loads(raw)
                if ev.get("event") == "App\\Events\\ChatMessageEvent":
                    q.put((time.time(), json.loads(ev["data"]).get("content", "")))

    def loop():
        while not (stop and stop.is_set()):
            try:
                asyncio.run(run())
            except Exception as e:  # reconnect on drop
                print("kick chat reconnect:", e)
                time.sleep(2)
        q.put(None)

    threading.Thread(target=loop, daemon=True).start()
    while (item := q.get()) is not None:
        yield item


def _kick_vod(url):
    # shortcut: unofficial endpoint; returns a few seconds of chat per call, so a 6h VOD is ~4k requests.
    # If Kick changes it, record the stream live instead.
    uuid = re.search(r"/videos?/([0-9a-f-]{36})", url).group(1)
    v = _kick_get(f"/api/v1/video/{uuid}")
    ls = v["livestream"]
    start = datetime.fromisoformat(ls["start_time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
    dur = (ls.get("duration") or 0) / 1000 or 6 * 3600
    cur, seen = start, set()
    while (cur - start).total_seconds() < dur:
        data = _kick_get(f"/api/v2/channels/{ls['channel_id']}/messages",
                         start_time=cur.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        msgs = (data.get("data") or {}).get("messages") or []
        for m in msgs:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            t = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
            yield (t - start).total_seconds(), m.get("content", "")
        cur += timedelta(seconds=5)
