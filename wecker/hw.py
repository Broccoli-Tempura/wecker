"""GPIO, LCD, Ton. Fällt auf eine Konsole zurück, wenn kein Pi da ist."""

from __future__ import annotations

import math
import struct
import threading
import time
import wave
from pathlib import Path

from wecker.talk import Conversation

SOUNDS = Path(__file__).resolve().parent.parent / "sounds"

# BCM. I2C (GPIO 2/3) bleibt fürs LCD frei.
PIN_RGB = (5, 6, 13)
PIN_FAN_INA = 24
PIN_FAN_INB = 25
PIN_SENSOR = 4
PIN_SERVO = 19
PIN_STEP = (17, 27, 22, 23)
PIN_ENC = (9, 10)
PIN_ENC_SW = 11
LCD_ADDR = 0x27
STEP_SEQUENCE = (
    (1, 0, 0, 0),
    (1, 1, 0, 0),
    (0, 1, 0, 0),
    (0, 1, 1, 0),
    (0, 0, 1, 0),
    (0, 0, 1, 1),
    (0, 0, 0, 1),
    (1, 0, 0, 1),
)
STEPS_OPEN = 16384


def on_pi() -> bool:
    try:
        model = Path("/proc/device-tree/model").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "Raspberry Pi" in model


def _pad16(text: str) -> str:
    return (text + " " * 16)[:16]


def ensure_sounds() -> None:
    SOUNDS.mkdir(parents=True, exist_ok=True)
    specs = {
        "wake_klangschale.wav": lambda i, n: 0.35 * math.sin(2 * math.pi * 174 * i / n) + 0.15 * math.sin(2 * math.pi * 261 * i / n),
        "wake_voegel.wav": lambda i, n: 0.3 * math.sin(2 * math.pi * (880 + 180 * math.sin(2 * math.pi * 6 * i / n)) * i / n),
        "wake_klavier.wav": lambda i, n: 0.3 * math.sin(2 * math.pi * 262 * i / n) * (0.4 + 0.6 * abs(math.sin(2 * math.pi * 2 * i / n))),
        "alarm_feuer.wav": lambda i, n: 0.45 * (1 if int(i / n / 0.25) % 2 == 0 else -1) * (980 if int(i / (n * 0.25)) % 2 == 0 else 740) / 980,
        "alarm_sirene.wav": lambda i, n: 0.4 * math.sin(2 * math.pi * (500 + 800 * abs(math.sin(2 * math.pi * 0.7 * i / n))) * i / n),
        "alarm_piep.wav": lambda i, n: 0.4 * math.sin(2 * math.pi * 1800 * i / n) if int(i / n / 0.15) % 2 == 0 else 0.0,
    }
    for name, fn in specs.items():
        path = SOUNDS / name
        if path.exists():
            continue
        _write_tone(path, fn)


def _write_tone(path: Path, fn, seconds: float = 2.0, rate: int = 22050) -> None:
    count = int(rate * seconds)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for i in range(count):
            sample = max(-1.0, min(1.0, fn(i, rate)))
            frames += struct.pack("<h", int(sample * 28000))
        handle.writeframes(frames)


class Hardware:
    def read_encoder(self) -> int:
        return 0

    def read_button(self) -> bool:
        return False

    def read_sensor(self) -> bool:
        return False

    def read_response(self) -> bool:
        return False

    def read_quiz_done(self) -> bool:
        return False

    def apply(self, view, wake_index: int, alarm_index: int, genre: str = "") -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


class MockHardware(Hardware):
    """Tasten: a/d Rad, m Funktion, s Sensor, r Antwort, q Ende."""

    def __init__(self) -> None:
        import select
        import sys

        self._select = select
        self._stdin = sys.stdin
        self._sensor = False
        self._quit = False
        self._delta = 0
        self._button = False
        self._response = False
        self._last = ""

    @property
    def quit(self) -> bool:
        return self._quit

    def _poll(self) -> None:
        while self._select.select([self._stdin], [], [], 0)[0]:
            char = self._stdin.read(1)
            if char in ("a", "A"):
                self._delta -= 1
            elif char in ("d", "D"):
                self._delta += 1
            elif char in ("m", "M"):
                self._button = True
            elif char in ("s", "S"):
                self._sensor = not self._sensor
                print(f"Sensor {'belegt' if self._sensor else 'frei'}")
            elif char in ("r", "R"):
                self._response = True
            elif char in ("q", "Q"):
                self._quit = True

    def read_encoder(self) -> int:
        self._poll()
        delta, self._delta = self._delta, 0
        return delta

    def read_button(self) -> bool:
        self._poll()
        pressed, self._button = self._button, False
        return pressed

    def read_sensor(self) -> bool:
        self._poll()
        return self._sensor

    def read_response(self) -> bool:
        self._poll()
        answered, self._response = self._response, False
        return answered

    def apply(self, view, wake_index: int, alarm_index: int, genre: str = "") -> None:
        line = (
            f"{view.lcd[0]} | {view.lcd[1]} | {view.phase} "
            f"licht={view.light:.2f} strobo={int(view.strobe)} duft={int(view.scent)} "
            f"klang={int(view.wake_on)}@{view.wake_volume:.2f} alarm={int(view.alarm_on)} "
            f"kugel={int(view.release_edge)} genre={genre}"
        )
        if line != self._last:
            print(line)
            self._last = line


class PiHardware(Hardware):
    def __init__(self) -> None:
        from gpiozero import Button, DigitalOutputDevice, PWMLED, RotaryEncoder, Servo, Motor

        self._button_edge = False
        self._encoder = RotaryEncoder(*PIN_ENC, max_steps=0, wrap=True)
        self._last_steps = 0
        self._switch = Button(PIN_ENC_SW, pull_up=True, bounce_time=0.05)
        self._switch.when_pressed = self._on_switch
        self._sensor = Button(PIN_SENSOR, pull_up=True, bounce_time=0.05)
        self._fan = Motor(PIN_FAN_INA, PIN_FAN_INB)
        self._rgb = tuple(PWMLED(pin) for pin in PIN_RGB)
        self._servo = Servo(PIN_SERVO, min_pulse_width=0.0005, max_pulse_width=0.0012)
        self._servo.min()
        time.sleep(1)
        self._servo.value = None
        self._steps = tuple(DigitalOutputDevice(pin) for pin in PIN_STEP)
        self._step_pos = 0
        self._scent_open = False
        self._lock = threading.Lock()
        self._lcd = self._open_lcd()
        self._lcd_cache = ("", "")
        self._lcd_logged = ("", "")
        self._lcd_next = 0.0
        self._channel = None
        self._playing = ""
        self._mixer = None
        self._convo = Conversation()
        self._ensure_mixer()
        ensure_sounds()

    def _on_switch(self) -> None:
        self._button_edge = True

    def _open_lcd(self):
        try:
            from RPLCD.i2c import CharLCD

            return CharLCD("PCF8574", LCD_ADDR, cols=16, rows=2)
        except Exception as exc:  # noqa: BLE001
            print(f"LCD aus: {exc}")
            return None

    def read_encoder(self) -> int:
        steps = self._encoder.steps
        delta, self._last_steps = steps - self._last_steps, steps
        return int(delta)

    def read_button(self) -> bool:
        pressed, self._button_edge = self._button_edge, False
        return pressed

    def read_sensor(self) -> bool:
        return bool(self._sensor.is_pressed)

    def read_response(self) -> bool:
        return self._convo.take_heard()

    def read_quiz_done(self) -> bool:
        return self._convo.take_finished()

    def apply(self, view, wake_index: int, alarm_index: int, genre: str = "") -> None:
        self._light(0.0 if view.strobe and not _strobe_on() else (1.0 if view.strobe else view.light), view.strobe)
        self._fan.forward(0.5) if view.scent and not view.conversation else self._fan.stop()
        self._move_scent(view.scent)
        if view.release_edge:
            threading.Thread(target=self._drop_ball, daemon=True).start()
        lines = view.lcd
        if view.conversation:
            self._convo.start(genre)
            line1, line2 = self._convo.lcd
            if line1:
                lines = (_pad16(line1), _pad16(line2))
        else:
            self._convo.stop()
        if self._convo.wants_mic():
            self._release_mixer()
            self._convo.mic_ready()
        else:
            self._ensure_mixer()
        self._lcd_write(lines)
        self._sound(view, wake_index, alarm_index)
        if view.conversation and not self._convo.wants_mic():
            self._convo.pump(self._channel, self._mixer)

    def _ensure_mixer(self) -> None:
        if self._mixer is not None and self._channel is not None:
            return
        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=1)
            self._mixer = pygame.mixer
            self._channel = pygame.mixer.Channel(0)
        except Exception as exc:  # noqa: BLE001
            print(f"Ton aus: {exc}")

    def _release_mixer(self) -> None:
        if self._channel is not None:
            try:
                self._channel.stop()
            except Exception:  # noqa: BLE001
                pass
        self._channel = None
        self._playing = ""
        if self._mixer is not None:
            try:
                self._mixer.quit()
            except Exception:  # noqa: BLE001
                pass
        self._mixer = None

    def _light(self, level: float, strobe: bool) -> None:
        if strobe and level > 0:
            for led in self._rgb:
                led.value = 1.0
            return
        # Warmes Tageslicht, keine blaue Bühne.
        gains = (1.0, 0.52, 0.08)
        for led, gain in zip(self._rgb, gains):
            led.value = max(0.0, min(1.0, level * gain))

    def _move_scent(self, open_: bool) -> None:
        if open_ == self._scent_open:
            return
        self._scent_open = open_
        direction = 1 if open_ else -1
        threading.Thread(target=self._step, args=(direction, STEPS_OPEN), daemon=True).start()

    def _step(self, direction: int, count: int) -> None:
        with self._lock:
            for _ in range(count):
                self._step_pos = (self._step_pos + direction) % len(STEP_SEQUENCE)
                for device, bit in zip(self._steps, STEP_SEQUENCE[self._step_pos]):
                    device.value = bit
                time.sleep(0.002)
            for device in self._steps:
                device.value = 0

    def _drop_ball(self) -> None:
        self._servo.max()
        time.sleep(1)
        self._servo.value = None
        time.sleep(5)
        self._servo.min()
        time.sleep(1)
        self._servo.value = None

    def _lcd_write(self, lines: tuple[str, str]) -> None:
        if lines != self._lcd_logged:
            print("LCD", lines[0], "|", lines[1])
            self._lcd_logged = lines
        if self._lcd is None or time.monotonic() < self._lcd_next or lines == self._lcd_cache:
            return
        try:
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(lines[0])
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(lines[1])
            self._lcd_cache = lines
        except OSError as exc:
            print("LCD Fehler:", exc)
            self._lcd_cache = ("", "")
            if exc.errno in (5, 121):
                time.sleep(0.5)
                try:
                    self._lcd.clear()
                except Exception as clear_exc:  # noqa: BLE001
                    print("LCD clear:", clear_exc)
            self._lcd_next = time.monotonic() + 0.5
        except Exception as exc:  # noqa: BLE001
            print("LCD Fehler:", exc)
            self._lcd_cache = ("", "")
            self._lcd_next = time.monotonic() + 0.5

    def _sound(self, view, wake_index: int, alarm_index: int) -> None:
        if self._channel is None or self._mixer is None:
            return
        if view.wake_on:
            name = ("wake_klangschale.wav", "wake_voegel.wav", "wake_klavier.wav")[wake_index]
            self._loop(name, view.wake_volume)
        elif view.alarm_on:
            name = ("alarm_feuer.wav", "alarm_sirene.wav", "alarm_piep.wav", "alarm_metal.wav")[alarm_index]
            self._loop(name, 1.0)
        elif self._playing:
            self._channel.stop()
            self._playing = ""

    def _loop(self, name: str, volume: float) -> None:
        if self._playing != name:
            self._channel.play(self._mixer.Sound(str(SOUNDS / name)), loops=-1)
            self._playing = name
        self._channel.set_volume(max(0.0, min(1.0, volume)))

    def close(self) -> None:
        self._convo.stop()
        self._fan.stop()
        for led in self._rgb:
            led.off()
        for device in self._steps:
            device.off()
        if self._channel is not None:
            self._channel.stop()


def _strobe_on() -> bool:
    return (time.monotonic() * 6) % 1 < 0.35
