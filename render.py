#!/usr/bin/env python3
"""
Story Studio - story JSON in, finished MP4 out.

Per scene: image (your own file, or free AI image, or placeholder) + free TTS voice
Effects: zoom/pan motion, transitions between scenes, animated captions, optional music.
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
TRANSITIONS = ["fade", "slideleft", "circleopen", "wipeleft", "dissolve",
               "slideup", "radial", "smoothleft", "circleclose", "wiperight"]
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
    # 1) your own file: stories/<name>/scene1.png (1-based) or a path in scene["file"]
    candidates = []
    if scene.get("file"):
        candidates.append(Path(story_dir) / scene["file"])
    candidates += [Path(story_dir) / f"scene{idx + 1}.{e}" for e in IMG_EXT]
    for c in candidates:
        if c.exists():
            log(f"  scene {idx + 1}: using your image {c.name}")
            return str(c)
    out = str(Path(work) / f"img_{idx}.jpg")
    # 2) free AI image
    if story.get("ai_images", True) and scene.get("image"):
        prompt = ", ".join(p for p in [story.get("style"), story.get("character"), scene["image"]] if p)
        log(f"  scene {idx + 1}: generating AI image")
        if fetch_ai_image(prompt, story.get("seed", 1), w, h, out):
            time.sleep(3)
            return out
    # 3) placeholder so the video always completes
    log(f"  scene {idx + 1}: using placeholder image")
    placeholder_image(scene.get("image") or scene["text"], idx, w, h, out)
    return out


# ------------------------------------------------------------------ voice
async def _edge_tts(text, voice, rate, out):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(out)


def make_voice(text, story, idx, work):
    mp3 = str(Path(work) / f"voice_{idx}.mp3")
    try:
        asyncio.run(_edge_tts(text, story.get("voice", "en-US-AnaNeural"), story.get("rate", "+0%"), mp3))
        if Path(mp3).exists() and Path(mp3).stat().st_size > 1000:
            return mp3
        raise RuntimeError("empty audio")
    except Exception as e:  # noqa: BLE001
        log(f"  voice failed ({e}); using silent audio so the video still renders")
        dur = max(3.0, len(text.split()) / 2.4)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-t", f"{dur:.2f}", "-q:a", "5", mp3])
        return mp3


# --------------------------------------------------------------- captions
def ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def split_phrases(text, max_words=5):
    parts = re.split(r"(?<=[,.!?;:])\s+", text.strip())
    out = []
    for p in parts:
        words = p.split()
        for i in range(0, len(words), max_words):
            out.append(" ".join(words[i:i + max_words]))
    return [p for p in out if p]


def build_ass(title, scene_infos, w, h, path):
    size = int(h * 0.058)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,{size},&H0000E8FF,&H000000FF,&H00301830,&H80000000,1,0,0,0,100,100,0,0,1,{max(3, size // 12)},1,2,{int(w * 0.06)},{int(w * 0.06)},{int(h * 0.07)},1
Style: Title,DejaVu Sans,{int(size * 1.5)},&H00FFFFFF,&H000000FF,&H00301830,&H80000000,1,0,0,0,100,100,0,0,1,{max(4, size // 9)},2,8,{int(w * 0.06)},{int(w * 0.06)},{int(h * 0.08)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        lines.append(f"Dialogue: 1,{ass_time(0.4)},{ass_time(3.2)},Title,,0,0,0,,"
                     r"{\fad(400,400)}" + title.replace("\n", " "))
    for info in scene_infos:
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
def make_scene_clip(img, voice, idx, dur, w, h, work):
    n = int(dur * FPS)
    mode = idx % 4
    if mode == 0:     # slow zoom in
        z, x, y = f"1+0.14*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mode == 1:   # pan left to right
        z, x, y = "1.14", f"(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"
    elif mode == 2:   # zoom out
        z, x, y = f"1.16-0.14*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    else:             # pan right to left
        z, x, y = "1.14", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"
    out = str(Path(work) / f"clip_{idx}.mp4")
    vf = (f"[0:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
          f"crop={w * 2}:{h * 2},setsar=1,"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={FPS},format=yuv420p[v]")
    af = (f"[1:a]aresample=44100,aformat=channel_layouts=stereo,"
          f"adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},apad[a]")
    run(["ffmpeg", "-y", "-i", img, "-i", voice, "-filter_complex", f"{vf};{af}",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", out])
    return out


def assemble(clips, durs, ass_name, music, total, work, out_path):
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
            tr = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
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
        print("Usage: python render.py stories/<name>.json")
        sys.exit(1)
    story_path = Path(sys.argv[1])
    story = json.loads(story_path.read_text(encoding="utf-8"))
    name = story_path.stem
    story_dir = story_path.parent / name
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

    clips, durs, infos, t_cursor = [], [], [], 0.0
    for i, scene in enumerate(story["scenes"]):
        log(f"scene {i + 1}/{len(story['scenes'])}")
        img = get_image(story, scene, i, story_dir, work, w, h)
        voice = make_voice(scene["text"], story, i, work)
        speech = media_duration(voice)
        dur = speech + LEAD + TAIL
        clips.append(make_scene_clip(img, voice, i, dur, w, h, work))
        durs.append(dur)
        infos.append({"text": scene["text"], "start": t_cursor, "speech": speech})
        t_cursor += dur - XF

    total = sum(durs) - XF * (len(durs) - 1)
    build_ass(story.get("title"), infos, w, h, work / "captions.ass")
    log("assembling final video...")
    assemble(clips, durs, "captions.ass", music, total, work, out_path)
    log(f"DONE -> {out_path} ({total:.1f}s)")


if __name__ == "__main__":
    main()
