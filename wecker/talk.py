"""Gespräch über die OpenAI-API. Fragen und Antworten auf Englisch.

Der Pi nimmt auf, schickt die Aufnahme weg und spielt die Antwort ab.
Eine falsche Antwort zählt als Antwort. Nur Stille lässt den 90-Sekunden-Timer weiterlaufen.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# API-Schlüssel hier einsetzen. Nicht weitergeben.
# Alternativ vor dem Start: export OPENAI_API_KEY="sk-..."
OPENAI_API_KEY = ""  # <-- hier den Schlüssel eintragen

CHAT_MODEL = "gpt-4o-mini"
TTS_MODEL = "tts-1"
STT_MODEL = "gpt-4o-mini-transcribe"
VOICE = "alloy"

# USB-Mikrofon. `arecord -l` zeigt die Karte, z.B. "plughw:1,0".
MIC_DEVICE = "default"
LISTEN_SECONDS = 8
# Ohne Sprache so lange warten. Beide Versuche, nicht 20 Sekunden.
THINK_SECONDS = 10
# Unter dieser Lautstärke gilt die Aufnahme als Stille und wird nicht geschickt.
SILENCE_RMS = 250

GENRE_HINT = {
    "Brain teaser": "a short brain teaser with one clear answer",
    "Math": "a mental arithmetic problem with at most 3 operators, whose answer is a small integer",
    "Trivia": "a general knowledge question",
    "Harry Potter": "a Harry Potter trivia question",
}
QUIZ_COUNT = 7


def api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip() or OPENAI_API_KEY.strip()


def parse_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)


def _request(url: str, payload: bytes, content_type: str, timeout: float = 30) -> bytes:
    key = api_key()
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"OpenAI {exc.code}: {detail}") from exc


def chat(messages: list[dict], temperature: float = 0.7) -> dict:
    body = json.dumps(
        {
            "model": CHAT_MODEL,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": temperature,
        }
    ).encode()
    raw = _request("https://api.openai.com/v1/chat/completions", body, "application/json")
    content = json.loads(raw)["choices"][0]["message"]["content"]
    return parse_json(content)


ASKED = Path(__file__).resolve().parent.parent / "asked.json"
DIFFICULTY = {
    1: "very easy",
    2: "easy",
    3: "medium",
    4: "hard",
    5: "hard and specific",
}


def _load_asked() -> list[str]:
    try:
        raw = json.loads(ASKED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw][-30:]


def _save_asked(items: list[str]) -> None:
    try:
        ASKED.write_text(json.dumps(items[-30:], ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print("Fragen nicht gespeichert:", exc)


def ask_question(genre: str, previous: list[str], level: int) -> tuple[str, str]:
    import random

    if genre == "Math":
        for _ in range(8):
            question, solution = _make_math(level)
            if question not in previous:
                return question, solution
        return _make_math(level)
    hint = GENRE_HINT.get(genre, "a short quiz question")
    used = " | ".join(previous[-12:]) or "none"
    rank = max(1, min(5, level))
    system = (
        "You write one wake-up quiz question in simple spoken English. "
        f"Genre: {hint}. Difficulty: {DIFFICULTY[rank]} ({rank} of 5). "
        f"Variation {random.randint(1, 1_000_000_000)}. "
        "One new question only, no greeting, under 18 words. "
        'Reply as JSON: {"question": "...", "answer": "the short spoken answer"}. '
        f"Do not repeat or rephrase any of these: {used}"
    )
    data = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "Ask the next question."},
        ],
        temperature=1.0,
    )
    question = str(data.get("question", "")).strip()
    answer = str(data.get("answer", "")).strip()
    if not question:
        raise RuntimeError("leere Frage")
    return question, answer or "I am not sure."


def _make_math(level: int) -> tuple[str, str]:
    import random

    rank = max(1, min(5, level))

    def n(lo: int, hi: int) -> int:
        return random.randint(lo, hi)

    if rank <= 1:
        a, b = n(1, 9), n(1, 9)
        parts = [(a, None), (b, "+")]
    elif rank == 2:
        a, b, c = n(2, 12), n(1, 9), n(1, 9)
        parts = [(a, None), (b, "+"), (c, "-")]
    elif rank == 3:
        a, b, c = n(3, 20), n(1, 12), n(1, 12)
        op = random.choice(["+", "-"])
        parts = [(a, None), (b, "+"), (c, op)]
    elif rank == 4:
        a, b, c = n(2, 9), n(2, 9), n(1, 12)
        parts = [(a, None), (b, "*"), (c, random.choice(["+", "-"]))]
    else:
        a, b, c, d = n(2, 9), n(2, 6), n(1, 9), n(1, 9)
        parts = [(a, None), (b, "*"), (c, "+"), (d, "-")]
    value = parts[0][0]
    words = [str(parts[0][0])]
    names = {"+": "plus", "-": "minus", "*": "times"}
    for number, op in parts[1:]:
        value = value + number if op == "+" else value - number if op == "-" else value * number
        words.extend((names[op], str(number)))
    return "What is " + " ".join(words) + "?", str(value)


_NUMBER_WORDS = {
    "zero": 0, "oh": 0, "one": 1, "won": 1, "two": 2, "to": 2, "too": 2,
    "three": 3, "four": 4, "for": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "ate": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}


def _spoken_numbers(text: str) -> list[int]:
    import re

    found = [int(item) for item in re.findall(r"-?\d+", text)]
    words = re.findall(r"[a-z]+", text.lower().replace("-", " "))
    current: int | None = None
    negative = False

    def flush() -> None:
        nonlocal current, negative
        if current is None:
            return
        found.append(-current if negative else current)
        current = None
        negative = False

    for word in words:
        if word in ("and", "a"):
            continue
        if word in ("minus", "negative") and current is None:
            negative = True
            continue
        if word == "hundred" and current is not None:
            current *= 100
            continue
        value = _NUMBER_WORDS.get(word)
        if value is None:
            flush()
            continue
        if current is None:
            current = value
        elif value < 10 and 20 <= current < 100 and current % 10 == 0:
            current += value
        else:
            flush()
            current = value
    flush()
    return found


def judge(genre: str, question: str, answer: str, solution: str = "") -> tuple[bool, str]:
    if genre == "Math":
        heard = _spoken_numbers(answer)
        try:
            expected = int(solution)
        except ValueError:
            expected = None
        print("Mathe", expected, "gehört", heard, "roh", answer)
        if expected is not None and expected in heard:
            return True, f"Yes, {expected}."
        if expected is not None:
            return False, f"No. The answer is {expected}."
    system = (
        "You grade a spoken quiz answer. Be lenient with wording and number words. "
        'Reply as JSON: {"correct": true, "say": "one short English sentence"}. '
        "say tells them if they were right and, if wrong, the correct answer. Under 16 words."
    )
    data = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Genre: {genre}\nQuestion: {question}\nAnswer: {answer}"},
        ]
    )
    say = str(data.get("say", "")).strip() or "Okay."
    return bool(data.get("correct")), say


_mic_name: str | None = None


def capture_device() -> str:
    global _mic_name
    if _mic_name:
        return _mic_name
    chosen = os.environ.get("WECKER_MIC", "").strip() or ("" if MIC_DEVICE == "default" else MIC_DEVICE)
    if chosen:
        _mic_name = chosen
        print("Mikrofon", _mic_name)
        return _mic_name
    try:
        listing = subprocess.check_output(["arecord", "-l"], text=True, stderr=subprocess.STDOUT, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Kein Mikrofon") from exc
    for line in listing.splitlines():
        if not line.startswith("card "):
            continue
        bits = line.replace(":", " ").split()
        if "card" in bits and "device" in bits:
            card = bits[bits.index("card") + 1]
            device = bits[bits.index("device") + 1]
            _mic_name = f"plughw:{card},{device}"
            print("Mikrofon", _mic_name)
            return _mic_name
    raise RuntimeError("Kein Mikrofon")


def speak(text: str, path: Path) -> None:
    body = json.dumps(
        {"model": TTS_MODEL, "voice": VOICE, "input": text, "response_format": "wav"}
    ).encode()
    raw = _request("https://api.openai.com/v1/audio/speech", body, "application/json", timeout=40)
    if not raw.startswith(b"RIFF"):
        snippet = raw[:80].decode("utf-8", "replace").replace("\n", " ")
        raise RuntimeError(snippet or "keine WAV")
    path.write_bytes(_small_wav(raw))


def _small_wav(raw: bytes) -> bytes:
    """Kleine Mono-WAV, 22050 Hz, höchstens 12 Sekunden. pygame lädt sie sonst ganz in den Speicher."""
    import array
    import io
    import wave

    if len(raw) < 44 or raw[:4] != b"RIFF":
        raise RuntimeError("keine WAV")
    pos = 12
    fmt = b""
    data = b""
    while pos + 8 <= len(raw):
        kind = raw[pos : pos + 4]
        size = int.from_bytes(raw[pos + 4 : pos + 8], "little")
        start = pos + 8
        end = min(len(raw), start + size)
        if kind == b"fmt ":
            fmt = raw[start:end]
        elif kind == b"data":
            data = raw[start:end]
            break
        pos = start + size + (size & 1)
    if len(fmt) < 16 or not data:
        raise RuntimeError("WAV ohne Daten")
    audio_format = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    rate = int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    if audio_format not in (1, 65534) or bits != 16 or channels not in (1, 2) or not 8000 <= rate <= 48000:
        raise RuntimeError(f"{audio_format}/{channels}ch/{rate}Hz/{bits}bit")
    frame = channels * 2
    data = data[: rate * 12 * frame]
    count = len(data) // frame
    samples = array.array("h")
    if channels == 1:
        samples.frombytes(data[: count * 2])
    else:
        for index in range(count):
            off = index * 4
            left = int.from_bytes(data[off : off + 2], "little", signed=True)
            right = int.from_bytes(data[off + 2 : off + 4], "little", signed=True)
            samples.append((left + right) // 2)
    target = 22050
    if rate != target and samples:
        resized = array.array("h")
        last = len(samples) - 1
        step = rate / target
        cursor = 0.0
        while cursor < len(samples):
            resized.append(samples[min(last, int(cursor))])
            cursor += step
        samples = resized
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(target)
        handle.writeframes(samples.tobytes())
    return buf.getvalue()


def _multipart(fields: dict[str, str], file_name: str, file_bytes: bytes) -> tuple[str, bytes]:
    boundary = "----weckerTalk7f3a"
    chunks = bytearray()
    for key, value in fields.items():
        chunks += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n"
        ).encode()
    chunks += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file_name}\"\r\n"
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode()
    chunks += file_bytes
    chunks += f"\r\n--{boundary}--\r\n".encode()
    return boundary, bytes(chunks)


def _rms(path: Path) -> float:
    data = path.read_bytes()
    start = data.find(b"data")
    if start < 0:
        return 0.0
    pcm = data[start + 8 :]
    count = len(pcm) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack("<" + "h" * count, pcm[: count * 2])
    return (sum(sample * sample for sample in samples) / count) ** 0.5


def transcribe(path: Path) -> str:
    loudness = _rms(path)
    print(f"Mikrofon RMS {loudness:.0f}")
    if loudness < SILENCE_RMS:
        return ""
    boundary, payload = _multipart(
        {"model": STT_MODEL, "language": "en"},
        path.name,
        path.read_bytes(),
    )
    raw = _request(
        "https://api.openai.com/v1/audio/transcriptions",
        payload,
        f"multipart/form-data; boundary={boundary}",
        timeout=40,
    )
    text = str(json.loads(raw).get("text", "")).strip()
    print("Gehört:", text)
    return text


def record(path: Path, stop: threading.Event) -> bool:
    """Ohne Sprache 10 Sekunden. Sobald jemand spricht, Ende nach einer kurzen Pause."""
    try:
        device = capture_device()
    except RuntimeError as exc:
        print(exc)
        return False
    proc = subprocess.Popen(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw", "-q"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    pcm = bytearray()
    speech_at: float | None = None
    quiet_at: float | None = None
    loud_run = 0
    started = time.monotonic()
    frame = 1600 * 2
    assert proc.stdout is not None
    try:
        while not stop.is_set():
            now = time.monotonic()
            if speech_at is None and now - started >= THINK_SECONDS:
                break
            if speech_at is not None and now - speech_at >= LISTEN_SECONDS:
                break
            chunk = proc.stdout.read(frame)
            if not chunk:
                break
            pcm.extend(chunk)
            if _pcm_rms(chunk) >= SILENCE_RMS:
                loud_run += 1
                quiet_at = None
                if speech_at is None and loud_run >= 4:
                    speech_at = time.monotonic()
            else:
                loud_run = 0
                if speech_at is not None:
                    if quiet_at is None:
                        quiet_at = time.monotonic()
                    elif time.monotonic() - quiet_at >= 0.8:
                        break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
    print(f"Aufnahme {time.monotonic() - started:.1f}s")
    if not pcm:
        print("arecord: keine Daten")
        return False
    _write_wav(path, bytes(pcm))
    return True


def _pcm_rms(pcm: bytes) -> float:
    count = len(pcm) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack("<" + "h" * count, pcm[: count * 2])
    return (sum(sample * sample for sample in samples) / count) ** 0.5


def _write_wav(path: Path, pcm: bytes) -> None:
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm)


class Conversation:
    """Läuft im Hintergrund. pygame wird nur vom Hauptthread über pump() bedient."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._played = threading.Event()
        self._need_mic = threading.Event()
        self._mic_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._wav: str | None = None
        self._started = False
        self._heard = False
        self._finished = False
        self.lcd = ("", "")
        self._count = 0

    def start(self, genre: str) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._need_mic.clear()
        self._mic_ready.clear()
        self._thread = threading.Thread(target=self._loop, args=(genre,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._played.set()
        self._mic_ready.set()
        self._need_mic.clear()

    def wants_mic(self) -> bool:
        return self._need_mic.is_set()

    def mic_ready(self) -> None:
        self._mic_ready.set()

    def take_heard(self) -> bool:
        with self._lock:
            heard, self._heard = self._heard, False
            return heard

    def take_finished(self) -> bool:
        with self._lock:
            finished, self._finished = self._finished, False
            return finished

    def pump(self, channel, mixer) -> None:
        with self._lock:
            wav = self._wav
            started = self._started
        if not wav:
            return
        if channel is None or mixer is None:
            with self._lock:
                self._wav = None
                self._started = False
            self._played.set()
            return
        if not started:
            try:
                if Path(wav).stat().st_size > 700_000:
                    raise RuntimeError("Datei zu gross")
                channel.play(mixer.Sound(wav))
            except Exception as exc:  # noqa: BLE001
                print("Abspielen fehlgeschlagen:", exc)
                with self._lock:
                    self._wav = None
                    self._started = False
                self._played.set()
                return
            with self._lock:
                self._started = True
            return
        if not channel.get_busy():
            with self._lock:
                self._wav = None
                self._started = False
            self._played.set()

    def _set_lcd(self, line1: str, line2: str) -> None:
        with self._lock:
            self.lcd = (line1, line2)

    def _mark_heard(self) -> None:
        with self._lock:
            self._heard = True

    def _mark_finished(self) -> None:
        with self._lock:
            self._finished = True

    def _say(self, text: str) -> bool:
        self._count += 1
        spoken = Path(f"/tmp/wecker-say-{self._count}.wav")
        try:
            speak(text, spoken)
            self._offer(spoken)
        except Exception as exc:  # noqa: BLE001
            print("Stimme fehlgeschlagen:", exc)
            self._set_lcd("No voice", str(exc).replace("\n", " ")[:16])
            self._stop.wait(3)
            return not self._stop.is_set()
        return not self._stop.is_set()

    def _listen(self, genre: str) -> str:
        self._set_lcd("Your turn", genre)
        self._mic_ready.clear()
        self._need_mic.set()
        if not self._mic_ready.wait(3):
            print("Mikrofon: Tonausgabe nicht freigegeben")
        self._stop.wait(0.4)
        captured = Path("/tmp/wecker-answer.wav")
        try:
            ok = record(captured, self._stop)
            return transcribe(captured) if ok else ""
        except Exception as exc:  # noqa: BLE001
            print("Aufnahme fehlgeschlagen:", exc)
            return ""
        finally:
            self._need_mic.clear()

    def _loop(self, genre: str) -> None:
        previous = _load_asked()
        unanswered = 0
        for number in range(1, QUIZ_COUNT + 1):
            if self._stop.is_set():
                return
            if not api_key():
                self._set_lcd("No API key", "see talk.py")
                print("OpenAI: Schlüssel fehlt. Eintragen in wecker/talk.py bei OPENAI_API_KEY.")
                if self._stop.wait(3):
                    return
                continue
            if number == QUIZ_COUNT:
                self._set_lcd("Last question", genre)
                if not self._say("This is the last question."):
                    return
            try:
                question, answer = ask_question(genre, previous, number)
            except Exception as exc:  # noqa: BLE001
                print("Frage fehlgeschlagen:", exc)
                self._set_lcd("API error", "question")
                if self._stop.wait(5):
                    return
                continue
            previous.append(question)
            _save_asked(previous)
            self._set_lcd("Listen", f"{number}/{QUIZ_COUNT}")
            if not self._say(question):
                return
            text = self._listen(genre)
            if self._stop.is_set():
                return
            if not text:
                self._set_lcd("Say again", genre)
                if not self._say(f"I couldn't hear you. The question was: {question}"):
                    return
                text = self._listen(genre)
                if self._stop.is_set():
                    return
            if not text:
                unanswered += 1
                self._set_lcd("The answer", f"{unanswered} missed")
                print("Nichts gehört.")
                if not self._say(f"The answer was: {answer}."):
                    return
                if unanswered >= 2:
                    self._set_lcd("Please get up", genre)
                    self._say("Please get up now. The alarm will continue until the ball is in place.")
                    self._mark_finished()
                    return
                continue
            unanswered = 0
            self._mark_heard()
            try:
                correct, say = judge(genre, question, text, answer)
            except Exception as exc:  # noqa: BLE001
                print("Bewertung fehlgeschlagen:", exc)
                correct, say = False, "Thanks."
            self._set_lcd("Correct" if correct else "Wrong", f"{number}/{QUIZ_COUNT}")
            if not self._say(say):
                return
        if self._stop.is_set():
            return
        self._set_lcd("Please get up", genre)
        self._say("Please get up now. The alarm will continue until the ball is in place.")
        self._mark_finished()

    def _offer(self, path: Path) -> None:
        self._played.clear()
        with self._lock:
            self._wav = str(path)
            self._started = False
        while not self._stop.is_set():
            if self._played.wait(0.2):
                return

