import unittest
from datetime import datetime

from wecker.logic import (
    HARD_AFTER_MS,
    MIN,
    NO_RESPONSE_MS,
    PRE_MS,
    TALK_AFTER_MS,
    Model,
    apply_encoder,
    tick,
)

DAY = int(datetime(2026, 10, 28, 7, 0, 0).timestamp() * 1000)


def model(**kwargs) -> Model:
    base = Model(alarm_hour=7, alarm_minute=0, armed=True, done_id=None, latched_at=None, last_now=None, phase="idle")
    return Model(**{**base.__dict__, **kwargs})


class LogicTest(unittest.TestCase):
    def test_idle_before_window(self):
        _model, view, _events = tick(model(), DAY - 6 * MIN, False)
        self.assertEqual(view.phase, "idle")
        self.assertEqual(view.light, 0)
        self.assertTrue(view.can_skip)

    def test_sunrise_level(self):
        _model, view, _events = tick(model(), DAY - 4 * MIN, False)
        self.assertEqual(view.phase, "sunrise")
        self.assertAlmostEqual(view.light, 0.1)

    def test_alarm_instant(self):
        _model, view, _events = tick(model(last_now=DAY - 1000), DAY, False)
        self.assertEqual(view.phase, "wake")
        self.assertAlmostEqual(view.light, 0.5)
        self.assertTrue(view.release_edge)
        self.assertTrue(view.scent)
        self.assertFalse(view.can_skip)
        self.assertAlmostEqual(view.wake_volume, 0.15)

    def test_talk_then_fire(self):
        started, view, events = tick(model(last_now=DAY - 1), DAY + TALK_AFTER_MS, False)
        self.assertEqual(view.phase, "talk")
        self.assertFalse(view.wake_on)
        self.assertIn("Gespraech", events)
        _model, late, _events = tick(started, DAY + TALK_AFTER_MS + NO_RESPONSE_MS, False)
        self.assertEqual(late.phase, "escalate")
        self.assertTrue(late.strobe)

    def test_response_delays_until_five_minutes(self):
        started, _view, _events = tick(model(last_now=DAY - 1), DAY + TALK_AFTER_MS, False)
        started.responded = True
        _model, talking = tick(started, DAY + 4 * MIN, False)[0:2]
        self.assertEqual(talking.phase, "talk")
        _model, late, _events = tick(_model, DAY + HARD_AFTER_MS, False)
        self.assertEqual(late.phase, "escalate")

    def test_ball_not_same_tick(self):
        nxt, view, _events = tick(model(last_now=DAY - 1000), DAY, True)
        self.assertTrue(view.release_edge)
        self.assertEqual(view.phase, "wake")
        _model, done, events = tick(nxt, DAY + 1000, True)
        self.assertEqual(done.phase, "idle")
        self.assertIn("Aus: Kugel liegt auf", events)

    def test_skip_blocked_after_start(self):
        sun, _view, _events = tick(model(menu="skip"), DAY - 2 * MIN, False)
        skipped, _shift = apply_encoder(sun, 1, DAY - 2 * MIN)
        self.assertIsNone(skipped.latched_at)
        wake, _view, _events = tick(model(menu="skip", last_now=DAY - 1), DAY + 30_000, False)
        blocked, _shift = apply_encoder(wake, 1, DAY + 30_000)
        self.assertEqual(blocked.latched_at, DAY)

    def test_no_catchup_after_reboot(self):
        later = int(datetime(2026, 10, 28, 8, 0, 0).timestamp() * 1000)
        _model, view, _events = tick(model(), later, False)
        self.assertEqual(view.phase, "idle")
        self.assertEqual(view.alarm_at, int(datetime(2026, 10, 29, 7, 0, 0).timestamp() * 1000))

    def test_full_light_at_hard_limit(self):
        _model, view, _events = tick(model(last_now=DAY - 1), DAY + HARD_AFTER_MS, False)
        self.assertEqual(view.phase, "escalate")
        self.assertAlmostEqual(view.light, 1)

    def test_pre_constant(self):
        self.assertEqual(PRE_MS, 5 * MIN)


if __name__ == "__main__":
    unittest.main()
