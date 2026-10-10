# Story Studio

Turn a simple JSON story into a finished kids video — complete with voice narration, mood-aware camera motion, smooth transitions, **word-by-word karaoke captions**, and optional music. Everything renders for free on GitHub Actions.

## Quick Start (from your phone)

1. Open the repo → **Actions** tab → **Render story video** → **Run workflow**
2. Type the story name (e.g. `sample` or `milo`) → Run
3. Wait 5–15 minutes → go to **Releases** → download the latest `.mp4`

Pushing a new or updated `stories/*.json` also auto-triggers a render.

## Create a New Story

1. Copy `stories/sample.json` and rename it (e.g. `fox.json`)
2. Edit the title, scenes, voice, etc.
3. (Optional) Add your own images: put `scene1.jpg`, `scene2.jpg`… inside `stories/<name>/`
4. Push — the video renders automatically

### Story JSON options

| Field | Description | Example |
|-------|-------------|---------|
| `format` | `landscape` or `vertical` (Shorts) | `"vertical"` |
| `resolution` | `720p` or `1080p` | `"720p"` |
| `width` / `height` | Custom size (overrides format/resolution) | `1080`, `1920` |
| `voice` | Any edge-tts voice | `"en-US-AnaNeural"` |
| `rate` | Speech speed | `"-5%"` |
| `seed` | Keeps look consistent across re-renders | `42` |
| `ai_images` | Auto-generate missing images (default `true`) | `false` |
| `style` / `character` | Shared prompt pieces for AI images | — |

### Per-scene options (new)

| Field | Description |
|-------|-------------|
| `mood` | `happy` · `calm` · `tense` · `reveal` · `sad` · `default` — controls transition style, camera motion, and color tone |
| `transition` | Force a specific transition (e.g. `"circleopen"`) |
| `motion` | Force a specific camera move: `zoom_in`, `pan_lr`, `zoom_out`, `pan_rl` |
| `file` | Use a custom image filename instead of `sceneN.jpg` |

When `mood` is omitted the old deterministic cycling is used, so existing stories look identical.

## Features

- **Word-accurate karaoke captions** — highlights the exact word being spoken (powered by edge-tts WordBoundary timing)
- **Mood-aware effects** — transitions, Ken-Burns motion, and subtle color grading all respond to the scene’s `mood`
- **Your own images or free AI** — drop images into the story folder or let the free Pollinations fallback handle it
- **Optional background music** — just drop one `.mp3` into the `music/` folder
- **Fully free** — runs on GitHub Actions with no API keys required for basic use

## Music

Upload one audio file (`.mp3`, `.m4a` or `.wav`) into the `music/` folder. It plays quietly under the narration.

## License

Free to use and modify.
