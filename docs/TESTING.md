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

Die editierbare Installation wird auch für die echten Windows-Konsolenprogrammtests benötigt. Diese Tests starten `.venv/Scripts/visionflow.exe` ohne `PYTHONIOENCODING` und prüfen UTF-8-Ausgabe. Für `pytest` setzt `pyproject.toml` den `src`-Pfad bereits. Nutze den Interpreter aus `.venv`, damit die geprüfte OpenCV-Version verwendet wird. Diese Core-Umgebung enthält absichtlich kein Playwright; Collection und normale pytest-Läufe starten keinen Browser.

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

## Straßendemo-Coretests und Legacy-DOM-Smoke

Die Tests für die Straßendemo umfassen Richtungsanalyse, Aufnahme-Journal, MP4-Clip-Export, Modellprüfung, Fristen und Arbeitsplatz:

```powershell
# Gezielte Road-Suite (API, CLI, Review)
.\.venv\Scripts\python.exe -m pytest tests/test_road_api.py tests/test_road_cli.py tests/test_road_review_api.py tests/test_road_review_cli.py -q

# Vollständige Core-Testsuite
.\.venv\Scripts\python.exe -m pytest -q
```

Der ältere `road_browser_acceptance.py`-Lauf ist nur ein DOM-Smoke-Test. Er prüft, ob ausgewählte Controls und Container im DOM vorhanden sind und ob die Road-Kameraoberfläche sichtbar ist. Er beweist weder reale Case-/Job-/ModelExchange-/Clip-Identität noch Videobytes, Decode oder menschliche Reviewaktionen. Führe ihn nur als schnellen Oberflächen-Smoke mit einer separat verfügbaren Playwright-Installation aus:

```powershell
# Legacy-DOM-Smoke: Kameracontainer und Controls zählen
python tests/road_browser_acceptance.py --phase camera --output .local/acceptance/run-camera-01

# Legacy-DOM-Smoke: Reviewcontainer zählen
python tests/road_browser_acceptance.py --phase review --output .local/acceptance/run-review-01
```

Der Prüfharness kennzeichnet seine Berichte mit `test_level: dom_smoke`. JSON-Berichte und Screenshots sind keine GUI-Akzeptanzbelege.

## Vier-Räume-Browserabnahme

Der eigenständige Runner `tests/gui_browser_acceptance.py` prüft den realen lokalen API-/GUI-Pfad in nativem Microsoft Edge. Installiere Playwright in einer separaten Umgebung; `requirements.browser.lock` pinnt Playwright 1.60.0 einschließlich der lokal aufgelösten Abhängigkeiten. `pyproject.toml` stellt zusätzlich das optionale Extra `browser-test` für manuelle Installationen bereit. System-Edge und FFmpeg werden unabhängig verwaltet. Der echte Server verwendet die bestehende Core-Umgebung `.venv` und bekommt pro Lauf einen neuen lokalen Datenordner.

```powershell
py -3.12 -m venv .venv-browser
.\.venv-browser\Scripts\python.exe -m pip install -r requirements.browser.lock
.\.venv-browser\Scripts\python.exe -m pip check

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
.\.venv-browser\Scripts\python.exe tests\gui_browser_acceptance.py `
  --phase fast `
  --server-python .\.venv\Scripts\python.exe `
  --browser-path $edge `
  --output ".local\acceptance\gui-controller-fast-$stamp"
```

Der Runner nimmt ausschließlich frische Ordner der Form `.local/acceptance/gui-controller-{red|fast|full}-<timestamp>` an. `red` ist der Vorher-Beleg für die frühere GUI und endet erwartungsgemäß mit Exit-Code 1, sobald echte Case-, Exchange- und Clipdaten bereit sind und die alten UI-Assertions scheitern. Für den reparierten Stand sind `fast` und `full` maßgeblich. `fast` prüft einen echten `wrong_way/pending`-Fall einschließlich empfangener MP4-Bytes, SHA-256, nativem Play/Pause/Seek, sichtbarem Prompt/Antwort sowie Reject/Kaffee ohne Falscheskalation. `full` ergänzt mehrere serverseitige Jobs und Fälle, stabile Fallauswahl nach Reload, Serverfrist, Fehler-/Follow-up- und Profilpfade sowie Tastatur-, EN-, 390-Pixel-, Reduced-Motion-, CSP- und unerwartete JavaScript-Fehlerprüfungen. Der Runner erzeugt keine gemockten API-Antworten, sendet keine Modellprovideranfragen, verkürzt keine echte Serverfrist und bricht externe Browseranfragen ab.

Jeder Bericht enthält Laufphase, Browser-/Serverumgebung, echte Case-/Job-/Exchange-/Clip-IDs, HTTP-Methoden und Pfade, SHA-256 der empfangenen Clipbytes, Playerzustand, Assertions und Screenshots. Ein Fall ohne echte serverseitige Bewertung bleibt ein Fehler; Testprofile werden explizit pro Job gewählt und niemals aus dem Ereignisnamen abgeleitet.

## Medienzeit und Browserbelege

Dateivideo-Folgebeobachtung sampelt die Medienzeit, nicht die nächste Serie nativer Frames. Die lokale Implementierung benötigt eine positive, verlässliche FPS-Angabe und ein Backend, das die angeforderte Frameposition tatsächlich bestätigt. Unbekannte FPS oder unzuverlässiges Seek-Verhalten liefern einen strukturierten Medienfehler. Die synthetische AVI-Demo verwendet konstante Framerate; Videos mit variabler Framerate und reale Kameras sind nicht abgenommen.

Die unabhängige lokale Headless-Edge-Prüfung am 2026-10-02 bestand acht ältere Analyse-/CLI-Fälle einschließlich Upload, Neuaufnahme, Video-Folgebeobachtung, Freigabe, Abbruch, defekter Medien, Mehrtabbetrieb und 390-Pixel-Ansicht. Sie ist kein Nachweis für den Vier-Räume-Controller und ersetzt dessen neue Browserabnahme nicht. Echte Kamera-Hardware und Wettbewerbsverhalten wurden nicht geprüft.

## Ausführungsregeln des Kerns

Zusammenlaufende gewählte Workflowzweige werden in topologischer Reihenfolge zusammengeführt; jeder Knoten läuft genau einmal. Mehrere Bildwerkzeuge verwenden dasselbe geladene Bild, wobei jeder Analyse-Knoten weiterhin das Frame-Verarbeitungsbudget belastet. Eine reine Texteingabe bewahrt Mediencursor und Werkzeugzustand; eine ausdrücklich gewählte Ersatzquelle startet einen neuen Analysezustand. Quellen bleiben während einer Eingabepause offen und werden bei Abschluss oder Abbruch geschlossen.

Bei Datei-Videos begrenzt die native Framerate die tatsächliche Abtastung; die konfigurierte `sample_fps` ist eine Obergrenze. Die Vorprüfung des Framebudgets bleibt konservativ anhand der angeforderten Rate. ROI-Findings bewahren die Region in Originalpixeln; `metrics.coordinate_system` kennzeichnet die Pixelkoordinaten des Bildausschnitts und der darin gemessenen Bewegungsregionen. Berichte und Nachrichtenentwürfe übernehmen nur die Metadaten früherer gerenderter Ausgaben, keine verschachtelten Berichtsinhalte.
