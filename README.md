# Story Studio

Story JSON -> finished kids story video (voice, zoom/pan motion, transitions, animated captions, optional music), rendered free on GitHub Actions.

## Run (from phone)
1. Repo > **Actions** tab > **Render story video** > **Run workflow**
2. Type the story name (e.g. `sample`) > Run
3. After ~5-15 min: repo > **Releases** > open latest > download the `.mp4`

## New story
Copy `stories/sample.json`, rename it (e.g. `fox.json`), change title/scenes. Pushing a new JSON auto-renders it.

## Your own images (optional)
Upload to `stories/<name>/scene1.png`, `scene2.png` ... (1-based). Missing ones are AI-generated free; if that fails a placeholder is used.
Set `"ai_images": false` to disable AI images.

## Music (optional)
Upload one `.mp3` into `music/`. It plays quietly under the voice.

## Options in the JSON
format: landscape | vertical (Shorts) | resolution: 720p | 1080p | voice: any edge-tts voice (e.g. en-US-AnaNeural, en-US-JennyNeural) | rate: "-5%" | seed: keep same for consistent look
