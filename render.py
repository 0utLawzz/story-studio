#!/usr/bin/env python3
"""
Story Studio - story JSON in, finished MP4 out.

Per scene: image (your own file, or free AI image, or placeholder) + free TTS voice
Effects: mood-aware zoom/pan motion, mood-aware transitions between scenes,
word-by-word karaoke captions (timed exactly from the voice engine itself),
light mood-based color tone, optional music.
Usage:  python render.py stories/sample.json
"""
import asyncio
import json
import random
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FPS = 30
XF = 0.7          # transition length (seconds)
LEAD = 0.3        # silence before narration in each scene
TAIL = 1.0        # extra time after narration (includes the transition)

# ---------------------------------------------------------------------------
# CHANGE 1: transitions are now grouped by "mood" instead of one flat list.
# Give any scene a "mood": "happy" / "calm" / "tense" / "reveal" / "sad" in
# the story JSON and the transition INTO that scene is picked from the
# matching pool below -- instead of just cycling blindly through all 10.
# "default" is EXACTLY the old list in the old order, so a story with no
# "mood" field renders with the identical transition pattern as before.
# AFFECTS: which transition plays between two scenes.
# ---------------------------------------------------------------------------
TRANSITION_POOLS = {
    "default": ["fade", "slideleft", "circleopen", "wipeleft", "dissolve",
                "slideup", "radial", "smoothleft", "circleclose", "wiperight"],
    "calm":    ["fade", "dissolve", "smoothleft", "smoothright"],
    "happy":   ["circleopen", "radial", "slideup", "zoomin"],
    "tense":   ["wipeleft", "wiperight", "slideleft", "slideright"],
    "reveal":  ["circleopen", "radial", "circlecrop"],
    "sad":     ["fade", "dissolve", "fadegrays"],
}
ALL_TRANSITIONS = sorted({t for pool in TRANSITION_POOLS.values() for t in pool})

# ---------------------------------------------------------------------------
# CHANGE 2: motion (the Ken Burns zoom/pan) is also grouped by mood now.
# "default" reproduces the OLD idx % 4 cycling exactly, so nothing changes
# for a story that doesn't set "mood".
# AFFECTS: which of the 4 motion styles a scene gets.
# ---------------------------------------------------------------------------
MOTION_POOLS = {
    "default": ["zoom_in", "pan_lr", "zoom_out", "pan_rl"],
    "calm":    ["zoom_in", "pan_lr"],
    "happy":   ["pan_lr", "zoom_in"],
    "tense":   ["zoom_out", "pan_rl"],
    "reveal":  ["zoom_in"],
    "sad":     ["zoom_out"],
}

# ---------------------------------------------------------------------------
# CHANGE 3: light per-mood color tone (saturation, contrast, brightness).
# This is ONE extra ffmpeg filter tacked onto the existing filter chain --
# no extra render pass, so it costs almost nothing in render time.
# AFFECTS: the overall color "feel" of a scene's clip.
# ---------------------------------------------------------------------------
MOOD_TONE = {
    "default": (1.0, 1.0, 0.0),
    "calm":    (0.95, 1.0, 0.02),
    "happy":   (1.18, 1.05, 0.03),
    "tense":   (0.85, 1.15, -0.02),
    "reveal":  (1.1, 1.1, 0.02),
    "sad":     (0.65, 0.95, -0.03),
}

IMG_EXT = ("png", "jpg", "jpeg", "webp")
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/system/fonts/Roboto-Bold.ttf",
]


def log(msg):
    print(f"[studio] {msg}", flush=True)


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"Command failed: {' '.join(map(str, cmd[:4]))} ...")
    return r.stdout


def media_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def get_font(size):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ----------------------------------------------------------------- images
# (unchanged from your original -- no edits in this section)
def placeholder_image(text, idx, w, h, out):
    rnd = random.Random(idx * 7 + 3)
    c1 = tuple(rnd.randint(150, 235) for _ in range(3))
    c2 = tuple(rnd.randint(150, 235) for _ in range(3))
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        px.line([(0, y), (w, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    font = get_font(max(24, h // 22))
    words, lines, line = text.split(), [], ""
    for wd in words:
        test = (line + " " + wd).strip()
        if px.textlength(test, font=font) > w * 0.8:
            lines.append(line)
            line = wd
        else:
            line = test
    lines.append(line)
    y = h // 2 - len(lines) * font.size // 2
    for ln in lines:
        px.text(((w - px.textlength(ln, font=font)) / 2, y), ln, font=font, fill=(40, 40, 60))
        y += int(font.size * 1.3)
    img.save(out, quality=92)


def fetch_ai_image(prompt, seed, w, h, out):
    """Free image generation via Pollinations (no key needed). Returns True on success."""
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width={w}&height={h}&nologo=true&seed={seed}")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "story-studio/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            Path(out).write_bytes(data)
            Image.open(out).verify()
            return True
        except Exception as e:  # noqa: BLE001
            log(f"  image attempt {attempt + 1} failed: {e}")
            time.sleep(4 * (attempt + 1))
    return False


def get_image(story, scene, idx, story_dir, work, w, h):
    candidates = []
    if scene.get("file"):
        candidates.append(Path(story_dir) / scene["file"])
    candidates += [Path(story_dir) / f"scene{idx + 1}.{e}" for e in IMG_EXT]
    for c in candidates:
        if c.exists():
            log(f"  scene {idx + 1}: using your image {c.name}")
            return str(c)
    out = str(Path(work) / f"img_{idx}.jpg")
    if story.get("ai_images", True) and scene.get("image"):
        prompt = ", ".join(p for p in [story.get("style"), story.get("character"), scene["image"]] if p)
        log(f"  scene {idx + 1}: generating AI image")
        if fetch_ai_image(prompt, story.get("seed", 1), w, h, out):
            time.sleep(3)
            return out
    log(f"  scene {idx + 1}: using placeholder image")
    placeholder_image(scene.get("image") or scene["text"], idx, w, h, out)
    return out


# ------------------------------------------------------------------ voice
# ---------------------------------------------------------------------------
# CHANGE 4: make_voice now streams from edge-tts and captures its
# WordBoundary events -- the engine already knows exactly when each word
# starts and how long it lasts. Previously that data was thrown away and
# captions were just a rough character-count guess. Free, no extra tool.
# RETURNS: (audio_path, word_list) where word_list is
#          [(word_text, start_seconds, duration_seconds), ...] relative to
#          the START of this scene's own audio. word_list is [] only if
#          the voice failed and we fell back to silent audio -- in that
#          case captions fall back to the old estimate method automatically.
# AFFECTS: caption accuracy (this is what makes word-by-word highlighting
# possible).
# ---------------------------------------------------------------------------
async def _edge_tts_stream(text, voice, rate, out):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    words = []
    with open(out, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset/duration arrive in 100-nanosecond ticks; /10_000_000 -> seconds
                start = chunk["offset"] / 10_000_000
                dur = chunk["duration"] / 10_000_000
                words.append((chunk["text"], start, dur))
    return words


def make_voice(text, story, idx, work):
    mp3 = str(Path(work) / f"voice_{idx}.mp3")
    try:
        words = asyncio.run(_edge_tts_stream(text, story.get("voice", "en-US-AnaNeural"),
                                              story.get("rate", "+0%"), mp3))
        if Path(mp3).exists() and Path(mp3).stat().st_size > 1000:
            return mp3, words
        raise RuntimeError("empty audio")
    except Exception as e:  # noqa: BLE001
        log(f"  voice failed ({e}); using silent audio so the video still renders")
        dur = max(3.0, len(text.split()) / 2.4)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-t", f"{dur:.2f}", "-q:a", "5", mp3])
        return mp3, []   # no word timing -> build_ass() falls back automatically


# --------------------------------------------------------------- captions
def ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def split_phrases(text, max_words=5):
    # unchanged -- this is now ONLY the fallback path, used when a scene's
    # voice failed and we have no real word timing to work with.
    parts = re.split(r"(?<=[,.!?;:])\s+", text.strip())
    out = []
    for p in parts:
        words = p.split()
        for i in range(0, len(words), max_words):
            out.append(" ".join(words[i:i + max_words]))
    return [p for p in out if p]


# ---------------------------------------------------------------------------
# CHANGE 5 (new function): groups real word timings into small on-screen
# caption chunks (4 words at a time) and builds one ASS "karaoke" line per
# chunk using \kf tags -- libass (ffmpeg's subtitle renderer) animates the
# highlight sweeping across the words exactly as they're spoken. This is
# the "TikTok/CapCut style" caption look.
# AFFECTS: caption visuals -- replaces the old estimate-based phrase pop
# with frame-accurate word-by-word highlighting.
# ---------------------------------------------------------------------------
def build_karaoke_lines(words, scene_start, max_words=4):
    lines = []
    for i in range(0, len(words), max_words):
        chunk = words[i:i + max_words]
        t0 = scene_start + LEAD + chunk[0][1]
        t_end = scene_start + LEAD + chunk[-1][1] + chunk[-1][2]
        parts = []
        for j, (w, start, dur) in enumerate(chunk):
            # fold any tiny gap before this word into its own \kf duration
            # so the highlight sweep doesn't visibly stall between words
            gap = 0.0 if j == 0 else max(0.0, start - (chunk[j - 1][1] + chunk[j - 1][2]))
            cs = int(round((dur + gap) * 100))  # \kf wants centiseconds
            parts.append(r"{\kf" + str(max(1, cs)) + "}" + w + " ")
        lines.append((t0, t_end, "".join(parts).strip()))
    return lines


def build_ass(title, scene_infos, w, h, path):
    size = int(h * 0.058)
    # CHANGE 6: PrimaryColour = the "already highlighted / spoken" color,
    # SecondaryColour = the "not yet spoken" color. \kf sweeps text from
    # Secondary to Primary as each word plays -- this is what makes the
    # active word pop while the rest stays dim. AFFECTS: caption colors.
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,{size},&H0000E8FF,&H00FFFFFF,&H00301830,&H80000000,1,0,0,0,100,100,0,0,1,{max(3, size // 12)},1,2,{int(w * 0.06)},{int(w * 0.06)},{int(h * 0.07)},1
Style: Title,DejaVu Sans,{int(size * 1.5)},&H00FFFFFF,&H000000FF,&H00301830,&H80000000,1,0,0,0,100,100,0,0,1,{max(4, size // 9)},2,8,{int(w * 0.06)},{int(w * 0.06)},{int(h * 0.08)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        lines.append(f"Dialogue: 1,{ass_time(0.4)},{ass_time(3.2)},Title,,0,0,0,,"
                     r"{\fad(400,400)}" + title.replace("\n", " "))
    for info in scene_infos:
        if info["words"]:
            # CHANGE 7: word-accurate karaoke path (new default)
            for t0, t1, text in build_karaoke_lines(info["words"], info["start"]):
                lines.append(f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},Cap,,0,0,0,,"
                             r"{\fad(80,80)}" + text)
        else:
            # old estimate-based fallback -- only runs if voice generation
            # failed for this scene and we're on silent placeholder audio
            phrases = split_phrases(info["text"])
            total_chars = sum(len(p) for p in phrases) or 1
            t = info["start"] + LEAD
            for p in phrases:
                dur = info["speech"] * len(p) / total_chars
                lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + dur)},Cap,,0,0,0,,"
                             r"{\fad(120,120)\fscx88\fscy88\t(0,180,\fscx100\fscy100)}" + p)
                t += dur
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------- video bits
# ---------------------------------------------------------------------------
# CHANGE 8: the 4 motion formulas are now named and stored in a dict
# instead of an if/elif chain keyed by idx % 4. Same math as before --
# just reorganized so pick_motion() can select by name/mood.
# ---------------------------------------------------------------------------
MOTION_PARAMS = {
    "zoom_in":  lambda n: (f"1+0.14*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "pan_lr":   lambda n: ("1.14", f"(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"),
    "zoom_out": lambda n: (f"1.16-0.14*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "pan_rl":   lambda n: ("1.14", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"),
}


def pick_motion(scene, idx, seed):
    # CHANGE 9: explicit per-scene "motion" in the JSON always wins. Else
    # pick from the mood's pool (seeded on the story's own "seed" value so
    # re-rendering gives the SAME result every time -- consistent output).
    # "default" mood with no override reproduces the OLD idx % 4 cycling
    # exactly -- old story JSONs are unaffected.
    if scene.get("motion") in MOTION_PARAMS:
        return scene["motion"]
    mood = scene.get("mood", "default")
    pool = MOTION_POOLS.get(mood, MOTION_POOLS["default"])
    if mood == "default":
        return pool[idx % len(pool)]
    return random.Random(seed * 1000 + idx).choice(pool)


def pick_transition(scene, idx, seed):
    # CHANGE 10: same pattern as pick_motion(), but for the transition
    # INTO this scene (scene's own "mood" controls the transition leading
    # into it). "default" mood reproduces the OLD cycling exactly.
    if scene.get("transition") in ALL_TRANSITIONS:
        return scene["transition"]
    mood = scene.get("mood", "default")
    pool = TRANSITION_POOLS.get(mood, TRANSITION_POOLS["default"])
    if mood == "default":
        return pool[(idx - 1) % len(pool)]
    return random.Random(seed * 2000 + idx).choice(pool)


def make_scene_clip(img, voice, idx, dur, w, h, work, motion, mood):
    n = int(dur * FPS)
    z, x, y = MOTION_PARAMS[motion](n)
    sat, con, bri = MOOD_TONE.get(mood, MOOD_TONE["default"])
    out = str(Path(work) / f"clip_{idx}.mp4")
    # CHANGE 11: added eq=saturation:contrast:brightness at the end of the
    # video filter chain -- the mood color tone. One extra lightweight
    # filter, same single ffmpeg pass as before (no added render time).
    vf = (f"[0:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
          f"crop={w * 2}:{h * 2},setsar=1,"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={FPS},"
          f"eq=saturation={sat}:contrast={con}:brightness={bri},format=yuv420p[v]")
    af = (f"[1:a]aresample=44100,aformat=channel_layouts=stereo,"
          f"adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},apad[a]")
    run(["ffmpeg", "-y", "-i", img, "-i", voice, "-filter_complex", f"{vf};{af}",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", out])
    return out


def assemble(clips, durs, transitions, ass_name, music, total, work, out_path):
    n = len(clips)
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", Path(c).name]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
    f = []
    if n == 1:
        vlast, alast = "[0:v]", "[0:a]"
    else:
        offset = 0.0
        vprev, aprev = "[0:v]", "[0:a]"
        for i in range(1, n):
            offset += durs[i - 1] - XF
            tr = transitions[i - 1]  # CHANGE 12: now comes from pick_transition(), not a flat cycle
            f.append(f"{vprev}[{i}:v]xfade=transition={tr}:duration={XF}:offset={offset:.3f}[v{i}]")
            f.append(f"{aprev}[{i}:a]acrossfade=d={XF}[a{i}]")
            vprev, aprev = f"[v{i}]", f"[a{i}]"
        vlast, alast = vprev, aprev
    f.append(f"{vlast}subtitles={ass_name},fade=t=in:st=0:d=0.6,"
             f"fade=t=out:st={total - 1.0:.3f}:d=1.0[vout]")
    if music:
        f.append(f"[{n}:a]volume=0.10[bgm]")
        f.append(f"{alast}[bgm]amix=inputs=2:duration=first:dropout_transition=0,volume=2,"
                 f"afade=t=out:st={total - 1.2:.3f}:d=1.2[aout]")
    else:
        f.append(f"{alast}afade=t=out:st={total - 1.2:.3f}:d=1.2[aout]")
    cmd += ["-filter_complex", ";".join(f), "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(Path(out_path).resolve())]
    run(cmd, cwd=work)


# ------------------------------------------------------------------- main
def main():
    if len(sys.argv) < 2:
        print("Usage: python render.py stories/<n>.json")
        sys.exit(1)
    story_path = Path(sys.argv[1])
    story = json.loads(story_path.read_text(encoding="utf-8"))
    name = story_path.stem
    story_dir = story_path.parent / name
    # CHANGE 13: optional explicit "width"/"height" in the JSON now
    # overrides the 720p/1080p + landscape/vertical presets, for a custom
    # aspect ratio (1:1, 4:5, anything) without touching this file. If not
    # given, falls back to the exact old behaviour -- landscape/vertical
    # both still work exactly as before.
    if story.get("width") and story.get("height"):
        w, h = int(story["width"]) // 2 * 2, int(story["height"]) // 2 * 2
    else:
        base = {"720p": 720, "1080p": 1080}.get(story.get("resolution", "720p"), 720)
        long_side = int(base * 16 / 9) // 2 * 2
        w, h = (base, long_side) if story.get("format") == "vertical" else (long_side, base)
    work = Path("work") / name
    work.mkdir(parents=True, exist_ok=True)
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{name}.mp4"

    music = None
    for ext in ("mp3", "m4a", "wav"):
        found = sorted(Path("music").glob(f"*.{ext}"))
        if found:
            music = found[0]
            break
    log(f"{story.get('title', name)} | {w}x{h} | music: {music.name if music else 'none'}")

    seed = story.get("seed", 1)
    clips, durs, infos, transitions, t_cursor = [], [], [], [], 0.0
    for i, scene in enumerate(story["scenes"]):
        log(f"scene {i + 1}/{len(story['scenes'])}")
        img = get_image(story, scene, i, story_dir, work, w, h)
        voice, words = make_voice(scene["text"], story, i, work)
        speech = media_duration(voice)
        dur = speech + LEAD + TAIL
        mood = scene.get("mood", "default")
        motion = pick_motion(scene, i, seed)
        clips.append(make_scene_clip(img, voice, i, dur, w, h, work, motion, mood))
        durs.append(dur)
        infos.append({"text": scene["text"], "start": t_cursor, "speech": speech, "words": words})
        if i > 0:
            transitions.append(pick_transition(scene, i, seed))
        t_cursor += dur - XF

    total = sum(durs) - XF * (len(durs) - 1)
    build_ass(story.get("title"), infos, w, h, work / "captions.ass")
    log("assembling final video...")
    assemble(clips, durs, transitions, "captions.ass", music, total, work, out_path)
    log(f"DONE -> {out_path} ({total:.1f}s)")


if __name__ == "__main__":
    main()
