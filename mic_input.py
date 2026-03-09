"""
Microphone speech-to-text: record from default mic and return transcript.
Uses sounddevice for recording and SpeechRecognition (Google or Sphinx) for STT.
On macOS: grant Terminal (or your IDE) mic access in System Settings > Privacy & Security > Microphone.
"""
import sys
import tempfile
import wave

try:
    import numpy as np
    import sounddevice as sd
    import speech_recognition as sr
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

# 16 kHz mono is standard for speech recognition
SAMPLE_RATE = 16000
CHANNELS = 1
MAX_DURATION_SEC = 6


def _get_input_device():
    """Return default input device index for sd.rec(..., device=)."""
    try:
        default = sd.default.device
        if isinstance(default, (list, tuple)) and len(default) >= 1:
            return int(default[0])
        return int(default) if default is not None else None
    except Exception:
        pass
    return None


def record_and_transcribe(duration_sec: float = 5.0) -> str:
    """
    Record from default microphone for duration_sec, then transcribe.
    Returns transcript string or empty string on failure.
    On macOS: if you get no speech, grant Microphone access to Terminal in
    System Settings > Privacy & Security > Microphone.
    """
    if not HAS_DEPS:
        return ""
    if duration_sec <= 0 or duration_sec > MAX_DURATION_SEC:
        duration_sec = min(5.0, MAX_DURATION_SEC)
    frames = int(SAMPLE_RATE * duration_sec)
    device = _get_input_device()
    try:
        if device is not None:
            recording = sd.rec(
                frames,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=np.int16,
                device=device,
            )
        else:
            recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=np.int16)
        sd.wait()
    except Exception as e:
        if "Permission" in str(e) or "access" in str(e).lower() or "denied" in str(e).lower():
            return ""
        raise

    recording = np.squeeze(recording)
    if recording.size == 0:
        return ""
    if int(np.max(np.abs(recording))) == 0:
        return ""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        path = f.name
        with wave.open(path, "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(recording.tobytes())
        try:
            r = sr.Recognizer()
            with sr.AudioFile(path) as source:
                audio = r.record(source)
            try:
                return (r.recognize_google(audio) or "").strip()
            except sr.UnknownValueError:
                return ""
            except sr.RequestError:
                try:
                    return (r.recognize_sphinx(audio) or "").strip()
                except Exception:
                    return ""
        except Exception:
            return ""


def check_mic_and_print_tips():
    """Print helpful tips if mic might not be working (call before first use)."""
    if not HAS_DEPS:
        print("Install: pip install sounddevice SpeechRecognition", file=sys.stderr)
        return
    try:
        idx = _get_input_device()
        if idx is not None:
            dev = sd.query_devices(idx)
            name = dev.get("name", "?") if isinstance(dev, dict) else str(dev)
            print(f"Mic: using input device [{idx}] {name}", file=sys.stderr)
        else:
            dev = sd.query_devices()
            name = dev.get("name", "?") if isinstance(dev, dict) else str(dev)
            print(f"Mic: default device: {name}", file=sys.stderr)
    except Exception as e:
        print(f"Mic: could not query device: {e}", file=sys.stderr)
    print("If no speech is detected: grant Microphone access to Terminal in System Settings > Privacy & Security > Microphone.", file=sys.stderr)


def record_until_stop(stop_event, chunk_sec: float = 0.1, min_duration_sec: float = 0.2, max_duration_sec: float = 120.0):
    """
    Record from mic in one continuous stream until stop_event.is_set() or max_duration_sec.
    Uses InputStream so the mic stays open (no macOS menu bar flicker).
    Records one extra chunk after stop so the last bit of speech isn't cut off.
    Returns (sample_rate, np.int16 array) or (None, None) on failure.
    """
    if not HAS_DEPS:
        return None, None
    import time
    import threading
    device = _get_input_device()
    chunks = []
    chunks_lock = threading.Lock()
    frames_per_chunk = int(SAMPLE_RATE * chunk_sec)
    block_duration = chunk_sec  # seconds per block for the stream

    def callback(indata, frames, time_info, status):
        if status:
            return
        if indata is not None and indata.size > 0:
            with chunks_lock:
                chunks.append(np.squeeze(indata).copy())

    kwargs = {
        "samplerate": SAMPLE_RATE,
        "channels": CHANNELS,
        "dtype": np.int16,
        "blocksize": frames_per_chunk,
    }
    if device is not None:
        kwargs["device"] = device

    start = time.time()
    try:
        stream = sd.InputStream(callback=callback, **kwargs)
        stream.start()
        try:
            while not stop_event.is_set() and (time.time() - start) < max_duration_sec:
                time.sleep(0.05)
            # One more short sleep so we get the last callback(s)
            time.sleep(block_duration + 0.05)
        finally:
            stream.stop()
            stream.close()
        with chunks_lock:
            if not chunks:
                return None, None
            out = np.concatenate(chunks)
        return SAMPLE_RATE, np.ascontiguousarray(out, dtype=np.int16)
    except Exception:
        return None, None


def transcribe_audio(samples: "np.ndarray", sample_rate: int) -> str:
    """Transcribe int16 mono audio. Returns transcript or empty string."""
    if not HAS_DEPS or samples is None or samples.size == 0:
        return ""
    if int(np.max(np.abs(samples))) == 0:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        path = f.name
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(samples.tobytes())
        try:
            r = sr.Recognizer()
            with sr.AudioFile(path) as source:
                audio = r.record(source)
            try:
                return (r.recognize_google(audio) or "").strip()
            except sr.UnknownValueError:
                return ""
            except sr.RequestError:
                try:
                    return (r.recognize_sphinx(audio) or "").strip()
                except Exception:
                    return ""
        except Exception:
            return ""
