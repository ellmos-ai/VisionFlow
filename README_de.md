# VisionFlow (Deutsch)

**Wiederverwendbare OpenCV-5-Abläufe, in denen die Pixel entscheiden, was als Nächstes passiert.**
Kamera, Video oder Einzelbild gehen hinein; OpenCV misst; die Messung öffnet einen Fall, fordert
weitere Belege an, bindet ein Modell und einen Menschen unter einer Frist ein und endet in einer
begrenzten, protokollierten Aktion. Das Vorzeigebeispiel ist eine **Falschfahrer-Warnung**, die
im Browser ausprobiert werden kann.

Die vollständige Beschreibung steht im [englischen README](README.md); dort finden sich auch
Live-Demo, Video und alle Belege. Gebaut von Lukas Geiger (Einzelteilnahme) für die
[OpenCV AI Competition 2026, powered by AWS](https://opencv26.devpost.com/).

![Spuren des Richtungswerkzeugs: grün mit dem Verkehr, rot dagegen](docs/media/direction-tracks.png)

## Der Ablauf

1. **Wahrnehmen.** Das Werkzeug `direction` verfolgt Graustufen-Bildausschnitte von Frame zu
   Frame und meldet Spuren, die sich gegen die konfigurierte Fahrtrichtung bewegen. Es sieht nur
   Pixel, nie Szenario-Bezeichnungen.
2. **Entscheiden.** Ein Treffer beendet die Beobachtung und öffnet einen Fall. Der Server schneidet
   einen unveränderlichen Clip (5 s davor, 10 s danach) und versiegelt ihn mit SHA-256. Modell und
   Mensch prüfen genau diesen Clip.
3. **Handeln.** Die Entscheidungsregel verwirft den Fall, meldet ihn bei hoher Sicherheit sofort
   oder wartet bis zur Frist auf einen Menschen. Ohne Entscheidung folgt die automatische Meldung
   an ein simuliertes Behördenpostfach, eine spätere Ablehnung sendet eine verknüpfte Entwarnung.
   Es wird nie etwas Echtes versendet.

Die Oberfläche ist standardmäßig englisch; Deutsch über den Knopf „DE“ oder `?lang=de`.

Jede Erkennung erzeugt ein nummeriertes Ticket an seiner aktuellen Station: Modellprüfung
in Raum 2, menschliche Prüfung in Raum 3 und abgelaufene Fristen in Raum 4. Ein zweites Ereignis
während einer Prüfung startet den nächsten Fall auf derselben Kamera; beim Kamera-Stopp
bleiben versiegelte Tickets entscheidbar.

## Schnellstart

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock      # Windows: .venv\Scripts\python.exe
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m visionflow.cli serve --port 8765      # danach http://127.0.0.1:8765/
```

## Wiederverwendbar

Abläufe sind JSON-Dateien (`examples/workflows/`). Derselbe Kern prüft die Schärfe eines
Dokumentfotos und fordert eine bessere Aufnahme an, beobachtet Bewegung in einem Video oder
überwacht eine Straße – mit Grenzen für Frames, Beobachtungszeit und Modellaufrufe in jedem Lauf.

## Auswertung und Grenzen

Synthetische RoadWorld-Auswertung (16 Sequenzen): Richtungskandidaten bei Normalverkehr 0/4,
Falschfahrer 4/4, Vogel 4/4, Blatt 4/4. Die Pixelstufe findet Gegenbewegung, keine Fahrzeuge;
Auto, Vogel und Blatt zu unterscheiden ist Aufgabe von Modell und Mensch. Der Verkehr ist
synthetisch, die Empfänger sind simuliert, und es gibt keine Aussage über reale Straßen.
Reproduzierbar mit `evaluation/roadworld_eval.py`.

Technischer Bericht: [docs/TECHNICAL_REPORT.md](docs/TECHNICAL_REPORT.md) (englisch).
