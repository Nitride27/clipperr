"""Chat-spike detection. Pure functions: events in, clip windows out."""
import re
from collections import Counter
from functools import lru_cache
from statistics import median

_CAPS = re.compile(r"[A-Z]{4,}")
_STRETCH = re.compile(r"([a-z])\1{2,}")  # "NOOOO", "lmaooo", "whattt"


@lru_cache(maxsize=8)
def _matcher(items):
    """One regex for all weight keys. Word keys must start a word ("clip" not in "eclipse"); short ones like "w"
    must also end it ("W" yes, "now" no); longer ones may run on ("pog" -> "poggers", "lmao" -> "lmaoooo")."""
    parts = []
    for i, (k, _) in enumerate(items):
        k = k.strip().lower()
        if not k:
            continue
        pat = re.escape(k)
        if k[0].isalnum():
            pat = r"(?<![a-z0-9])" + pat
        if k[-1].isalnum() and len(k) <= 2:
            pat += r"(?![a-z0-9])"
        parts.append(f"(?P<g{i}>{pat})")
    return (re.compile("|".join(parts)) if parts else None), {f"g{i}": v for i, (_, v) in enumerate(items)}


def msg_weight(text, weights):
    low = text.lower()
    stretched = _STRETCH.search(low)
    norm = _STRETCH.sub(r"\1", low) if stretched else low  # "noooo wayyy" -> "no way" so keys still match
    rx, vals = _matcher(tuple(weights.items()))
    w = 1.0 + (sum(vals[g] for g in {m.lastgroup for m in rx.finditer(norm)}) if rx else 0)
    if _CAPS.search(text):
        w += 0.5
    if stretched:
        w += 0.5
    words = low.split()
    if len(words) >= 3 and len(set(words)) == 1:  # emote spam "KEKW KEKW KEKW"
        w += 1.0
    return w


def series(events, weights, t0=None, t1=None):
    """events: [(t_seconds, text)] -> (t0, per-second weighted scores)."""
    if not events:
        return 0, []
    t0 = int(min(t for t, _ in events)) if t0 is None else int(t0)
    t1 = int(max(t for t, _ in events)) if t1 is None else int(t1)
    s = [0.0] * (t1 - t0 + 1)
    for t, text in events:
        i = int(t) - t0
        if 0 <= i < len(s) and not text.startswith("!"):  # "!enter" giveaway floods aren't moments
            s[i] += msg_weight(text, weights)
    return t0, s


def detect(events, cfg, weights, t1=None):
    """Return clip windows [{start, end, peak, run_end, score}] in the events' time base, best first."""
    return detect_series(*series(events, weights, t1=t1), cfg)


def detect_series(t0, s, cfg):
    """detect() on an already weighted series, so callers re-running with other thresholds skip re-weighting."""
    n, sm_w, base = len(s), cfg["smooth_s"], cfg["baseline_s"]
    if n < 40:
        return []
    sm = [sum(s[max(0, i - sm_w + 1):i + 1]) / sm_w for i in range(n)]

    hot, meds = [False] * n, [0.0] * n
    for i in range(30, n):
        win = sm[max(0, i - base):i - 5]  # skip the last 5s so the spike doesn't raise its own bar
        med = max(median(win), cfg["min_rate"])
        mad = max(median(abs(x - med) for x in win), 0.5)
        meds[i] = med
        hot[i] = sm[i] > med + cfg["k_mad"] * mad and sm[i] > cfg["min_ratio"] * med

    runs, i = [], 0
    while i < n:
        if not hot[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and (hot[j + 1] or (j + 3 < n and any(hot[j + 1:j + 4]))):  # bridge 1-3s dips
            j += 1
        runs.append((i, j))
        i = j + 1

    wins = []
    for a, b in runs:
        peak = max(range(a, b + 1), key=lambda k: sm[k])
        med = meds[a]
        onset = a
        while onset > max(0, a - 15) and sm[onset - 1] > med:  # walk back to where chat started rising
            onset -= 1
        start, end = onset - cfg["pre_roll"], peak + cfg["post_roll"]
        if end - start < cfg["min_len"]:
            start = end - cfg["min_len"]
        if end - start > cfg["max_len"]:
            start = end - cfg["max_len"]
        score = sm[peak] / med * sum(sm[k] / med for k in range(a, b + 1))
        wins.append({"start": t0 + max(start, 0), "end": t0 + end, "peak": t0 + peak,
                     "run_end": t0 + b, "score": round(score, 1)})

    merged = []
    for w in sorted(wins, key=lambda w: w["start"]):
        if merged and w["start"] - merged[-1]["end"] <= cfg["merge_gap"] \
                and w["end"] - merged[-1]["start"] <= cfg["max_len"]:
            m = merged[-1]
            m["end"], m["run_end"] = max(m["end"], w["end"]), max(m["run_end"], w["run_end"])
            if w["score"] > m["score"]:
                m["peak"] = w["peak"]
            m["score"] = round(m["score"] + w["score"], 1)
        elif merged and w["start"] < merged[-1]["end"]:  # too long to merge but overlapping: no footage in two clips
            m = merged[-1]
            if w["end"] - m["end"] >= cfg["min_len"]:
                merged.append({**w, "start": m["end"]})  # the lead-in is already the end of the previous clip
            elif w["score"] > m["score"]:
                merged[-1] = dict(w)  # still starts after merged[-2] ends: w["start"] >= m["start"]
        else:
            merged.append(dict(w))
    return sorted(merged, key=lambda w: -w["score"])


def boost(wins, heat):
    """Re-rank by YouTube's "most replayed" heatmap [{start_time, end_time, value 0..1}]: x0.5 (nobody rewatched)
    to x1.5 (the most rewatched part of the video). Chat says *something* happened; replays say it was worth it."""
    if not heat:
        return wins
    for w in wins:
        h = max((x["value"] for x in heat if x["start_time"] < w["end"] and x["end_time"] > w["start"]), default=0)
        w["score"], w["replay"] = round(w["score"] * (0.5 + h), 1), round(h, 2)
    return sorted(wins, key=lambda w: -w["score"])


def chat_says(events, a, b, n=3):
    """What chat was spamming in [a, b]: [[text, count]] ranked by surge over the same span just before, so
    ever-present chatter ("hi", "lol") loses to the actual reaction. Shown on the clip card and in {chat}."""
    def count(lo, hi):
        return Counter(_STRETCH.sub(r"\1", t.lower()).strip()
                       for x, t in events if lo <= x <= hi and not t.startswith("!") and "://" not in t)
    now, before = count(a, b), count(2 * a - b, a)
    top = sorted(now, key=lambda t: before[t] - now[t])[:n]
    return [[t[:24], now[t]] for t in top if t and now[t] >= 2 and now[t] > before[t]]
