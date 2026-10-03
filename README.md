# VisionFlow

VisionFlow führt lokale, begrenzte Bild- und Video-Workflows mit OpenCV aus. Derselbe Workflow lässt sich über die Python-API, die CLI und den lokalen Webserver verwenden. Der Server bindet ausschließlich an Loopback.

## Lokaler MVP-Stand

Die zwei Beispiele verwenden künstlich erzeugte Testmedien und den ausdrücklich markierten `fixture`-Modelladapter. Ein echter CLI-Demolauf mit OpenCV 5.0.0 ergab für das unscharfe synthetische Bild eine Laplace-Varianz von `4.557734375` und nach der Eingabe eines zweiten, schärferen Bildes `8000.528359375`. Das Video enthält 30 Frames mit 10 fps; nach den initialen Frames `0, 1` las die 2-fps-Folgebeobachtung die echten Medienframes `2, 7, 12` weiter. Trace-JSON und Framebelege lagen unter dem gewählten Demo-Ausgabeordner. Dieser Lauf nutzte kein Netzwerk.

Die beiden Schärfewerte kalibrieren nur diese künstlichen Bilder. Sie sind keine allgemeingültige Qualitätsgrenze und belegen weder OCR noch Dokumentvollständigkeit. Die Bewegungssequenz ist ebenfalls ein synthetischer Funktionstest, keine Evaluation an einem Datensatz.

## Installation aus dem Checkout

Voraussetzung ist Python 3.12 oder neuer. Die geprüfte Umgebung war Windows x64 mit Python 3.12.10, NumPy 2.5.3 und OpenCV-Python 5.0.0.93 (Laufzeitversion `cv2.__version__`: 5.0.0).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:PYTHONIOENCODING = "utf-8"
```

`requirements.lock` pinnt Laufzeitpakete und pytest. Playwright gehört nicht zur Core-Umgebung; dadurch bleibt die normale Testsammlung ohne Browserpaket und Browserprozess ausführbar. Prüfe die Installation mit:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m visionflow.cli --help
```

Die editierbare Installation registriert zusätzlich `.\.venv\Scripts\visionflow.exe` und bleibt an diesen Quellcheckout gebunden. `PYTHONPATH` ist damit für die CLI nicht erforderlich; die gezeigte Einstellung erlaubt auch einen direkten Quellimport. Die CLI schreibt auch ohne `PYTHONIOENCODING` UTF-8.

## Workflows und Offline-Demo

Die Workflowdateien liegen unter `examples/workflows/`. Ihre Eingabequelle `USER_PROVIDED` ist ein Platzhalter. Ersetze sie für einen normalen Serverlauf in einer Kopie der JSON-Datei durch einen erreichbaren Bild- oder Videopfad und validiere die Kopie:

```powershell
.\.venv\Scripts\python.exe -m visionflow.cli validate .\examples\workflows\document-quality.json
.\.venv\Scripts\python.exe -m visionflow.cli validate .\examples\workflows\motion-observation.json
```

Die reproduzierbare Demo erzeugt ihre eigenen Bilder und ihr eigenes Video. Sie braucht keinen Server und keinen externen Dienst. Wähle für jeden Lauf einen noch nicht existierenden Zielordner; vorhandene Ordner werden mit `FileExistsError` abgewiesen und nicht überschrieben.

```powershell
.\.venv\Scripts\python.exe -m visionflow.cli demo run --output-dir "$env:TEMP\visionflow-demo-01"
```

Die CLI gibt UTF-8-JSON mit Status, OpenCV-Version, Szenarioergebnissen, echten Frame-IDs, Messwerten sowie Trace- und Evidence-Pfaden aus. Die Traces liegen in `traces/`, die erzeugten Medien in `media/` und die echten Framebelege in `evidence/` unter dem Ausgabeordner.

## Lokaler Server, CLI und Weboberfläche

Starte den Server in einem PowerShell-Fenster. `--host` akzeptiert nur Loopback-Adressen; der Standard ist `127.0.0.1`. Workflow- und Evidence-Daten bleiben im lokalen Datenordner.

```powershell
.\.venv\Scripts\python.exe -m visionflow.cli serve --host 127.0.0.1 --port 8765 --data-dir .\.visionflow-data
```

Öffne anschließend `http://127.0.0.1:8765/` oder nutze in einem zweiten Fenster die CLI:

```powershell
.\.venv\Scripts\python.exe -m visionflow.cli tools --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli validate .\examples\workflows\document-quality.json
.\.venv\Scripts\python.exe -m visionflow.cli run .\examples\workflows\document-quality.json --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli status RUN_ID --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli cancel RUN_ID --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli confirm RUN_ID ACTION_ID CONTENT_HASH --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli reject RUN_ID ACTION_ID CONTENT_HASH --server http://127.0.0.1:8765
```

Ersetze `RUN_ID` durch die ID aus der JSON-Antwort von `run`. Wenn ein Lauf auf ein Bild wartet, kannst du es mit einem JSON-Objekt fortsetzen:

```powershell
.\.venv\Scripts\python.exe -m visionflow.cli input RUN_ID '{"source":"C:\\Users\\User\\Pictures\\aufnahme.png"}' --server http://127.0.0.1:8765
```

`confirm RUN_ID ACTION_ID CONTENT_HASH` bestätigt einen lokal gespeicherten Entwurf nach Prüfung seiner ID und seines Inhalts. Das bestätigt keine externe Aktion und versendet nichts. Mit `reject RUN_ID ACTION_ID CONTENT_HASH` wird ein Entwurf abgelehnt.

Die lokale HTTP-API stellt dieselben Funktionen bereit: `GET /tools`, `POST /validate`, `POST /runs`, `GET /runs/{run_id}`, `POST /runs/{run_id}/cancel`, `POST /runs/{run_id}/input`, `POST /runs/{run_id}/confirm` und `POST /runs/{run_id}/reject`. `POST /media` nimmt eine begrenzte Bild- oder Videodatei für einen Lauf entgegen. Der CLI-Client und die Weboberfläche übernehmen die lokale Sitzung und den CSRF-Schutz für schreibende Requests.

Kameraeingaben benötigen eine ausdrücklich freigegebene Zuordnung wie `--camera schreibtisch=0`. VisionFlow sucht nicht nach Kameras. Kamera-Hardware wurde in dieser lokalen Abnahme nicht geprüft.

## Straßenverkehrs-Demonstrator (Road Demo)

Der Straßenverkehrs-Demonstrator erweitert VisionFlow um eine interaktive Kameraquelle mit synthetischem 960×540-Verkehr, pixelbasierter Richtungserkennung (`direction`-Werkzeug), automatischer H.264-Kameraaufzeichnung und einem Arbeitsplatz mit menschlicher Überprüfung und simulierter Behörden-Inbox:

- **Ereignis-Injektion:** Normalverkehr, Falschfahrer (`wrong_way`), Vogel (`bird`) und Blatt (`leaf`).
- **OpenCV Direction-Analyse:** Verfolgt Merkmale über mehrere Frames und erkennt Gegenverkehr anhand des konfigurierten Spurvektors rein aus Pixelbewegungen.
- **Unveränderliche Clips:** Vorlauf (5 s) und Nachlauf (10 s) werden als deterministischer MP4-Clip exportiert (gleicher SHA-256-Hash für Modell und Mensch).
- **Arbeitsplatz:** Countdown-Timer (20 s), Gefahr bestätigen, verneinen oder automatische Eskalation bei hoher Modellsicherheit (>0,90). Späte Ablehnungen erzeugen eine verknüpfte Entwarnung im simulierten Postfach.

### CLI-Befehle für die Straßendemo

```powershell
# Sitzung erstellen und steuern
.\.venv\Scripts\python.exe -m visionflow.cli road create --seed 42 --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli road inject SOURCE_ID wrong_way --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli road recording SOURCE_ID --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli road clip CLIP_ID --output .\clip.mp4 --server http://127.0.0.1:8765

# Fallprüfung und Arbeitsplatz
.\.venv\Scripts\python.exe -m visionflow.cli road case start SOURCE_ID --mode fixture --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli road case status CASE_ID --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli road case decide CASE_ID confirm --version 1 --content-hash HASH --idempotency-key click-1 --server http://127.0.0.1:8765
.\.venv\Scripts\python.exe -m visionflow.cli road inbox --server http://127.0.0.1:8765
```

Für den Video-Export wird FFmpeg mit `libx264` vorausgesetzt. Fehlt FFmpeg, bleibt der Core-Workflow uneingeschränkt nutzbar; die Clip-Erzeugung wird mit dem Grund `ffmpeg_required` deaktiviert.

### Browserabnahme der Vier-Räume-Steuerung

Die echte GUI-Abnahme läuft getrennt vom Core in einer optionalen Browserumgebung. Sie verwendet System-Edge und eine eigene Loopback-Serverinstanz mit frischem Datenordner. Es gibt keine API-Mocks, Modellprovider-Aufrufe oder externen Requests. Der Server läuft mit `.venv`; Playwright läuft mit `.venv-browser`:

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

`--phase full` ergänzt mehrere echte Jobs/Fälle, Fallauswahl nach Reload, Serverfrist, Modellprofile und Bedienprüfungen. `--phase red` ist der gezielte Vorher-Beleg für den alten UI-Fehler und soll im reparierten Stand nicht als PASS gewertet werden. Jeder Lauf verlangt einen noch nicht vorhandenen Ausgabeordner mit passendem Phasenpräfix und schreibt Bericht sowie Screenshots unter `.local/acceptance/`. Details und Einzelbefehle stehen in [docs/TESTING.md](docs/TESTING.md).

## Grenzen

Der MVP enthält keine echte Modell- oder Provideranbindung, keine AWS-Ausführung und keine Wettbewerbsbewertung. Der Fixture-Modus nutzt festgelegte lokale Entscheidungen und macht keine Netzaufrufe. Die künstlichen Beispiele ersetzen weder einen realen Kamera-/Browser-Hardwaretest noch Datensatz-, Baseline- oder Wettbewerbs-Evaluationen. Die lokale Browserprüfung am 2026-10-02 deckte acht ältere Analyse-/CLI-Fälle ab, darunter Unicode-Uploads, Video-Folgebeobachtung und eine 390-Pixel-Ansicht. Sie belegt nicht die Vier-Räume-Steuerung mit echten Road-Cases, ModelExchanges, Clip-Bytes oder nativem Player. Echte Kamera-Hardware und ein Mobilgerät wurden nicht geprüft.

Weitere Test- und Prüfschritte stehen in [docs/TESTING.md](docs/TESTING.md).

## Ausführungsregeln des Kerns

Zusammenlaufende gewählte Workflowzweige werden in topologischer Reihenfolge zusammengeführt; jeder Knoten läuft genau einmal. Mehrere Bildwerkzeuge verwenden dasselbe geladene Bild, wobei jeder Analyse-Knoten weiterhin das Frame-Verarbeitungsbudget belastet. Eine reine Texteingabe bewahrt Mediencursor und Werkzeugzustand; eine ausdrücklich gewählte Ersatzquelle startet einen neuen Analysezustand. Quellen bleiben während einer Eingabepause offen und werden bei Abschluss oder Abbruch geschlossen.

Bei Datei-Videos begrenzt die native Framerate die tatsächliche Abtastung; die konfigurierte `sample_fps` ist eine Obergrenze. Die Vorprüfung des Framebudgets bleibt konservativ anhand der angeforderten Rate. ROI-Findings bewahren die Region in Originalpixeln; `metrics.coordinate_system` kennzeichnet die Pixelkoordinaten des Bildausschnitts und der darin gemessenen Bewegungsregionen. Berichte und Nachrichtenentwürfe übernehmen nur die Metadaten früherer gerenderter Ausgaben, keine verschachtelten Berichtsinhalte.
