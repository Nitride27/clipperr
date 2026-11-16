"""Check chat sources. Usage:
    .venv\\Scripts\\python -m clipper.selftest <url> [<url> ...]
Live URLs (channel pages) are read for 20s; VOD URLs until 2000 messages."""
import sys
import threading
import time

from .chat import iter_chat


def check(url):
    live = not any(k in url for k in ("/videos/", "/video/", "watch?v=", "youtu.be/"))
    stop, t, st = threading.Event(), time.time(), {"n": 0, "last": None, "err": None}

    def read():
        try:
            for ts, _ in iter_chat(url, live, stop):
                st["n"], st["last"] = st["n"] + 1, ts
                if (live and time.time() - t > 20) or (not live and st["n"] >= 2000):
                    break
        except Exception as e:
            st["err"] = e
    th = threading.Thread(target=read, daemon=True)
    th.start()
    th.join(30 if live else 900)  # a silent/offline channel never yields, so don't wait on it forever
    stop.set()
    if st["err"]:
        return f"FAIL  {url}\n      {str(st['err'])[-300:]}"
    n, last = st["n"], st["last"]
    if not n:
        return f"FAIL  {url}  (0 messages; offline or quiet channel?)"
    what = f"lag {time.time() - last:.1f}s" if live else f"reached {int(last // 60)} min into VOD"
    return f"OK    {url}  {n} msgs in {time.time() - t:.0f}s, {what}"


if __name__ == "__main__":
    for u in sys.argv[1:] or [print(__doc__) or exit()]:
        print(check(u), flush=True)
