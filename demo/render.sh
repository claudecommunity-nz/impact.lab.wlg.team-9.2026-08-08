#!/usr/bin/env bash
# Mux the screen capture with the ElevenLabs voiceover into the final mp4.
#
#   demo/.venv/bin/python demo/capture.py   # writes out/screen.webm + timing.json
#   demo/render.sh                          # writes out/walkthrough.mp4
#
# Each voiceover file is padded out to the exact budget its beat was shot
# against before the tracks are joined, so beat 5 lands on beat 5 rather than
# drifting a little further ahead with every cut.
set -euo pipefail

cd "$(dirname "$0")"
OUT=out
VO=vo
TIMING="$OUT/timing.json"
SCREEN="$OUT/screen.webm"
FINAL="$OUT/walkthrough.mp4"

[ -f "$SCREEN" ] || { echo "No $SCREEN — run capture.py first." >&2; exit 1; }
[ -f "$TIMING" ] || { echo "No $TIMING — run capture.py first." >&2; exit 1; }

LEAD=$(python3 -c "import json;print(json.load(open('$TIMING'))['lead'])")
COUNT=$(python3 -c "import json;print(len(json.load(open('$TIMING'))['beats']))")

# Every stage runs at this rate. loudnorm outputs 192kHz whatever you feed it,
# and the concat demuxer does not resample — it reinterprets whatever follows
# at the FIRST file's rate. A 192kHz beat concatenated after 48kHz lead-in
# silence therefore played at quarter speed, which sounds like the voice has
# been slowed down rather than like a sample-rate bug.
RATE=48000

missing=0
for i in $(seq 1 "$COUNT"); do
  [ -f "$VO/beat-$i.mp3" ] || { echo "missing $VO/beat-$i.mp3"; missing=1; }
done
if [ "$missing" = 1 ]; then
  echo
  echo "Generate the beats first:  python demo/voiceover.py"
  echo "Rendering a silent cut instead so you can still check the timing."
  ffmpeg -y -loglevel error -i "$SCREEN" \
    -vf "scale=1920:1080:flags=lanczos,format=yuv420p" \
    -c:v libx264 -preset slow -crf 20 -movflags +faststart -an \
    "$OUT/walkthrough-silent.mp4"
  echo "Wrote $OUT/walkthrough-silent.mp4"
  exit 0
fi

rm -rf "$OUT/audio"
mkdir -p "$OUT/audio"

# Lead-in silence, then each beat padded to its shot length.
ffmpeg -y -loglevel error -f lavfi -t "$LEAD" -i "anullsrc=r=$RATE:cl=stereo" \
  -ar "$RATE" -c:a pcm_s16le "$OUT/audio/000-lead.wav"

# Each beat plays at its own natural speed, starting the instant its beat does,
# with silence after it to fill the rest of the budget. Nothing is time-
# stretched: the budgets were derived from these files' real durations, so
# stretching could only ever fight the timing it was derived from.
#
# Order matters. Normalise first, on the speech alone — normalising after the
# padding lets the silence drag the gain around and the level pumps between
# beats. Then resample, because loudnorm has just forced 192kHz. Then pad.
for i in $(seq 1 "$COUNT"); do
  secs=$(python3 -c "import json;print(json.load(open('$TIMING'))['beats'][$i-1]['seconds'])")
  ffmpeg -y -loglevel error -i "$VO/beat-$i.mp3" \
    -af "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=$RATE,apad" \
    -t "$secs" -ac 2 -ar "$RATE" -c:a pcm_s16le "$OUT/audio/$(printf '%03d' "$i").wav"
done

: > "$OUT/audio/list.txt"
for f in "$OUT"/audio/*.wav; do
  echo "file '$(basename "$f")'" >> "$OUT/audio/list.txt"
done

ffmpeg -y -loglevel error -f concat -safe 0 -i "$OUT/audio/list.txt" \
  -ar "$RATE" -c:a pcm_s16le "$OUT/voiceover.wav"

# The concat above is the step that silently mis-speeds everything if a rate
# slips through, so check rather than trust. Expected length is the lead plus
# every beat budget.
want=$(python3 -c "import json;d=json.load(open('$TIMING'));print(d['lead']+sum(b['seconds'] for b in d['beats']))")
got=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/voiceover.wav")
python3 - "$want" "$got" <<'EOF'
import sys
want, got = float(sys.argv[1]), float(sys.argv[2])
if abs(got - want) > 1.0:
    sys.exit(
        f"voiceover.wav is {got:.1f}s but should be {want:.1f}s "
        f"({got / want:.2f}x). A sample-rate mismatch between the beat files "
        f"and the lead-in makes concat replay them at the wrong speed."
    )
print(f"  voiceover {got:.1f}s (expected {want:.1f}s)")
EOF

# 1600x900 up to 1080p. -shortest so a slightly long tail on either track does
# not leave the other hanging on a frozen frame.
ffmpeg -y -loglevel error -i "$SCREEN" -i "$OUT/voiceover.wav" \
  -vf "scale=1920:1080:flags=lanczos,format=yuv420p" \
  -c:v libx264 -preset slow -crf 20 \
  -c:a aac -b:a 192k -ar 48000 \
  -movflags +faststart -shortest "$FINAL"

dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$FINAL")
printf 'Wrote %s — %.1fs\n' "$FINAL" "$dur"
