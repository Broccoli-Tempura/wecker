# Multisensueller Wecker (Raspberry Pi 4)

Steuerung über LCD und Drehknopf. Das Gespräch läuft über die OpenAI-API (Chat, TTS, STT). Der Pi macht Licht, Ton, Duft, Kugel und Menü.

## Ablauf

- T−4 min: Licht startet bei 0 und ist zur Weckzeit auf 50 %, um T+4 auf 100 %.
- T: Wecksound (sofort laut), Ventilator an, Servo wirft die Kugel.
- T+2: Wecksound aus, Gespräch beginnt.
- 7 Fragen, oder 2 unbeantwortet hintereinander: Aufforderung aufzustehen, dann Eskalation (Strobo und Alarmsound).
- Kugel 60 Sekunden auf der Lichtschranke: alles aus. Ein- und Ausschalten nur vor T.

## Menü

Drehdrücker: Druck wechselt die Funktion, Drehen stellt den Wert. Anzeige auf Englisch.

1. Anzeige
2. Uhrzeit (interner Offset, stellt nicht die Systemuhr)
3. Weckzeit
4. Wecksound (Singing bowl, Birds, Piano)
5. Alarmsound (Horn, Siren, Beep, Metal)
6. Genre (Brain teaser, Math, Trivia, Harry Potter)
7. Ein/Aus — links aus, rechts an. Während der Wecker läuft: „Use the ball / to turn off“

## Gespräch

- 7 Fragen, die letzte wird angekündigt.
- 10 Sekunden Wartezeit. Wird etwas gehört, stoppt die Aufnahme nach einer kurzen Pause.
- Nichts gehört: einmal wiederholen. Danach die Lösung sagen.
- Zwei unbeantwortete Fragen hintereinander: Eskalation.
- Mathefragen rechnet der Pi selbst. Gesprochene Zahlen zählen als richtig.

## Start

Headless, ohne Desktop und ohne Anmeldung. Der Benutzerdienst startet über linger.

```bash
sudo raspi-config nonint do_boot_target B1
sudo raspi-config nonint do_autologin 0
sudo loginctl enable-linger "$USER"
```

Dienst in `~/.config/systemd/user/wecker.service`. Lautstärke setzt der Dienst beim Start auf 100 %.

Manuell:

```bash
cd ~/wecker-pi
python3 -m wecker
```

Ohne Pi:

```bash
WECKER_MOCK=1 python3 -m wecker
```

`a`/`d` Rad, `m` Funktion, `s` Sensor, `r` Antwort, `q` Ende.

## Anschlüsse (BCM)

| Teil | GPIO |
| --- | --- |
| RGB R, G, B | 5, 6, 13 |
| Ventilator INA, INB | 24, 25 |
| Lichtschranke, active low | 4 |
| Servo Falltür | 19 |
| Schrittmotor IN1–4 | 17, 27, 22, 23 |
| Encoder A, B, Knopf | 9, 10, 11 |
| LCD I2C 16×2, Adresse 0x27 | 2, 3 |

Lautsprecher an der Klinke, Mikrofon per USB. Tondateien in `sounds/`. Zustand in `wecker-state.json`. API-Schlüssel in `wecker/talk.py` oder als `OPENAI_API_KEY`.
