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

Die gepinnte Lockdatei umfasst Laufzeitpakete und die Testwerkzeuge. Prüfe die Installation mit:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m visionflow.cli --help
```

Die editierbare Installation registriert zusätzlich `.\\.venv\\Scripts\\visionflow.exe` und bleibt an diesen Quellcheckout gebunden. `PYTHONPATH` ist damit für die CLI nicht erforderlich; die gezeigte Einstellung erlaubt auch einen direkten Quellimport. Die CLI schreibt auch ohne `PYTHONIOENCODING` UTF-8.

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

## Grenzen

Der MVP enthält keine echte Modell- oder Provideranbindung, keine AWS-Ausführung und keine Wettbewerbsbewertung. Der Fixture-Modus nutzt festgelegte lokale Entscheidungen und macht keine Netzaufrufe. Die künstlichen Beispiele ersetzen weder einen realen Kamera-/Browser-Hardwaretest noch Datensatz-, Baseline- oder Wettbewerbs-Evaluationen. Die lokale Browser-Abnahme am 2026-10-02 bestand sieben Fälle in Headless Edge: separate CLI-Prozesse, Unicode-Uploads und Neuaufnahme, Video-Folgebeobachtung mit Entwurfsfreigabe, Abbruch, defekte Medien und eine 390-Pixel-Ansicht ohne horizontalen Überlauf. Browserkonsole und externe Seitenanfragen blieben leer. Echte Kamera-Hardware und ein Mobilgerät wurden nicht geprüft.

Weitere Test- und Prüfschritte stehen in [docs/TESTING.md](docs/TESTING.md).
