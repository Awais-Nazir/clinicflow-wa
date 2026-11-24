"""Bounded WhatsApp voice transcription with a swappable implementation."""

from io import BytesIO
from typing import Protocol

MAX_VOICE_BYTES = 2 * 1024 * 1024
MAX_VOICE_SECONDS = 60
SAMPLE_RATE = 16000


class VoiceError(RuntimeError):
    """The voice message cannot be safely transcribed."""


class VoiceMedia(Protocol):
    def download_audio(self, *, phone_number_id: str, media_id: str, max_bytes: int) -> bytes: ...


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, *, max_seconds: int) -> str: ...


class VoiceProcessor:
    def __init__(self, media: VoiceMedia, transcriber: Transcriber):
        self.media = media
        self.transcriber = transcriber

    def process(self, phone_number_id: str, media_id: str) -> str:
        audio = self.media.download_audio(
            phone_number_id=phone_number_id, media_id=media_id, max_bytes=MAX_VOICE_BYTES,
        )
        if not audio or len(audio) > MAX_VOICE_BYTES:
            raise VoiceError("audio exceeds the size limit")
        transcript = " ".join(self.transcriber.transcribe(audio, max_seconds=MAX_VOICE_SECONDS).split())
        if not transcript or len(transcript) > 1200:
            raise VoiceError("no usable speech")
        return transcript


class WhisperTranscriber:
    """Run multilingual Whisper on the local CPU."""

    def __init__(self, model: str, *, allow_download: bool = False):
        self._model_name = model
        self._allow_download = allow_download
        self._model = None

    def transcribe(self, audio: bytes, *, max_seconds: int) -> str:
        try:
            import av
            import numpy as np
            from faster_whisper import WhisperModel

            frames = []
            count = 0
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            with av.open(BytesIO(audio)) as media:
                if not media.streams.audio:
                    raise VoiceError("missing audio stream")
                for frame in media.decode(audio=0):
                    for output in resampler.resample(frame):
                        count += output.samples
                        if count > max_seconds * SAMPLE_RATE:
                            raise VoiceError("voice message exceeds duration limit")
                        frames.append(output.to_ndarray().reshape(-1))
            for output in resampler.resample(None):
                count += output.samples
                if count > max_seconds * SAMPLE_RATE:
                    raise VoiceError("voice message exceeds duration limit")
                frames.append(output.to_ndarray().reshape(-1))
            if not frames:
                raise VoiceError("missing audio frames")
            samples = np.concatenate(frames).astype(np.float32) / 32768.0
            if self._model is None:
                self._model = WhisperModel(
                    self._model_name, device="cpu", compute_type="int8",
                    local_files_only=not self._allow_download,
                )
            segments, _ = self._model.transcribe(samples, beam_size=3, vad_filter=True)
            return " ".join(segment.text.strip() for segment in segments)
        except VoiceError:
            raise
        except Exception as error:
            raise VoiceError("local transcription failed") from error
