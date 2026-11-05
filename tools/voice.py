"""Voice: read an episode's script aloud with VOICEVOX and time the subtitles to it.

Each sentence ("say") is synthesized on its own, so its start and end are known from the lengths alone (no speech
recognition). The sentences are joined with short pauses (longer between parts), loudness-normalized by ffmpeg and
written as one mp3:
  audio/<date>-<lang>.mp3        the voice
  episodes/<date>-<lang>.json    gets seconds, lines_timed, segments, made_at (the rest of the script is left as it is)
  episodes.json                  gets (or updates) this episode's title and length

usage: python tools/voice.py episodes/<date>-<lang>.json [--speaker NAME_OR_ID] [--style STYLE] [--speed 1.1]
  --speaker  a VOICEVOX speaker name or style id (default: env VOICEVOX_SPEAKER, else 冥鳴ひまり)
  --style    the speaker's style when a name is given (default: ノーマル)
  --speed    speedScale (default: 1.1)
Needs the VOICEVOX engine running (env VOICEVOX_URL, default http://127.0.0.1:50021) and ffmpeg.
"""
import datetime, io, json, os, subprocess, sys, tempfile, urllib.parse, urllib.request, wave

ENGINE = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")
LEAD, GAP, PART_GAP, TAIL = 0.6, 0.25, 1.05, 0.8   # seconds of silence: before the first line, between lines, between parts, after the last


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def call(path, params, body=None):
    url = f"{ENGINE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.read()


def style_id(speaker, style):
    if str(speaker).isdigit():
        return int(speaker)
    for s in json.loads(call("/speakers", {})):
        if s["name"] == speaker:
            for st in s["styles"]:
                if st["name"] == style:
                    return st["id"]
            raise SystemExit(f"{speaker} has no style {style}: {[st['name'] for st in s['styles']]}")
    raise SystemExit(f"no VOICEVOX speaker {speaker}")


def synth(text, sid, speed):
    q = json.loads(call("/audio_query", {"text": text, "speaker": sid}, b""))
    q["speedScale"] = speed
    q["prePhonemeLength"] = q["postPhonemeLength"] = 0.05
    return call("/synthesis", {"speaker": sid}, json.dumps(q).encode())


def main():
    path = sys.argv[1]
    root = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    ep = json.load(open(path, encoding="utf-8"))
    date, lang = ep["date"], ep.get("lang", "ja")
    sid = style_id(arg("--speaker", os.environ.get("VOICEVOX_SPEAKER", "冥鳴ひまり")), arg("--style", "ノーマル"))
    speed = float(arg("--speed", 1.1))

    parts = [("opening", None, ep["opening"])] + [("news", i, n["lines"]) for i, n in enumerate(ep["news"])] + [("closing", None, ep["closing"])]
    frames, rate, params = [], None, None
    t, timed, segments = LEAD, [], []
    for p, (kind, index, lines) in enumerate(parts):
        if p:
            t += PART_GAP - GAP
        seg_start = None
        for line in lines:
            w = wave.open(io.BytesIO(synth(line["say"], sid, speed)))
            if params is None:
                params, rate = w.getparams(), w.getframerate()
                frames.append(b"\0" * int(LEAD * rate) * w.getsampwidth() * w.getnchannels())
            elif timed:
                pause = PART_GAP if seg_start is None else GAP
                frames.append(b"\0" * int(pause * rate) * params.sampwidth * params.nchannels)
            data = w.readframes(w.getnframes())
            length = w.getnframes() / rate
            start = round(t, 3)
            seg_start = start if seg_start is None else seg_start
            t += length
            timed.append({"start": start, "end": round(t, 3), "show": line["show"], "part": p})
            frames.append(data)
            t += GAP
            print(f"{start:7.2f}s  {line['show']}", flush=True)
        segments.append({"kind": kind, "index": index, "start": seg_start, "end": None})
    for a, b in zip(segments, segments[1:]):
        a["end"] = b["start"]
    segments[-1]["end"] = timed[-1]["end"]
    frames.append(b"\0" * int(TAIL * rate) * params.sampwidth * params.nchannels)

    os.makedirs(os.path.join(root, "audio"), exist_ok=True)
    mp3 = os.path.join(root, "audio", f"{date}-{lang}.mp3")
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "all.wav")
        with wave.open(wav, "wb") as out:
            out.setparams(params)
            out.writeframes(b"".join(frames))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                        "-ar", "44100", "-ac", "1", "-b:a", "96k", mp3], check=True)

    ep.update(format=ep.get("format", "monoradio/news-1"), lang=lang, seconds=round(timed[-1]["end"] + TAIL, 2),
              lines_timed=timed, segments=segments, made_at=datetime.datetime.now().isoformat(timespec="seconds"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ep, f, ensure_ascii=False, indent=1)

    listing = os.path.join(root, "episodes.json")
    episodes = json.load(open(listing, encoding="utf-8")) if os.path.exists(listing) else []
    entry = next((e for e in episodes if e["date"] == date), None)
    if entry is None:
        entry = {"date": date}
        episodes.append(entry)
    entry[lang] = {"title": ep["title"], "seconds": ep["seconds"]}
    episodes.sort(key=lambda e: e["date"], reverse=True)
    with open(listing, "w", encoding="utf-8") as f:
        json.dump(episodes, f, ensure_ascii=False)
    print(f"{mp3}: {ep['seconds']}s, {len(timed)} lines")


if __name__ == "__main__":
    main()
