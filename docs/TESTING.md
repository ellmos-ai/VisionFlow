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

# Gesamtsuite
.\.venv\Scripts\python.exe -m pytest -q
```

GitHub Actions ist für dieselbe Suite und den CLI-Demolauf auf Ubuntu und Windows konfiguriert (`.github/workflows/ci.yml`); die aktuellen Jobs starten wegen einer Abrechnungssperre nicht. Ein Hosted-CI-PASS wird daraus nicht abgeleitet.

### Browserabnahme

Die Browserabnahmen sind eigenständige Skripte und werden von `pytest` nicht gesammelt. Sie starten jeweils einen eigenen Loopback-Server mit frischem Datenordner und schreiben Belege nur unter `.local/`. Voraussetzungen:

- Playwright in der Projektumgebung: `.\.venv\Scripts\python.exe -m pip install playwright`. Es steht nicht in der Lockdatei.
- Ein Chromium-Browser:
  - Unter Windows wird Microsoft Edge verwendet.
  - Auf anderen Systemen ein installiertes Chromium oder das von Playwright.
  - `VISIONFLOW_BROWSER` wählt ausdrücklich eine Browserdatei.

```powershell
# Kamera-Phase (Steuerelemente, keine Browserfehler, keine externen Anfragen)
.\.venv\Scripts\python.exe tests\road_browser_acceptance.py --phase camera --output .local/acceptance/run-camera-01

# Emergency-Leitstelle: drei vollständige echte Fälle über die Spieloberfläche
.\.venv\Scripts\python.exe tests\game_multievent_acceptance.py --output .local/acceptance/multievent-01
```

`road_browser_acceptance.py` schreibt `acceptance.json`, das Serverlog und Screenshots.

`game_multievent_acceptance.py` spielt drei Fälle durch:

1. **Profil `pending`:**
   - Falschfahrer und Blatt in die laufende Analyse einblenden.
   - Die echte Modellantwort `0,80` prüfen.
   - Prüfen, dass neue Ereignisse während der Prüfung gesperrt sind.
   - Den Server-Clip mit bestätigter SHA-256-Prüfsumme laden, dann die Gefahr bestätigen.
2. **Profil `leaf` mit eingeblendetem Vogel:** Die Oberfläche zeigt die Fixture-Antwort „Blatt“, nicht das Szenario.
3. **Profil `pending` ohne Entscheidung:**
   - Echte Frist in Raum 4.
   - Automatische Meldung nach Ablauf.
   - Späte Verneinung mit Entwarnung.

Ein Lauf dauert etwa drei Minuten. Das Open-Source-Chromium von Playwright enthält keinen H.264-Decoder. Der Clip wird dort geladen und geprüft, aber nicht abgespielt; Edge und Chrome spielen ihn ab.

## Medienzeit und Browserbelege

Dateivideo-Folgebeobachtung sampelt die Medienzeit, nicht die nächste Serie nativer Frames. Die lokale Implementierung benötigt eine positive, verlässliche FPS-Angabe und ein Backend, das die angeforderte Frameposition tatsächlich bestätigt. Unbekannte FPS oder unzuverlässiges Seek-Verhalten liefern einen strukturierten Medienfehler. Die synthetische AVI-Demo verwendet konstante Framerate; Videos mit variabler Framerate und reale Kameras sind nicht abgenommen.

Die unabhängige lokale Headless-Edge-Prüfung am 2026-10-02 bestand acht CLI-/Browserfälle einschließlich Upload, Neuaufnahme, Video-Folgebeobachtung, Freigabe, Abbruch, defekter Medien, Mehrtabbetrieb und 390-Pixel-Ansicht. Sie verwendete echte Serverprozesse, OpenCV und gespeicherte Framebelege. Screenshots und Ergebnis-JSON sind lokale Abnahmebelege; es gab keine externen Seitenanfragen und keine Browserfehler. Diese Prüfung ist keine Hardware- oder Wettbewerbsabnahme.

## Ausführungsregeln des Kerns

Zusammenlaufende gewählte Workflowzweige werden in topologischer Reihenfolge zusammengeführt; jeder Knoten läuft genau einmal. Mehrere Bildwerkzeuge verwenden dasselbe geladene Bild, wobei jeder Analyse-Knoten weiterhin das Frame-Verarbeitungsbudget belastet. Eine reine Texteingabe bewahrt Mediencursor und Werkzeugzustand; eine ausdrücklich gewählte Ersatzquelle startet einen neuen Analysezustand. Quellen bleiben während einer Eingabepause offen und werden bei Abschluss oder Abbruch geschlossen.

Bei Datei-Videos begrenzt die native Framerate die tatsächliche Abtastung; die konfigurierte `sample_fps` ist eine Obergrenze. Die Vorprüfung des Framebudgets bleibt konservativ anhand der angeforderten Rate. ROI-Findings bewahren die Region in Originalpixeln; `metrics.coordinate_system` kennzeichnet die Pixelkoordinaten des Bildausschnitts und der darin gemessenen Bewegungsregionen. Berichte und Nachrichtenentwürfe übernehmen nur die Metadaten früherer gerenderter Ausgaben, keine verschachtelten Berichtsinhalte.

## Prüfstand vom 8. Oktober 2026

Produktrevision `e65ef914`: vollständige Mac-Suite mit 549 bestandenen Tests in 101,46 Sekunden; tatsächliche Windows/Edge- und Mac/Chromium-Browserläufe mit jeweils drei vollständigen Fällen und elf erfolgreichen Checks. Die unveränderten 90-Sekunden-Grenzen und Assertions bleiben erhalten. Kein Browserfehler und keine externe Anfrage. Frühere Fehlläufe bleiben dokumentiert. Persönliche GUI- und Geräteabnahme durch Lukas sowie echte Provider-/AWS-Ausführung sind getrennte offene Gates.
