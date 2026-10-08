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
$stackReadback = aws cloudformation describe-stacks --stack-name $stack --region $region --output json | ConvertFrom-Json
$outputs = $stackReadback.Stacks[0].Outputs
$bucket = ($outputs | Where-Object OutputKey -eq 'BucketName').OutputValue
$project = ($outputs | Where-Object OutputKey -eq 'ProjectName').OutputValue
$checksum = [Convert]::ToBase64String([byte[]]($sha -split '(..)' | Where-Object { $_ } | ForEach-Object { [Convert]::ToByte($_,16) }))
$upload = aws s3api put-object --bucket $bucket --key "sources/$sha.zip" --body $bundle.archive --server-side-encryption AES256 --checksum-algorithm SHA256 --checksum-sha256 $checksum --region $region --output json | ConvertFrom-Json
if (-not $upload.VersionId -or $upload.VersionId -eq 'null') { throw 'Versionierte S3-Quelle erforderlich' }
$sourceHead = aws s3api head-object --bucket $bucket --key "sources/$sha.zip" --version-id $upload.VersionId --checksum-mode ENABLED --region $region --output json | ConvertFrom-Json
aws s3api get-object --bucket $bucket --key "sources/$sha.zip" --version-id $upload.VersionId --checksum-mode ENABLED --region $region .local/aws-bundle-01/aws-source.zip
if ((Get-FileHash .local/aws-bundle-01/aws-source.zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha) { throw 'AWS-Quellbytes stimmen nicht mit dem ZIP überein' }
$build = aws codebuild start-build --project-name $project --source-version $upload.VersionId --region $region --output json | ConvertFrom-Json
$context = @{stack=$stackReadback; launch=$build; upload=$upload; source_head=$sourceHead}
$context | ConvertTo-Json -Depth 50 | Set-Content .local/aws-bundle-01/launch-context.json -Encoding utf8
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
python cloud/acceptance.py verify --evidence .local/aws-evidence-01 --evidence-zip .local/aws-bundle-01/evidence.zip --cloud-readback .local/aws-bundle-01/aws-build-readback.json --source-zip .local/aws-bundle-01/aws-source.zip --launch-context .local/aws-bundle-01/launch-context.json
```

Erwartet: `integrity_verified: true`, `cloud_verified: true`, `model_mode: fixture`. Für eine Cloudprüfung sind alle fünf Eingaben zwingend: entpackter Belegordner, originales Beleg-ZIP, terminaler Build-Readback, zurückgelesenes versioniertes Quell-ZIP und der unabhängig gesicherte Launch-Kontext aus Stack-/Start-/Upload-/Head-Antworten. Fehlende Eingaben stoppen die Prüfung. Ein Aufruf ohne AWS-Readback liefert `cloud_verified: false` und prüft nur lokale Integrität und Demo-Verträge.

Der SHA-256 des Original-Beleg-ZIP muss exakt `artifacts.sha256sum` entsprechen. Jede entpackte Datei einschließlich Manifest und die gesamte Dateimenge müssen bytegleich zu diesem ZIP sein. Für die Quelle müssen AWS-S3-FULL_OBJECT-Checksumme, lokale ZIP-Bytes und die fest gepinnte VersionId zusammenpassen; die deklarative Hash-Umgebungsvariable allein ist kein Nachweis der heruntergeladenen Quelle. Stack-Konto/Region, Projekt, Build-ID/ARN, exakte S3-Location, `cloud/buildspec.yml`, erfolgreicher terminaler Status, VersionId, Buildimage und festes Artefaktziel müssen zusammenpassen. Der Kontext und Readback müssen direkt aus dem freigegebenen AWS-Konto stammen; JSON-Dateien und Hashes sind keine unabhängige digitale Signatur.

Der Prüfer verlangt beide abgeschlossenen Szenarien, die Neuaufnahme mit höherem endlichem Schärfemaß, tatsächliche Observe/Report-Folge und fortlaufende Frame-IDs. Alle Medien-, Trace- und Frame-Referenzen sind relative Pfade innerhalb des Hashmanifests. Framebelege haben im Kern die Endung `.bin`; geprüft werden echte PNG-Bytes und deren Dekodierung, nicht die Dateiendung.

Vor öffentlicher Verwendung AWS-Konto-ID, ARN, Bucket-/Projekt-/Lognamen und absolute Laufpfade entfernen. Den unsanitisierten Nachweis privat behalten. Für öffentliche Claims reichen Commit, OpenCV-Version, Szenarioergebnisse, relative Belegpfade und Hashes plus ausdrücklich benannte Grenzen. Ein künstlicher Testdatensatz ist keine reale Verkehrs- oder Dokument-Evaluation.

## Cleanup und Readback

Erst lokale ZIPs/Hashes/Readback sichern und prüfen; dann ausschließlich den gerade angelegten Bucket mit allen Objektversionen und Löschmarkierungen leeren. Der Bucket ist versioniert, um die Quellversion beim Build festzuhalten. Ein einfaches `s3 rm` würde nur Löschmarkierungen anlegen. Keine Wildcards auf Kontobasis verwenden.

```powershell
# Werte nochmals anhand dieses Stacks prüfen; alle Builds müssen terminal sein.
$versions = aws s3api list-object-versions --bucket $bucket --region $region --output json | ConvertFrom-Json
$objects = @(@($versions.Versions) + @($versions.DeleteMarkers) | Where-Object { $_ } | ForEach-Object { @{Key=$_.Key; VersionId=$_.VersionId} })
if ($objects.Count -gt 1000) { throw 'Unerwartet viele Versionen; Cleanup prüfen, nicht pauschal löschen' }
if ($objects.Count) {
    $deleteJson = @{Objects=$objects; Quiet=$true} | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText((Join-Path (Get-Location) '.local/aws-bundle-01/delete-own-versions.json'), $deleteJson, [Text.UTF8Encoding]::new($false))
    aws s3api delete-objects --bucket $bucket --delete file://.local/aws-bundle-01/delete-own-versions.json --region $region
}
aws s3api list-object-versions --bucket $bucket --region $region
aws cloudformation delete-stack --stack-name $stack --region $region
aws cloudformation wait stack-delete-complete --stack-name $stack --region $region
aws codebuild batch-get-projects --names $project --region $region
aws s3api head-bucket --bucket $bucket --region $region
```

Erwartet: Projekt unter `projectsNotFound`; Bucket nicht mehr vorhanden (404/NoSuchBucket, nicht bloß AccessDenied). Stacklöschung ist erst nach bestätigtem `stack-delete-complete` belegt. Bei Timeout denselben Stack abfragen. S3-Lifecycle und Logretention von einem Tag sind zusätzliche Rückfallregeln; Lifecycle löscht verzögert und ersetzt keine bestätigte Stackbereinigung. IAM, Loggruppe und Projekt werden durch Stacklöschung entfernt. AWS-Buildhistorie kann als Servicemetadatum verbleiben.

## Jury-Reproduktion und Zugangsgrenze

Die Jury kann das Paket in ihrem AWS-Konto mit demselben Commit und synthetischen Medien reproduzieren oder die local CLI-Demo aus [TESTING.md](TESTING.md) ausführen. Die AWS-Komponente verarbeitet tatsächlich die OpenCV-Workflows; S3 ist nur Transport und Belegspeicher. CodeBuild ist ein Batch-Ausführungsort und kein öffentlicher Web-Demozugang. Der aktuelle Server bindet an Loopback. Ein bestätigter Jury-Endpunkt oder eine mit den Veranstaltern vereinbarte Screenshare-Demo ist separat erforderlich; dieses Paket erfüllt diese Anforderung noch nicht. Echte agentische Modellrückkopplung, Evaluation/Baseline, Video, finaler Bericht/ZIP und persönliche GUI-Abnahme müssen an ihren jeweiligen Nachweisen abgenommen werden.

Technische Grundlage: [CodeBuild-Runtime-Matrix](https://docs.aws.amazon.com/codebuild/latest/userguide/available-runtimes.html), [Projektkonfiguration](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-codebuild-project.html), [Build-Umgebungsvariablen](https://docs.aws.amazon.com/codebuild/latest/userguide/build-env-ref-env-vars.html). Stand der Vorbereitung: 2026-10-08.
