"""Dieselbe Wecker-Zustandsmaschine wie im Simulator. Zeiten in Millisekunden."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

MIN = 60_000
PRE_MS = 4 * MIN
TALK_AFTER_MS = 2 * MIN
HARD_AFTER_MS = 4 * MIN
HOLD_MS = 60_000
EXPIRE_MS = 12 * 60 * MIN

WAKE_SOUNDS = ("Singing bowl", "Birds", "Piano")
ALARM_SOUNDS = ("Horn", "Siren", "Beep", "Metal")
GENRES = ("Brain teaser", "Math", "Trivia", "Harry Potter")
MENUS = ("home", "clock", "alarm", "wake", "alert", "genre", "skip")


@dataclass
class Model:
    alarm_hour: int = 7
    alarm_minute: int = 0
    armed: bool = True
    wake_index: int = 0
    alarm_index: int = 0
    genre_index: int = 0
    done_id: str | None = None
    latched_at: int | None = None
    ball_released: bool = False
    sensor_since: int | None = None
    responded: bool = False
    quiz_done: bool = False
    talk_started_at: int | None = None
    menu: str = "home"
    last_now: int | None = None
    phase: str = "idle"


@dataclass
class View:
    phase: str
    alarm_at: int | None
    light: float
    strobe: bool
    wake_on: bool
    wake_volume: float
    alarm_on: bool
    scent: bool
    release_edge: bool
    conversation: bool
    can_skip: bool
    lcd: tuple[str, str]


def occurrence_id(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000).strftime("%Y-%m-%dT%H:%M")


def _at_on_day(now_ms: int, day_offset: int, hour: int, minute: int) -> int:
    dt = datetime.fromtimestamp(now_ms / 1000)
    dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day_offset)
    return int(dt.timestamp() * 1000)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _pad16(text: str) -> str:
    return (text + " " * 16)[:16]


def _hhmm(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000)
    return dt.strftime("%H:%M")


def _hms(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000)
    return dt.strftime("%H:%M:%S")


def _clock(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _clear(model: Model) -> Model:
    return replace(model, latched_at=None, ball_released=False, responded=False, talk_started_at=None, sensor_since=None, quiz_done=False)


def _finish(model: Model, at_ms: int) -> Model:
    return replace(_clear(model), done_id=occurrence_id(at_ms), phase="idle")


def _select(model: Model, now: int) -> int | None:
    if not model.armed:
        return None
    if model.latched_at is not None and occurrence_id(model.latched_at) != model.done_id:
        if model.ball_released or now >= model.latched_at - PRE_MS:
            return model.latched_at
    prev = model.last_now
    for day in range(3):
        at = _at_on_day(now, day, model.alarm_hour, model.alarm_minute)
        if model.done_id == occurrence_id(at):
            continue
        if now < at:
            return at
        if prev is not None and prev < at <= now:
            return at
    return None


def _phase(now: int, at: int | None, quiz_done: bool) -> str:
    if at is None:
        return "idle"
    elapsed = now - at
    if elapsed < -PRE_MS:
        return "idle"
    if elapsed < 0:
        return "sunrise"
    if elapsed < TALK_AFTER_MS:
        return "wake"
    if quiz_done:
        return "escalate"
    return "talk"


def _lcd(model: Model, now: int, phase: str, at: int | None) -> tuple[str, str]:
    if model.menu == "clock":
        return _pad16("Set clock"), _pad16(f"> {_hms(now)}")
    if model.menu == "alarm":
        return _pad16("Alarm time"), _pad16(f"> {_clock(model.alarm_hour, model.alarm_minute)}")
    if model.menu == "wake":
        return _pad16("Wake sound"), _pad16(f"{model.wake_index + 1} {WAKE_SOUNDS[model.wake_index]}")
    if model.menu == "alert":
        name = ALARM_SOUNDS[model.alarm_index % len(ALARM_SOUNDS)]
        return _pad16("Alert sound"), _pad16(f"{model.alarm_index % len(ALARM_SOUNDS) + 1} {name}")
    if model.menu == "genre":
        return _pad16("Genre"), _pad16(GENRES[model.genre_index % len(GENRES)])
    if model.menu == "skip":
        state = "on" if model.armed else "off"
        if phase not in ("idle", "sunrise"):
            return _pad16("Use the ball"), _pad16("to turn off")
        return _pad16("L off  R on"), _pad16(state)
    line2 = "Alarm off"
    if phase == "sunrise":
        line2 = "Light rising"
    elif phase == "wake":
        line2 = "Wake up!"
    elif phase == "talk":
        line2 = "Listening" if model.responded else "Talk to me"
    elif phase == "escalate":
        line2 = "GET UP NOW"
    elif model.armed and at is not None:
        line2 = f"Alarm {_hhmm(at)}"
    return _pad16(_hms(now)), _pad16(line2)


def derive(model: Model, now: int) -> View:
    at = _select(model, now)
    phase = _phase(now, at, model.quiz_done)
    active = phase in ("wake", "talk", "escalate")
    light = 0.0 if at is None or phase == "idle" else _clamp01((now - (at - PRE_MS)) / (PRE_MS + HARD_AFTER_MS))
    wake_volume = 1.0 if phase == "wake" and at is not None else 0.0
    before = phase in ("idle", "sunrise")
    view = View(
        phase=phase,
        alarm_at=at,
        light=light,
        strobe=phase == "escalate",
        wake_on=phase == "wake",
        wake_volume=wake_volume,
        alarm_on=phase == "escalate",
        scent=active,
        release_edge=False,
        conversation=phase == "talk",
        can_skip=model.armed and before,
        lcd=_lcd(model, now, phase, at),
    )
    if model.sensor_since is not None and model.latched_at is not None:
        left = max(0, HOLD_MS - (now - model.sensor_since))
        view.light = 0
        view.strobe = False
        view.wake_on = False
        view.wake_volume = 0
        view.alarm_on = False
        view.scent = False
        view.conversation = False
        view.lcd = (_pad16("Hold the ball"), _pad16(f"{(left + 999) // 1000}s left"))
    return view


def tick(model: Model, now: int, sensor: bool) -> tuple[Model, View, list[str]]:
    nxt = model
    events: list[str] = []
    if (
        nxt.latched_at is not None
        and nxt.ball_released
        and now - nxt.latched_at > EXPIRE_MS
        and occurrence_id(nxt.latched_at) != nxt.done_id
    ):
        nxt = _finish(nxt, nxt.latched_at)
        events.append("Sequenz verfallen")
    if nxt.latched_at is not None and occurrence_id(nxt.latched_at) != nxt.done_id:
        if not nxt.ball_released and now < nxt.latched_at - PRE_MS:
            nxt = _clear(nxt)
    target = _select(nxt, now)
    if target is not None and now >= target - PRE_MS and nxt.latched_at != target:
        nxt = replace(_clear(nxt), latched_at=target)
        events.append("Licht startet")
    at = nxt.latched_at
    phase = _phase(now, at, nxt.quiz_done)
    release_edge = False
    already = nxt.ball_released
    if at is not None and phase in ("wake", "talk", "escalate") and not already:
        nxt = replace(nxt, ball_released=True)
        release_edge = True
        events.append("Kugel, Klang, Duft")
    if phase == "talk" and nxt.talk_started_at is None and at is not None:
        nxt = replace(nxt, talk_started_at=at + TALK_AFTER_MS)
        events.append("Gespraech")
    if phase == "escalate" and nxt.phase != "escalate":
        events.append("Get up")
    if nxt.sensor_since is not None and not sensor:
        nxt = replace(nxt, sensor_since=None)
    elif already and sensor and phase in ("wake", "talk", "escalate") and at is not None:
        since = now if nxt.sensor_since is None else nxt.sensor_since
        if now - since >= HOLD_MS:
            nxt = _finish(nxt, at)
            events.append("Aus: Kugel liegt auf")
        else:
            nxt = replace(nxt, sensor_since=since)
    nxt = replace(nxt, last_now=now, phase="idle" if nxt.latched_at is None else phase)
    view = derive(nxt, now)
    view.release_edge = release_edge
    return nxt, view, events


def cycle_menu(model: Model) -> Model:
    index = MENUS.index(model.menu) if model.menu in MENUS else 0
    return replace(model, menu=MENUS[(index + 1) % len(MENUS)])


def apply_encoder(model: Model, delta: int, now: int) -> tuple[Model, int]:
    if delta == 0:
        return model, 0
    view = derive(model, now)
    if model.menu == "clock":
        return model, delta * MIN
    if model.menu == "alarm":
        total = (model.alarm_hour * 60 + model.alarm_minute + delta) % (24 * 60)
        nxt = replace(model, alarm_hour=total // 60, alarm_minute=total % 60, armed=True)
        if not model.ball_released:
            nxt = _clear(replace(nxt, done_id=None))
        return nxt, 0
    if model.menu == "wake":
        nxt_index = (model.wake_index + delta) % len(WAKE_SOUNDS)
        return replace(model, wake_index=nxt_index), 0
    if model.menu == "alert":
        nxt_index = (model.alarm_index + delta) % len(ALARM_SOUNDS)
        return replace(model, alarm_index=nxt_index), 0
    if model.menu == "genre":
        nxt_index = (model.genre_index + delta) % len(GENRES)
        return replace(model, genre_index=nxt_index), 0
    if model.menu == "skip":
        if view.phase not in ("idle", "sunrise"):
            return model, 0
        if delta < 0 and model.armed:
            return replace(_clear(model), armed=False), 0
        if delta > 0 and not model.armed:
            return replace(model, armed=True, done_id=None), 0
        return model, 0
    return model, 0


def mark_quiz_done(model: Model, now: int) -> Model:
    if derive(model, now).phase != "talk":
        return model
    return replace(model, quiz_done=True)


def mark_response(model: Model, now: int) -> Model:
    if derive(model, now).phase != "talk":
        return model
    return replace(model, responded=True)
