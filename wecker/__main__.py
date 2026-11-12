"""python -m wecker   (auf dem Pi)   oder   WECKER_MOCK=1 python -m wecker"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from wecker.hw import MockHardware, PiHardware, ensure_sounds, on_pi
from wecker.logic import ALARM_SOUNDS, GENRES, Model, apply_encoder, cycle_menu, mark_quiz_done, mark_response, tick

STATE = Path(__file__).resolve().parent.parent / "wecker-state.json"


def load() -> tuple[Model, int]:
    model = Model()
    offset = 0
    if not STATE.exists():
        return model, offset
    try:
        raw = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return model, offset
    for key in ("alarm_hour", "alarm_minute", "wake_index", "alarm_index", "genre_index"):
        if key in raw:
            setattr(model, key, int(raw[key]))
    model.alarm_index %= len(ALARM_SOUNDS)
    model.genre_index %= len(GENRES)
    model.armed = bool(raw.get("armed", True))
    model.menu = raw.get("menu", "home")
    offset = int(raw.get("clock_offset_ms", 0))
    return model, offset


def save(model: Model, offset: int) -> None:
    payload = {
        "alarm_hour": model.alarm_hour,
        "alarm_minute": model.alarm_minute,
        "armed": model.armed,
        "wake_index": model.wake_index,
        "alarm_index": model.alarm_index,
        "genre_index": model.genre_index,
        "done_id": model.done_id,
        "menu": model.menu,
        "latched_at": model.latched_at,
        "ball_released": model.ball_released,
        "responded": model.responded,
        "clock_offset_ms": offset,
    }
    STATE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def now_ms(offset: int) -> int:
    return int(time.time() * 1000) + offset


def main() -> None:
    ensure_sounds()
    mock = os.environ.get("WECKER_MOCK") == "1" or not on_pi()
    hw = MockHardware() if mock else PiHardware()
    model, offset = load()
    if mock:
        print("Mock: a/d Rad, m Funktion, s Sensor, r Antwort, q Ende")
    last_save = 0.0
    try:
        while True:
            if isinstance(hw, MockHardware) and hw.quit:
                break
            current = now_ms(offset)
            if hw.read_response():
                model = mark_response(model, current)
            if hw.read_quiz_done():
                model = mark_quiz_done(model, current)
            delta = hw.read_encoder()
            if model.menu == "home" and model.phase not in ("idle", "sunrise"):
                delta = 0
            if delta:
                step = max(-3, min(3, delta))
                model, shift = apply_encoder(model, step, current)
                offset += shift
                current = now_ms(offset)
            if hw.read_button():
                model = cycle_menu(model)
            model, view, _events = tick(model, current, hw.read_sensor())
            hw.apply(
                view,
                model.wake_index,
                model.alarm_index,
                GENRES[model.genre_index % len(GENRES)],
            )
            if time.monotonic() - last_save > 2:
                save(model, offset)
                last_save = time.monotonic()
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        save(model, offset)
        hw.close()


if __name__ == "__main__":
    main()
