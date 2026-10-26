"""Speech-to-text for voice messages with faster-whisper: free, runs on our own server, nothing is sent to a third party and the
audio is not stored. Optional: without the package the bot simply asks for a photo or typed times."""
import os
import tempfile
import threading

MODEL = os.environ.get("WHISPER_MODEL", "base")            # tiny / base / small: bigger = more accurate, slower, more RAM
PROMPT = ("Bomdod 5:20, Peshin 12:30, Asr 4:00, Shom 5:35, Xufton 7:00. Бомдод, пешин, аср, шом, хуфтон. "
          "Фаджр, зухр, аср, магриб, иша. Fajr, Dhuhr, Asr, Maghrib, Isha.")      # primes the recogniser for prayer names and times
ALLOWED = {"uz", "ru", "en"}
_model = None
_lock = threading.Lock()                                   # one transcription at a time: bounds CPU and memory


def available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(MODEL, device="cpu", compute_type="int8", download_root=os.environ.get("WHISPER_CACHE") or None)
    return _model


def transcribe(data):
    """Audio bytes (Telegram voice = OGG/Opus; PyAV inside faster-whisper decodes it) -> text."""
    with _lock:
        model = _load()
        with tempfile.NamedTemporaryFile(suffix=".ogg") as f:
            f.write(data)
            f.flush()
            kw = dict(beam_size=5, initial_prompt=PROMPT, condition_on_previous_text=False)
            segments, info = model.transcribe(f.name, **kw)                 # language is detected automatically
            text = " ".join(s.text.strip() for s in segments).strip()
            if info.language not in ALLOWED:                                # e.g. Uzbek mistaken for Turkish/Kazakh: force Uzbek
                segments, _ = model.transcribe(f.name, language="uz", **kw)
                text = " ".join(s.text.strip() for s in segments).strip()
        return text
