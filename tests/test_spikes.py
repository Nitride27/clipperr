import pytest
import random
import tomllib
from pathlib import Path

from clipper.spikes import detect

CFG = tomllib.loads((Path(__file__).parent.parent / "config.toml").read_text("utf-8"))


def chat(spikes):
    random.seed(1)
    ev = []
    for t in range(600):
        rate = 2 + random.random() * 2
        for a, b in spikes:
            if a <= t < b:
                rate = 40
        ev += [(t + random.random(), "KEKW" if rate > 10 else "hello chat") for _ in range(int(rate))]
    return ev


def test_finds_spike_with_lead_in():
    wins = detect(chat([(300, 308)]), CFG["spike"], CFG["weights"])
    assert len(wins) == 1, wins
    w = wins[0]
    assert w["start"] <= 300 - 15 and w["end"] >= 306 and 300 <= w["peak"] < 310
    assert CFG["spike"]["min_len"] <= w["end"] - w["start"] <= CFG["spike"]["max_len"]


def test_quiet_chat_no_spike():
    assert detect(chat([]), CFG["spike"], CFG["weights"]) == []


def test_two_spikes_ranked():
    wins = detect(chat([(150, 153), (400, 412)]), CFG["spike"], CFG["weights"])
    assert len(wins) == 2 and 400 <= wins[0]["peak"] < 415  # longer spike scores higher


def test_vod_relaxing_finds_more_on_quiet_chat():
    random.seed(2)
    ev = [(t + random.random(), "lol") for t in range(3000) for _ in range(random.choice([0, 1, 1, 2]))]
    for k in range(10):  # ten modest bumps, ~2x normal
        ev += [(300 * k + 100 + random.random() * 6, "LMAO") for _ in range(10)]
    strict = detect(ev, CFG["spike"], CFG["weights"])
    loose = detect(ev, {**CFG["spike"], "k_mad": 1.6, "min_ratio": 1.4, "min_rate": 0.4}, CFG["weights"])
    assert len(loose) > len(strict) and len(loose) >= 8


def test_keywords_match_words_not_substrings():
    from clipper.spikes import msg_weight
    W = CFG["weights"]
    assert msg_weight("total eclipse tonight", W) == 1.0  # "clip" inside a word
    assert msg_weight("now that is cool", W) == 1.0  # "w" inside a word
    assert msg_weight("W", W) == 1.5
    assert msg_weight("clip it", W) == 4.0
    assert msg_weight("noooo way", W) == 1.0 + 1.5 + 0.5  # squashed to "no way" + stretch bonus
    assert msg_weight("hahahaha", W) == 2.0


def test_heatmap_reranks():
    from clipper.spikes import boost
    wins = [{"start": 0, "end": 30, "score": 100.0}, {"start": 100, "end": 130, "score": 80.0}]
    heat = [{"start_time": 0, "end_time": 50, "value": 0.1}, {"start_time": 90, "end_time": 140, "value": 1.0}]
    assert [w["start"] for w in boost(wins, heat)] == [100, 0]
    assert boost(wins, []) is wins


def test_snap_to_speech_pauses():
    from clipper.subs import snap
    words = [{"w": "a", "s": 0.0, "e": 4.0}, {"w": "b", "s": 5.0, "e": 9.8}, {"w": "c", "s": 11.0, "e": 20.0}]
    assert snap(words, 6.0, 12.0) == pytest.approx((4.85, 10.1))  # start back to "b", end just after "b"
    assert snap([], 6.0, 12.0) == (6.0, 12.0)
    assert snap(words, 15.0, 16.0, slack=1) == (15.0, 16.0)  # no pause within reach


def test_bang_commands_ignored():
    from clipper.spikes import series
    assert series([(0, "!enter"), (1, "!enter"), (1, "lol")], CFG["weights"])[1] == [0.0, 1.0]


def test_chat_says_top_reactions():
    from clipper.spikes import chat_says
    ev = [(5, "KEKWWW"), (6, "kekw"), (7, "kekw"), (8, "no way"), (9, "NO WAY"), (10, "hi"), (50, "kekw"), (6, "!enter")]
    assert chat_says(ev, 0, 20) == [["kekw", 3], ["no way", 2]]


def test_close_spikes_never_share_footage():
    # bumps every 70s under relaxed (VOD fallback) thresholds: too long to merge, close enough to overlap
    random.seed(1)
    ev = [(t + random.random(), "lol") for t in range(1500) for _ in range(random.choice([0, 1, 1, 2]))]
    ev += [(70 * k + 100 + random.random() * 6, "LMAO") for k in range(20) for _ in range(8)]
    loose = {**CFG["spike"], "k_mad": 1.6, "min_ratio": 1.4, "min_rate": 0.4}
    wins = sorted(detect(ev, loose, CFG["weights"]), key=lambda w: w["start"])
    assert len(wins) > 3 and all(a["end"] <= b["start"] for a, b in zip(wins, wins[1:])), wins


def test_chat_says_skips_links():
    from clipper.spikes import chat_says
    assert chat_says([(1, "https://x.tv/a")] * 5 + [(2, "kekw")] * 3, 0, 5) == [["kekw", 3]]
