# clipperr

Chat-spike auto-clipper for streams (built for GTA 6 / Content Rewards). No AI picks the moments: a script watches
chat velocity plus hype words (KEKW, 💀, "clip it"...) and cuts from just before chat starts going off.
The only ML is offline faster-whisper on your GPU, used for the captions.

## Setup (Windows, once)
Needs Python 3.12, an NVIDIA GPU (NVENC encoding + CUDA whisper), and on PATH:
[ffmpeg](https://www.gyan.dev/ffmpeg/builds/) and [Node.js](https://nodejs.org) (yt-dlp uses it for YouTube).

    py -3.12 -m venv .venv
    .venv\Scripts\pip install -r requirements.txt

- **Twitch VODs**: download `TwitchDownloaderCLI` for Windows from
  [lay295/TwitchDownloader releases](https://github.com/lay295/TwitchDownloader/releases) and unzip it into `tools/`.
- **Fonts** (optional): Arial Black, Impact and Segoe UI Black are Windows fonts, so they're not in the repo. Copy them
  in so captions render exactly as in the editor:

      copy C:\Windows\Fonts\ariblk.ttf assets\fonts\ & copy C:\Windows\Fonts\impact.ttf assets\fonts\ & copy C:\Windows\Fonts\seguibl.ttf assets\fonts\

- **Split layout**: drop background gameplay loops (.mp4) into `assets/bg/` (see Layouts below).

The first clip downloads the whisper model (~1.6 GB), so it takes a few minutes longer.

## Run
    run.bat                     -> opens http://127.0.0.1:8765

Paste a URL, pick **live** (channel URL, e.g. https://www.twitch.tv/caseoh_) or **vod** (e.g.
https://www.twitch.tv/videos/123), pick a layout, and press Start. Clips appear as cards: preview, nudge in/out ±2s,
switch layout, add hook text, download, and copy the caption.
Each card shows **why** it was picked: what chat spammed (e.g. `×41 kekw`) and, for YouTube, how much viewers rewatched
that part. VOD jobs also get **+ More clips** (next 5 best moments from the saved chat, no re-download) and
**Download all** (every ready clip, best first, plus `captions.txt`, as one .zip).

## Layouts
- **split**: game on top, background loop on the bottom. Drop your own parkour or Subway-Surfers-style gameplay
  .mp4 files into `assets/bg/`. If that folder is empty, split falls back to full.
- **full**: center-cropped game over a blurred copy of itself.

## What works where (tested 2026-11-16)
| | live | vod / after-live |
|---|---|---|
| Twitch | ✅ chat (~1s lag) + recording | `tools/TwitchDownloaderCLI.exe`: run the check below once |
| YouTube | ✅ chat via yt-dlp (~20s lag, handled) + recording | ✅ chat via yt-dlp (5h VOD in <60s). Fails only if the streamer disabled chat replay |
| Kick | written; run the check below | written (unofficial API); run the check below |

### Check a platform (20s each)
    .venv\Scripts\python -m clipper.selftest https://kick.com/<live-channel> https://www.twitch.tv/videos/<id>

**Kick live fails at the channel lookup (403 / Cloudflare)?** Open `https://kick.com/api/v2/channels/<name>` in your
browser, find `"chatroom":{"id":12345`, and use the URL `https://kick.com/<name>?chatroom=12345`.

## Tuning (`config.toml`)
- Too many clips: raise `k_mad` / `min_ratio`. Too few: lower them.
- Clips start too late or too early: change `pre_roll`. In live mode only, also `sync_offset`.
- Captions: edit `caption` under `[render]`. Fields: `{name}`, `{tag}`, `{title}`, `{chat}` (chat's top reaction).
- Live mode keeps only the last `keep_s` seconds of footage (~150 MB) and deletes the recording when the job ends. Only clips stay.

## Test
    .venv\Scripts\python -m pytest tests
