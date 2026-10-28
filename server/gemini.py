"""Reads the five congregation prayer times from a photo of a timetable or from a spoken message (see aipool.py for how: Gemini
models in turn, limits respected, doubtful readings cross-checked). The file is handled in memory and never stored or logged;
the API keys are secrets and the feature is OFF while none is set. The caller shows the result and the user confirms before saving."""
import aipool

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
AUDIO_TYPES = {"audio/mp4", "audio/m4a", "audio/x-m4a", "audio/aac", "audio/ogg", "audio/opus", "audio/webm", "audio/wav", "audio/x-wav",
               "audio/mpeg", "audio/mp3"}
Unavailable = aipool.Unavailable
Unreadable = aipool.Unreadable


def available(cfg):
    return aipool.available(cfg)


def read_photo(cfg, data, mime):
    return aipool.read(cfg, "image", data, mime)


def read_voice(cfg, data, mime):
    return aipool.read(cfg, "audio", data, mime)
