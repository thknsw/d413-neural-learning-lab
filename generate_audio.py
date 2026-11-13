#!/usr/bin/env python3
"""Generate D413 course MP3s from the existing lesson scripts with neural Edge voices.

Uses https://github.com/rany2/edge-tts, an unofficial online interface to
Microsoft Edge TTS (requires an internet connection; availability isn't guaranteed).

Examples:
  python scripts/generate_audio.py --dry-run
  python scripts/generate_audio.py --voice jenny
  python scripts/generate_audio.py --concurrency 2
"""
import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VOICES = {
    "jenny": "en-US-JennyNeural",     # US female
    "andrew": "en-US-AndrewNeural",   # US male
    "sonia": "en-GB-SoniaNeural",     # UK female
}


def read_lessons():
    data = json.loads((ROOT / "curriculum.json").read_text(encoding="utf-8"))
    lessons = []
    for s in data["sections"]:
        for lesson in s["lessons"]:
            text = lesson["script"].strip()
            if not text or not lesson["id"]:
                raise ValueError("Every lesson needs an ID and an audio script.")
            lessons.append((lesson["id"], text, lesson["title"]))
    if len(lessons) != 60 or len({id for id, _, _ in lessons}) != 60:
        raise ValueError("Expected exactly 60 unique D413 lessons.")
    return lessons


async def render_one(voice_key, voice_id, lesson_id, text, semaphore, overwrite):
    folder = ROOT / "audio" / voice_key
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{lesson_id}.mp3"
    if destination.exists() and destination.stat().st_size > 5000 and not overwrite:
        return str(destination.relative_to(ROOT)), "cached"
    async with semaphore:
        for attempt in range(1, 5):
            staging = destination.with_suffix(".mp3.partial")
            try:
                # Use one request per lesson; no device TTS is ever involved.
                communicate = edge_tts.Communicate(text, voice=voice_id, rate="+0%")
                await communicate.save(str(staging))
                if staging.stat().st_size < 5000:
                    raise IOError("Resulting audio was unexpectedly small")
                staging.replace(destination)
                print(f"READY  [{voice_key}] {lesson_id} ({destination.stat().st_size:,} bytes)", flush=True)
                return str(destination.relative_to(ROOT)), "generated"
            except Exception as exc:
                staging.unlink(missing_ok=True)
                if attempt == 4:
                    raise RuntimeError(f"Could not synthesize {lesson_id} with {voice_id}: {exc}") from exc
                delay = min(25, 2 ** attempt + random.random() * 2)
                print(f"Retry {attempt}/4 for {lesson_id} / {voice_key} in {delay:.1f}s: {exc}", flush=True)
                await asyncio.sleep(delay)


async def run(args):
    lessons = read_lessons()
    target_voices = {args.voice: VOICES[args.voice]} if args.voice else VOICES
    expected = len(lessons) * len(target_voices)
    print(f"{len(lessons)} lessons × {len(target_voices)} voices = {expected} MP3 files")
    if args.dry_run:
        for voice in target_voices:
            print(f"  {voice}: {lessons[0][0]}.mp3 … {lessons[-1][0]}.mp3")
        return
    try:
        import edge_tts
    except ImportError:
        sys.exit("Missing edge-tts. Install with: python -m pip install -r requirements.txt")
    globals()["edge_tts"] = edge_tts
    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [render_one(v, full_voice, id, script, semaphore, args.overwrite)
             for v, full_voice in target_voices.items() for id, script, _ in lessons]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [x for x in results if isinstance(x, BaseException)]
    for error in failures:
        print(f"ERROR: {error}", file=sys.stderr)
    if failures:
        sys.exit(f"Failed to generate {len(failures)} of {expected} audio files. Run the workflow again to retry missing files.")
    # All full-quality MP3s are present before manifest is written.
    manifest = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "engine": "edge-tts (Microsoft Edge online neural voices)",
        "voices": target_voices,
        "lessonCount": len(lessons),
        "audioFiles": [path for path, _ in results],
    }
    audio_root = ROOT / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    (audio_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"SUCCESS: verified all {expected} MP3s; wrote audio/manifest.json")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--voice", choices=VOICES.keys(), help="Generate one narrator instead of all three")
    p.add_argument("--concurrency", type=int, default=2, help="Maximum simultaneous synthesis requests (default 2)")
    p.add_argument("--overwrite", action="store_true", help="Regenerate existing MP3s")
    p.add_argument("--dry-run", action="store_true", help="Show the plan without contacting the voice service")
    args = p.parse_args()
    if not 1 <= args.concurrency <= 5:
        p.error("--concurrency must be between 1 and 5")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
