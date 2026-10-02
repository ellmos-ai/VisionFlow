# Testen und lokale Abnahme

Diese Anleitung beschreibt die lokale Entwicklungsprüfung. Sie behauptet keine AWS-, Provider-, Kamera-Hardware-, Datensatz- oder Wettbewerbsabnahme.

## Umgebung

Im Repository-Root installierst du die gepinnten Pakete in eine virtuelle Umgebung und richtest den CLI-Import aus dem Quellcheckout ein:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pip check
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:PYTHONIOENCODING = "utf-8"
```

Die editierbare Installation wird auch für die echten Windows-Konsolenprogrammtests benötigt. Diese Tests starten `.venv/Scripts/visionflow.exe` ohne `PYTHONIOENCODING` und prüfen UTF-8-Ausgabe. Für `pytest` setzt `pyproject.toml` den `src`-Pfad bereits. Nutze den Interpreter aus `.venv`, damit die geprüfte OpenCV-Version verwendet wird.

## Gezielte Demosuite

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_demo.py -q
```

Die drei Tests erzeugen echte synthetische PNG- und AVI-Dateien mit OpenCV 5 und führen sie durch Registry, Controller und Fixture-Modelladapter. Sie prüfen:

- dass die erste Dokumentmessung eine echte Eingabeanforderung auslöst und eine zweite Bildquelle anschließend neu analysiert wird;
- dass der gemessene Laplace-Varianz-Wert des synthetischen Ersatzbilds höher ist;
- dass Video-Follow-up denselben fortlaufenden Mediencursor verwendet und bei 10 fps mit 2 fps die tatsächlichen Frame-Indizes `2, 7, 12` nach den Initialframes `0, 1` liefert;
- dass Trace-Dateien und Evidence-Dateien tatsächlich existieren und Evidence-Pfade innerhalb des Demo-Ausgabeordners bleiben;
- dass ein vorhandener Ausgabeordner abgewiesen wird, ohne seinen Inhalt zu überschreiben.

Die Demo injiziert eine Uhr, die Wartezeiten verkürzt. Sie ändert weder die Video-Timeline noch Frame-IDs, Messwerte oder Budgetgrenzen. Modellentscheidungen sind lokal festgelegt; es gibt keinen Netzwerkzugriff.

## CLI-Smoke-Test

Wähle einen neuen Ausgabeordner und führe den CLI-Demolauf aus:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m visionflow.cli demo run --output-dir "$env:TEMP\visionflow-demo-smoke-01"
```

Erwartet wird eine JSON-Antwort mit `status: "passed"`, `model_mode: "fixture"` und `network_used: false`. Öffne die angegebenen JSON-Traces und prüfe, dass deren Zustandswechsel und Frame-Ereignisse zu den zurückgegebenen Frame-IDs passen. Die Evidence-Refs zeigen auf die gespeicherten OpenCV-Framebilder im selben Ausgabeordner.

Ein realer lokaler CLI-Lauf unter Windows x64, Python 3.12.10 und OpenCV 5.0.0 ergab die synthetischen Schärfewerte `4.557734375` und `8000.528359375`. Bei 10 fps und einer angeforderten Beobachtung von 2 fps waren die Folgeframes `2, 7, 12`. Diese Werte beschreiben nur die erzeugten Testmedien.

## Weitere Tests

Die Task-8-Prüfung wird gezielt mit `tests/test_demo.py` ausgeführt. Für die integrierte lokale Regression läuft nach der Stabilisierung aller parallelen Tasks die vollständige Suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Der CLI- und API-Vertrag ist zusätzlich über `tests/test_cli.py` und `tests/test_api.py` abgedeckt. Ein laufender Server lässt sich ausschließlich an Loopback starten:

```powershell
.\.venv\Scripts\python.exe -m visionflow.cli serve --host 127.0.0.1 --port 8765 --data-dir .\.visionflow-data
```

Das Vorhandensein dieser Tests ersetzt keinen separat dokumentierten Browser- oder Hardwarelauf.

## Straßendemo-Tests und Browserabnahme

Die Tests für die Straßendemo umfassen Richtungsanalyse, Aufnahme-Journal, MP4-Clip-Export, Modellprüfung, Fristen und Arbeitsplatz:

```powershell
# Gezielte Road-Suite (API, CLI, Review)
.\.venv\Scripts\python.exe -m pytest tests/test_road_api.py tests/test_road_cli.py tests/test_road_review_api.py tests/test_road_review_cli.py -q

# Gesamtsuite inklusive aller 502 Tests
.\.venv\Scripts\python.exe -m pytest -q
```

Die automatisierte Browser-Abnahme prüft die Weboberfläche in nativem Microsoft Edge Headless (mit Playwright):

```powershell
# Kamera-Phase (Simulation, Event-Injektion, Controls)
python tests/road_browser_acceptance.py --phase camera --output .local/acceptance/run-camera-01

# Review-Phase (Arbeitsplatz, Countdown, Inbox, Entwarnung)
python tests/road_browser_acceptance.py --phase review --output .local/acceptance/run-review-01
```

Der Prüfharness erzeugt JSON-Ergebnisberichte (`acceptance.json`), Konsolenlogs sowie Screenshots (`landing.png`, `road-camera-before-start.png`, `road-camera-ready.png`) im angegebenen Ausgabeordner unter `.local/`.

## Medienzeit und Browserbelege

Dateivideo-Folgebeobachtung sampelt die Medienzeit, nicht die nächste Serie nativer Frames. Die lokale Implementierung benötigt eine positive, verlässliche FPS-Angabe und ein Backend, das die angeforderte Frameposition tatsächlich bestätigt. Unbekannte FPS oder unzuverlässiges Seek-Verhalten liefern einen strukturierten Medienfehler. Die synthetische AVI-Demo verwendet konstante Framerate; Videos mit variabler Framerate und reale Kameras sind nicht abgenommen.

Die unabhängige lokale Headless-Edge-Prüfung am 2026-10-02 bestand acht CLI-/Browserfälle einschließlich Upload, Neuaufnahme, Video-Folgebeobachtung, Freigabe, Abbruch, defekter Medien, Mehrtabbetrieb und 390-Pixel-Ansicht. Sie verwendete echte Serverprozesse, OpenCV und gespeicherte Framebelege. Screenshots und Ergebnis-JSON sind lokale Abnahmebelege; es gab keine externen Seitenanfragen und keine Browserfehler. Diese Prüfung ist keine Hardware- oder Wettbewerbsabnahme.

## Ausführungsregeln des Kerns

Zusammenlaufende gewählte Workflowzweige werden in topologischer Reihenfolge zusammengeführt; jeder Knoten läuft genau einmal. Mehrere Bildwerkzeuge verwenden dasselbe geladene Bild, wobei jeder Analyse-Knoten weiterhin das Frame-Verarbeitungsbudget belastet. Eine reine Texteingabe bewahrt Mediencursor und Werkzeugzustand; eine ausdrücklich gewählte Ersatzquelle startet einen neuen Analysezustand. Quellen bleiben während einer Eingabepause offen und werden bei Abschluss oder Abbruch geschlossen.

Bei Datei-Videos begrenzt die native Framerate die tatsächliche Abtastung; die konfigurierte `sample_fps` ist eine Obergrenze. Die Vorprüfung des Framebudgets bleibt konservativ anhand der angeforderten Rate. ROI-Findings bewahren die Region in Originalpixeln; `metrics.coordinate_system` kennzeichnet die Pixelkoordinaten des Bildausschnitts und der darin gemessenen Bewegungsregionen. Berichte und Nachrichtenentwürfe übernehmen nur die Metadaten früherer gerenderter Ausgaben, keine verschachtelten Berichtsinhalte.
