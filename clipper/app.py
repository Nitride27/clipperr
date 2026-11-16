"""Local dashboard: uvicorn clipper.app:app"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import jobs, render

app = FastAPI()
jobs.OUT.mkdir(exist_ok=True)
jobs.load_existing()
app.mount("/out", StaticFiles(directory=jobs.OUT), name="out")
app.mount("/web", StaticFiles(directory=jobs.ROOT / "web"), name="web")
render.BG_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/fonts", StaticFiles(directory=render.FONT_DIR), name="fonts")


class NewJob(BaseModel):
    url: str
    mode: str = "vod"
    layout: str = "split"


class Edit(BaseModel):
    d_start: float = 0
    d_end: float = 0
    layout: str | None = None
    hook: str | None = None
    style: dict | None = None


class Style(BaseModel):
    style: dict


def _job(jid):
    if jid not in jobs.JOBS:
        raise HTTPException(404)
    return jobs.JOBS[jid]


@app.get("/")
def index():
    return FileResponse(jobs.ROOT / "web" / "index.html")


@app.get("/api/jobs")
def list_jobs():
    return [j.view() for j in sorted(jobs.JOBS.values(), key=lambda j: -j.meta["created"])]


@app.post("/api/jobs")
def new_job(b: NewJob):
    if not b.url.startswith("http") or b.mode not in ("vod", "live"):
        raise HTTPException(400, "need an http(s) URL and mode vod|live")
    j = jobs.Job(b.url.strip(), b.mode, b.layout)
    j.start()
    return j.view()


@app.post("/api/jobs/{jid}/stop")
def stop_job(jid: str):
    _job(jid).stop.set()
    return {"ok": True}


@app.post("/api/jobs/{jid}/more")
def more_clips(jid: str, n: int = 5):
    j = _job(jid)
    if not j.view()["can_more"] or j.meta["status"] in ("running", "starting"):
        raise HTTPException(409, "only for a finished VOD job whose chat was saved")
    j.more(min(max(n, 1), 30))
    return {"ok": True}


@app.get("/api/jobs/{jid}/zip")
def zip_clips(jid: str):
    """Every ready clip + captions.txt in one zip, stored not compressed (mp4 already is)."""
    import zipfile
    j = _job(jid)
    ready = sorted((c for c in j.clips.values() if c["status"] == "ready"), key=lambda c: -c["score"])
    if not ready:
        raise HTTPException(404, "no ready clips")
    out = j.dir / "clips.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for i, c in enumerate(ready, 1):
            z.write(j.dir / f"{c['id']}.mp4", f"{i:02d}_{c['id']}.mp4")
        z.writestr("captions.txt", "\n\n".join(f"{i:02d}_{c['id']}: {c['caption']}" for i, c in enumerate(ready, 1)))
    return FileResponse(out, filename=f"{j.meta['name']}_clips.zip")


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    _job(jid).delete()
    return {"ok": True}


@app.post("/api/jobs/{jid}/clips/{cid}")
def edit_clip(jid: str, cid: str, b: Edit):
    j = _job(jid)
    if cid not in j.clips:
        raise HTTPException(404)
    j.edit(cid, b.d_start, b.d_end, b.layout, b.hook, b.style)
    return j.clips[cid]


class Mark(BaseModel):
    posted: bool


@app.post("/api/jobs/{jid}/clips/{cid}/posted")
def mark_clip(jid: str, cid: str, b: Mark):
    j = _job(jid)
    if cid not in j.clips:
        raise HTTPException(404)
    j.mark(cid, b.posted)
    return j.clips[cid]


@app.get("/api/info")
def info():
    from .render import bg_files
    return {"bg_loops": len(bg_files())}


@app.delete("/api/jobs/{jid}/clips/{cid}")
def delete_clip(jid: str, cid: str):
    j = _job(jid)
    if j.clips.pop(cid, None) is None:
        raise HTTPException(404)
    (j.dir / f"{cid}.json").unlink(missing_ok=True)  # first, so the clip can't come back after a restart
    for p in j.dir.glob(f"{cid}*"):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass  # the browser may still be playing the mp4 (locked on Windows); it's orphaned, not shown
    return {"ok": True}


@app.get("/api/style")
def get_style():
    return {"style": jobs.default_style(), "fonts": [{"name": n, "file": f} for n, f in render.FONTS.items()]}


@app.put("/api/style")
def put_style(b: Style):
    return {"style": jobs.save_default_style(b.style)}


@app.post("/api/jobs/{jid}/style")
def restyle_job(jid: str, b: Style):
    _job(jid).restyle_all(b.style)
    return {"ok": True}


@app.get("/api/bg")
def list_bg():
    return [{"name": p.name} for p in render.bg_files()]


@app.get("/api/bg/{name}/thumb")
def bg_thumb(name: str):
    p = next((x for x in render.bg_files() if x.name == name), None)
    if not p:
        raise HTTPException(404)
    return FileResponse(render.bg_thumb(p))


@app.post("/api/bg/open")
def open_bg():
    os.startfile(render.BG_DIR)  # local desktop app: open the folder so the user can drop loops in
    return {"ok": True}


@app.get("/api/jobs/{jid}/clips/{cid}/frame")
def clip_frame(jid: str, cid: str):
    j = _job(jid)
    c = j.clips.get(cid)
    src = j.dir / f"{cid}_src.mp4"
    if not c or not src.exists():
        raise HTTPException(404)
    at = (c["start"] + c["end"]) / 2 - c["src_start"]
    return FileResponse(render.frame(src, at, j.dir / f"{cid}_frame.jpg"))
