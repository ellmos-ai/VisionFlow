# AWS-Ausführung und Nachweis

Dieses Paket ist vorbereitet, aber noch nicht auf AWS ausgeführt. Ein lokaler PASS ist kein Cloudnachweis. Persönliche GUI-Abnahme, echte Modellanbieter, reale Kameras und Datensatz-Evaluation bleiben eigene offene Prüfungen.

## Konkrete Freigabeaktion

Empfehlung: AWS-Region `eu-central-1` (Frankfurt), **ein** CodeBuild-Lauf auf `BUILD_GENERAL1_SMALL`, Ubuntu `aws/codebuild/standard:7.0`, Python 3.12, höchstens 20 Minuten Buildzeit und 5 Minuten Warteschlange. Keine automatischen Wiederholungen, höchstens ein gleichzeitiger Build. Vorgeschlagener Kostenrahmen: **maximal 5 USD für diesen einen Versuch einschließlich S3 und Logs**, keine Modelle, keine zweite Ausführung ohne erneute Prüfung. Der Rahmen ist eine operative Freigabegrenze, kein technisch garantierter AWS-Abrechnungsdeckel. Vor Start den aktuellen regionalen Preis und die Kontoverfügbarkeit prüfen; bei prognostizierten Gesamtkosten über 5 USD nicht starten. Die [AWS-Preisseite](https://aws.amazon.com/codebuild/pricing/) beschreibt zeitabhängige Compute-Abrechnung und zusätzliche Dienste.

Angelegt werden ein privater S3-Bucket, ein CodeBuild-Projekt, eine eng begrenzte Service-Rolle und eine Loggruppe. Kein VPC/NAT, keine EC2-Instanz, kein öffentlicher Endpunkt, keine GitHub-Verbindung. Der Quellcode kommt aus einem bewusst erzeugten, privaten ZIP; Schlüssel werden weder gelesen noch eingebettet. Das AWS-CLI muss zuvor durch den Nutzer in einem freigegebenen Konto authentifiziert sein. Benötigte Verwaltungsrechte: CloudFormation, IAM-Rolle/PassRole für genau diesen Stack, CodeBuild Start/Stop/Read, S3 für dessen Bucket und Log-Readback. `CAPABILITY_IAM` genehmigt die im Template sichtbare Service-Rolle.

## Lokale Vorbereitung

Root integriert und prüft diesen Paket-Commit zuerst. `package` arbeitet ausschließlich mit `HEAD`, niemals mit noch uncommittierten Dateien. Eine Pfad-Allowlist enthält Code, Tests, Beispiele, Cloud-Rezept und freigegebene Dokumente. Private Projektplanung, `.env`, Schlüsseldateien, Datenbanken und Arbeitsberichte gehören nicht ins ZIP.

```powershell
python cloud/acceptance.py package --repo . --output .local/aws-bundle-01
$bundle = Get-Content .local/aws-bundle-01/package.json -Raw | ConvertFrom-Json
$sha = $bundle.source_zip_sha256
$region = 'eu-central-1'
$stack = 'visionflow-acceptance-20261008' # eindeutiger Name; keinen fremden Stack benutzen
```

Das ZIP und `package.json` lokal behalten. Das Quellmanifest enthält den vollen Commit und SHA-256 jeder aufgenommenen Datei. Es behauptet keine allgemeine Geheimniserkennung; den finalen ZIP-Inhalt vor Upload prüfen. Das Paket verändert den wiederverwendbaren Kern nicht.

## Erst nach AWS-Kostenfreigabe ausführen

Alle folgenden Befehle sind externe Aktionen. Sie wurden im Vorbereitungslauf nicht ausgeführt.

```powershell
aws cloudformation validate-template --template-body file://cloud/template.json --region $region
aws cloudformation deploy --stack-name $stack --template-file cloud/template.json --parameter-overrides "SourceZipSha256=$sha" --capabilities CAPABILITY_IAM --region $region
$outputs = aws cloudformation describe-stacks --stack-name $stack --region $region --query 'Stacks[0].Outputs' --output json | ConvertFrom-Json
$bucket = ($outputs | Where-Object OutputKey -eq 'BucketName').OutputValue
$project = ($outputs | Where-Object OutputKey -eq 'ProjectName').OutputValue
aws s3 cp $bundle.archive "s3://$bucket/sources/$sha.zip" --sse AES256 --region $region
$build = aws codebuild start-build --project-name $project --region $region --output json | ConvertFrom-Json
$buildId = $build.build.id
$buildId | Set-Content .local/aws-bundle-01/build-id.txt -Encoding utf8
```

Mit `aws codebuild batch-get-builds --ids $buildId --region $region` genau diesen Lauf beobachten. Nicht aufgrund einer Beobachtungszeitüberschreitung neu starten. Nach 20 Minuten beendet CodeBuild den Build; bei Fehlern/Abbruch keine erfolgreiche Cloudabnahme behaupten. Bei bewusster vorzeitiger Beendigung: `aws codebuild stop-build --id $buildId --region $region` und terminalen Zustand abfragen. Keine fremden Projekte oder Builds verändern.

Der Build installiert die Lockdatei, prüft Abhängigkeiten, führt die vollständige Pytest-Suite aus und startet beide echten OpenCV-Demos. Die Dokumentprobe fordert eine Neuaufnahme an und misst das Ersatzbild. Die Bewegungsprobe beobachtet zusätzliche Frames am fortlaufenden Cursor. Traces, synthetische Medien, Framebelege, JUnit, Paketliste und Receipt werden gemeinsam archiviert. Eine Fixture entscheidet über die nächste Beobachtung; es gibt keine LLM-Providerkosten.

Bei Installations- oder Testfehlern kann ein vollständiges Belegarchiv fehlen. Vor Cleanup den terminalen AWS-Readback und die im Feld `logs` referenzierten CloudWatch-Ereignisse privat sichern. Einen fehlgeschlagenen Build nicht als Cloudnachweis verwenden.

## Nachweis herunterladen und verifizieren

```powershell
aws codebuild batch-get-builds --ids $buildId --region $region --output json | Set-Content .local/aws-bundle-01/aws-build-readback.json -Encoding utf8
$readback = Get-Content .local/aws-bundle-01/aws-build-readback.json -Raw | ConvertFrom-Json
if ($readback.builds[0].buildStatus -ne 'SUCCEEDED') { throw 'Kein erfolgreicher AWS-Lauf' }
$artifact = $readback.builds[0].artifacts.location -replace '^arn:aws:s3:::', 's3://'
aws s3 cp $artifact .local/aws-bundle-01/evidence.zip --region $region
Expand-Archive -LiteralPath .local/aws-bundle-01/evidence.zip -DestinationPath .local/aws-evidence-01
python cloud/acceptance.py verify --evidence .local/aws-evidence-01 --cloud-readback .local/aws-bundle-01/aws-build-readback.json --source-zip .local/aws-bundle-01/source.zip
```

Erwartet: `integrity_verified: true`, `cloud_verified: true`, `model_mode: fixture`. Ein Aufruf ohne AWS-Readback prüft nur Artefaktintegrität und liefert `cloud_verified: false`. Die ZIP-Prüfsumme muss zur Receipt und AWS-Umgebungsvariable passen, der Quellmanifestinhalt zum archivierten Commit. AWS-ARN, Build-ID, erfolgreicher terminaler Status, S3-Quelle und Buildimage müssen zusammenpassen. Der Readback muss direkt aus dem freigegebenen AWS-Konto stammen; JSON-Dateien und Hashes sind keine unabhängige digitale Signatur.

Vor öffentlicher Verwendung AWS-Konto-ID, ARN, Bucket-/Projekt-/Lognamen und absolute Laufpfade entfernen. Den unsanitisierten Nachweis privat behalten. Für öffentliche Claims reichen Commit, OpenCV-Version, Szenarioergebnisse, relative Belegpfade und Hashes plus ausdrücklich benannte Grenzen. Ein künstlicher Testdatensatz ist keine reale Verkehrs- oder Dokument-Evaluation.

## Cleanup und Readback

Erst lokale ZIPs/Hashes/Readback sichern und prüfen; dann ausschließlich den gerade angelegten Bucket leeren. Der Bucket ist unversioniert. Keine Wildcards auf Kontobasis verwenden.

```powershell
# Werte nochmals anhand dieses Stacks prüfen; alle Builds müssen terminal sein.
aws s3 rm "s3://$bucket/" --recursive --region $region
aws cloudformation delete-stack --stack-name $stack --region $region
aws cloudformation wait stack-delete-complete --stack-name $stack --region $region
aws codebuild batch-get-projects --names $project --region $region
aws s3api head-bucket --bucket $bucket --region $region
```

Erwartet: Projekt unter `projectsNotFound`; Bucket nicht mehr vorhanden (404/NoSuchBucket, nicht bloß AccessDenied). Stacklöschung ist erst nach bestätigtem `stack-delete-complete` belegt. Bei Timeout denselben Stack abfragen. S3-Lifecycle und Logretention von einem Tag sind zusätzliche Rückfallregeln; Lifecycle löscht verzögert und ersetzt keine bestätigte Stackbereinigung. IAM, Loggruppe und Projekt werden durch Stacklöschung entfernt. AWS-Buildhistorie kann als Servicemetadatum verbleiben.

## Jury-Reproduktion und Zugangsgrenze

Die Jury kann das Paket in ihrem AWS-Konto mit demselben Commit und synthetischen Medien reproduzieren oder die local CLI-Demo aus [TESTING.md](TESTING.md) ausführen. Die AWS-Komponente verarbeitet tatsächlich die OpenCV-Workflows; S3 ist nur Transport und Belegspeicher. CodeBuild ist ein Batch-Ausführungsort und kein öffentlicher Web-Demozugang. Der aktuelle Server bindet an Loopback. Ein bestätigter Jury-Endpunkt oder eine mit den Veranstaltern vereinbarte Screenshare-Demo ist separat erforderlich; dieses Paket erfüllt diese Anforderung noch nicht. Echte agentische Modellrückkopplung, Evaluation/Baseline, Video, finaler Bericht/ZIP und persönliche GUI-Abnahme müssen an ihren jeweiligen Nachweisen abgenommen werden.

Technische Grundlage: [CodeBuild-Runtime-Matrix](https://docs.aws.amazon.com/codebuild/latest/userguide/available-runtimes.html), [Projektkonfiguration](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-codebuild-project.html), [Build-Umgebungsvariablen](https://docs.aws.amazon.com/codebuild/latest/userguide/build-env-ref-env-vars.html). Stand der Vorbereitung: 2026-10-08.
