#!/usr/bin/env python3
"""Generate the demo voiceover from narration.md via ElevenLabs.

The beats live in narration.md as blockquotes, so the script people read and
the script the API reads are the same text. Editing narration and forgetting to
regenerate is the failure this avoids.

Settings, all resolving environment first then .env then a default:

    ELEVENLABS_API_KEY     required
    ELEVENLABS_VOICE_ID    which voice
    ELEVENLABS_MODEL_ID    default eleven_multilingual_v2
    ELEVENLABS_STABILITY   default 0.55

    python demo/voiceover.py --voices        # list voices on the account
    python demo/voiceover.py --dry-run       # print what it would send
    python demo/voiceover.py                 # writes demo/vo/beat-N.mp3
    python demo/voiceover.py --beats 4 5     # regenerate only some

Each beat is one MP3. capture.py reads each file's real duration and holds that
beat on screen for exactly that long, so nothing needs manual syncing.

Costs credits on every run, so it skips beats whose text has not changed since
the last generation — tracked in vo/.hashes.json. --force overrides.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
NARRATION = HERE / "narration.md"
OUT_DIR = HERE / "vo"
HASHES = OUT_DIR / ".hashes.json"

API_ROOT = "https://api.elevenlabs.io/v1"
ENV_FILE = HERE.parent / ".env"


def _dotenv() -> dict[str, str]:
    """Parse .env once. Gitignored, so it is a reasonable place for these."""
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip("\"'")
    return values


DOTENV = _dotenv()


def setting(name: str, default: str = "") -> tuple[str, str]:
    """Return (value, where it came from).

    Every ELEVENLABS_* setting resolves the same way — real environment first,
    then .env, then the default. Reporting the origin matters: having a key in
    the shell and a different voice id in .env is exactly the situation where a
    silently ignored value wastes a run's worth of credits.
    """
    if os.getenv(name, "").strip():
        return os.environ[name].strip(), "environment"
    if DOTENV.get(name):
        return DOTENV[name], ".env"
    return default, "default"


# Measured and neutral, not a product-launch read — credibility is the whole
# pitch. Every one of these is overridable without editing this file.
VOICE_ID, VOICE_ID_FROM = setting("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")
MODEL_ID, MODEL_ID_FROM = setting("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
VOICE_SETTINGS = {
    "stability": float(setting("ELEVENLABS_STABILITY", "0.55")[0]),
    "similarity_boost": float(setting("ELEVENLABS_SIMILARITY", "0.75")[0]),
    "style": float(setting("ELEVENLABS_STYLE", "0.0")[0]),
    "use_speaker_boost": True,
}

BEAT_HEADING = re.compile(r"^##\s+Beat\s+(\d+)\s*[—-]\s*(.+?)\s*$", re.MULTILINE)


def load_key() -> str:
    key, _ = setting("ELEVENLABS_API_KEY")
    if key:
        return key
    sys.exit(
        "No ELEVENLABS_API_KEY.\n\n"
        "Add both settings to .env in the repo root (gitignored):\n"
        "  ELEVENLABS_API_KEY=...\n"
        "  ELEVENLABS_VOICE_ID=...\n"
        "or export them in your shell.\n"
    )


def list_voices(key: str) -> None:
    """Show the voices on this account, so a voice id can be checked rather
    than pasted hopefully."""
    req = urllib.request.Request(
        f"{API_ROOT}/voices", headers={"xi-api-key": key}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            voices = json.load(resp).get("voices", [])
    except urllib.error.HTTPError as exc:
        sys.exit(f"Could not list voices ({exc.code}) — check ELEVENLABS_API_KEY")

    print(f"{len(voices)} voices on this account:\n")
    for v in voices:
        labels = v.get("labels") or {}
        descr = ", ".join(f"{k}: {val}" for k, val in labels.items() if val)
        marker = "  ← currently selected" if v.get("voice_id") == VOICE_ID else ""
        print(f"  {v.get('voice_id')}  {v.get('name', '?'):<22} {descr}{marker}")
    print("\nSet one with ELEVENLABS_VOICE_ID, in .env or the environment.")


def parse_beats() -> list[dict]:
    """Pull each beat's spoken text out of narration.md.

    The spoken words are the blockquote lines under each `## Beat N` heading.
    Stage directions sit outside the quote in italics and are skipped, which is
    what keeps one file readable by both a person and this script.
    """
    text = NARRATION.read_text()
    matches = list(BEAT_HEADING.finditer(text))
    if not matches:
        sys.exit(f"No '## Beat N — ...' headings found in {NARRATION}")

    beats = []
    for i, m in enumerate(matches):
        body = text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]

        spoken = []
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith(">"):
                spoken.append(stripped.lstrip("> ").strip())
            elif not stripped and spoken and spoken[-1]:
                spoken.append("")  # paragraph break inside the quote

        joined = " ".join(w for w in " ".join(spoken).split() if w)
        if not joined:
            continue

        beats.append({
            "number": int(m.group(1)),
            "title": m.group(2),
            "text": joined,
        })
    return beats


def synthesise(key: str, text: str, dest: Path) -> None:
    payload = json.dumps({
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": VOICE_SETTINGS,
    }).encode()

    req = urllib.request.Request(
        f"{API_ROOT}/text-to-speech/{VOICE_ID}",
        data=payload,
        headers={
            "xi-api-key": key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        # 401 is the key, 422 is usually the voice id — worth separating,
        # because the messages look alike at a glance.
        hint = {
            401: "check ELEVENLABS_API_KEY",
            422: f"check ELEVENLABS_VOICE_ID (currently {VOICE_ID})",
            429: "rate limited or out of credits",
        }.get(exc.code, "")
        sys.exit(f"ElevenLabs returned {exc.code} — {hint}\n{detail}")

    dest.write_bytes(audio)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the text, call nothing")
    ap.add_argument("--force", action="store_true", help="regenerate even if unchanged")
    ap.add_argument("--beats", nargs="*", type=int, help="only these beat numbers")
    ap.add_argument("--voices", action="store_true", help="list the voices on this account")
    args = ap.parse_args()

    if args.voices:
        list_voices(load_key())
        return

    # Printed every run. The setting most likely to be wrong is the voice, and
    # the most confusing way for it to be wrong is silently.
    print(f"voice {VOICE_ID} (from {VOICE_ID_FROM})  ·  model {MODEL_ID} (from {MODEL_ID_FROM})")
    if VOICE_ID_FROM == "default":
        print("  no ELEVENLABS_VOICE_ID set — using the built-in default")

    beats = parse_beats()
    if args.beats:
        beats = [b for b in beats if b["number"] in args.beats]
        if not beats:
            sys.exit(f"No beats matching {args.beats}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hashes = json.loads(HASHES.read_text()) if HASHES.exists() else {}

    if args.dry_run:
        total = 0
        for b in beats:
            words = len(b["text"].split())
            total += words
            print(f"\n── Beat {b['number']} — {b['title']}")
            print(f"   {words} words, ~{words / 2.6:.0f}s at a measured pace, "
                  f"{len(b['text'])} characters")
            print(f"   {b['text']}")
        print(f"\n{len(beats)} beats, {total} words, "
              f"~{total / 2.6 / 60:.1f} minutes, "
              f"{sum(len(b['text']) for b in beats)} characters of credit")
        return

    key = load_key()
    generated = skipped = 0

    for b in beats:
        dest = OUT_DIR / f"beat-{b['number']}.mp3"
        digest = hashlib.sha256(
            f"{b['text']}|{VOICE_ID}|{MODEL_ID}|{json.dumps(VOICE_SETTINGS, sort_keys=True)}".encode()
        ).hexdigest()

        if not args.force and dest.exists() and hashes.get(dest.name) == digest:
            print(f"  beat {b['number']}  unchanged, skipped")
            skipped += 1
            continue

        print(f"  beat {b['number']}  {len(b['text'])} chars → {dest.name} ...", end="", flush=True)
        synthesise(key, b["text"], dest)
        hashes[dest.name] = digest
        print(f" {dest.stat().st_size // 1024} KB")
        generated += 1

    HASHES.write_text(json.dumps(hashes, indent=2, sort_keys=True))
    print(f"\n{generated} generated, {skipped} unchanged → {OUT_DIR}")
    if generated:
        print("Listen before recording. Then: ./demo/render.sh")


if __name__ == "__main__":
    main()
