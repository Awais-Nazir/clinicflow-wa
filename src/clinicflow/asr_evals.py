"""Measure word errors and latency against a local voice-note manifest."""

import argparse
import json
from pathlib import Path
import re
from time import perf_counter
import unicodedata

from clinicflow.voice import MAX_VOICE_BYTES, MAX_VOICE_SECONDS, VoiceError, WhisperTranscriber


def _words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.findall(r"[^\W_]+", normalized)


def word_errors(reference: str, transcript: str) -> tuple[int, int]:
    """Return edit distance and reference length for corpus-level WER."""

    expected, actual = _words(reference), _words(transcript)
    previous = list(range(len(actual) + 1))
    for row, word in enumerate(expected, start=1):
        current = [row]
        for column, heard in enumerate(actual, start=1):
            current.append(min(
                current[column - 1] + 1,
                previous[column] + 1,
                previous[column - 1] + (word != heard),
            ))
        previous = current
    return previous[-1], len(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--model", action="append", required=True, help="Repeat for each local model")
    parser.add_argument("--download", action="store_true", help="Allow model weights to be downloaded")
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        parser.error("manifest must contain at least one voice note")
    clips = []
    for case in cases:
        file = args.manifest.parent / case["audio"]
        if file.stat().st_size > MAX_VOICE_BYTES:
            parser.error(f"{file.name} exceeds the byte limit")
        if not _words(case["reference"]):
            parser.error(f"{file.name} needs a non-empty reference transcript")
        clips.append((file.read_bytes(), case["reference"]))

    failed = False
    for model in args.model:
        transcriber = WhisperTranscriber(model, allow_download=args.download)
        errors = expected_words = unavailable = 0
        elapsed = 0.0
        for audio, reference in clips:
            started = perf_counter()
            try:
                transcript = transcriber.transcribe(audio, max_seconds=MAX_VOICE_SECONDS)
            except VoiceError:
                unavailable += 1
                transcript = ""
            elapsed += perf_counter() - started
            edits, reference_words = word_errors(reference, transcript)
            errors += edits
            expected_words += reference_words
        print(
            f"{model} | {len(clips)} clips | WER {errors / expected_words:.1%} | "
            f"mean {elapsed / len(clips):.2f}s | failures {unavailable} | API cost $0"
        )
        failed = failed or unavailable > 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
