# AWS execution and evidence

VisionFlow uses AWS in two ways:

1. **Public demo** – `cloud/demo/demo-stack.yaml` and `cloud/demo/deploy_demo.py` (see
   [ARCHITECTURE.md](ARCHITECTURE.md)): AWS Graviton EC2 + nginx + CloudFront, server in
   `--public-demo` mode.
2. **Cloud acceptance** – this document: one AWS CodeBuild run that executes the full test suite
   and both OpenCV demos from an exact, hashed source package and returns verifiable evidence.

## Verified run: 14 October 2026

Source `aeb214887f825e47d59f58deba748173d6ccbe2b`, source ZIP SHA-256
`aedd94d98a67c31fb223d789c024b19cf4cc0072b6194979429322114eb1f767`:
AWS CodeBuild **SUCCEEDED**, **615 passed, 1 skipped, 0 failed** in 119.75 s;
both offline OpenCV demos passed. The verifier returned exit 0 with
`integrity_verified: true` and `cloud_verified: true` after checking the exact source,
downloaded artifact and independent AWS metadata. The temporary stack was deleted;
`DELETE_COMPLETE` was independently read back.

The public EC2/CloudFront demo serves this source and passed a bird dismissal and
a wrong-way case with a real 60-second deadline, simulated report and linked late correction.
Downloaded clip bytes matched both the case and HTTP-header hashes.

[Publication-safe result summary](AWS_RESULTS_2026-10-14.json). The complete original
cloud evidence and private context are retained privately; this public summary is
not a digital signature and does not replace the full verification inputs below.
Earlier failed attempts remain in the project record and are not counted as passes.

## Scope and cost bound

Region `eu-central-1` (Frankfurt), **one** CodeBuild build on `BUILD_GENERAL1_SMALL` with
`aws/codebuild/standard:7.0` and Python 3.12, at most 20 minutes build time and 5 minutes queue
time, no automatic retries, at most one concurrent build. Operational budget: **at most 5 USD for
the attempt including S3 and logs**; no model calls. The bound is an operational approval limit,
not a technical billing cap – check current regional prices before starting
([CodeBuild pricing](https://aws.amazon.com/codebuild/pricing/)).

The stack creates a private, versioned S3 bucket, a CodeBuild project, a narrowly scoped service
role and a log group – no VPC/NAT, no EC2 instance, no public endpoint, no GitHub connection.
The source comes from a deliberately created private ZIP; no keys are read or embedded. The AWS
CLI must be authenticated by the account owner. Required permissions: CloudFormation, IAM
role/PassRole for this stack, CodeBuild start/stop/read, S3 on its bucket, log readback.
`CAPABILITY_IAM` approves the service role visible in the template.

## One-command run

```bash
python cloud/run_codebuild_acceptance.py --region eu-central-1 --stack visionflow-acceptance-YYYYMMDD
```

Choose a new stack name for each attempt. Results are saved under
`.local/aws-acceptance/<stack>/`. On a failed build, the runner downloads the original
artifact ZIP and extracts `cloud-evidence/` (including `pytest.log` when the test phase ran),
then saves every CloudWatch log page as `failure/cloudwatch-events.json` before deleting
the stack and all bucket versions. `failure/build.json` and `failure/capture.json` preserve
the build metadata and capture outcome.

If either evidence channel cannot be captured, cleanup is skipped and `result.json`
records `skipped: failure evidence incomplete`. Recover the missing evidence from the
recorded S3 location or CloudWatch stream, then empty the stack's versioned bucket and
delete the stack; retained AWS resources continue to incur storage/log costs. `--keep`
also skips automatic cleanup, while still capturing failed-build evidence.

The script performs exactly the steps below and writes everything to
`.local/aws-acceptance/<stack>/` (package, launch context, build readback, evidence ZIP,
`verify.txt`, `result.json`). It exits non-zero unless the build succeeded and verification
passed. `--keep` skips the cleanup.

## The steps

1. **Package.** `python cloud/acceptance.py package --repo . --output <dir>` archives only
   committed files from `HEAD` (never uncommitted work) through a path allowlist: code, tests,
   examples, cloud recipes, evaluation and public documents. Private planning, `.env`, key files,
   databases and work reports are excluded. The manifest holds the full commit and the SHA-256 of
   every file; it does not claim general secret detection – inspect the ZIP before upload.
2. **Deploy.** `aws cloudformation validate-template` and `deploy` of `cloud/template.json` with
   `SourceZipSha256=<sha>` and `CAPABILITY_IAM`; read the outputs `BucketName`, `ProjectName`.
3. **Upload and read back.** `aws s3api put-object` to `sources/<sha>.zip` with SSE-AES256 and a
   SHA-256 checksum; a missing `VersionId` aborts. `head-object` and `get-object` of exactly that
   version with checksum mode; the downloaded bytes must hash to `<sha>`.
4. **Build.** `aws codebuild start-build --source-version <VersionId>`; the stack, start, upload
   and head responses are saved as the launch context. The build installs the lock file, checks
   dependencies, runs the complete pytest suite and both real OpenCV demos (the document probe
   requests a replacement and measures it; the motion probe observes more frames at the running
   cursor) and archives traces, synthetic media, frame evidence, JUnit, the package list and a
   receipt. A fixture decides the next observation; there are no provider costs.
5. **Observe.** `aws codebuild batch-get-builds` for this build until it is terminal; no restart
   because of an observation timeout. A failed or stopped build is not cloud evidence; save the
   terminal readback and the CloudWatch events it references.
6. **Verify.** Download the evidence ZIP from the artifact location and run
   `python cloud/acceptance.py verify --evidence <dir> --evidence-zip <zip> --cloud-readback <readback.json> --source-zip <aws-source.zip> --launch-context <launch-context.json>`.
   Expected: `integrity_verified: true`, `cloud_verified: true`, `model_mode: fixture`.

All five verification inputs are required; missing inputs stop the check, and a run without AWS
readback yields `cloud_verified: false` (local integrity only). The SHA-256 of the original
evidence ZIP must equal `artifacts.sha256sum`; every extracted file including the manifest must be
byte-identical to it. For the source, the S3 FULL_OBJECT checksum, the local ZIP bytes and the
pinned VersionId must agree – the declared hash variable alone is no proof. Account/region,
project, build ID/ARN, exact S3 location, `cloud/buildspec.yml`, the terminal status, VersionId,
build image and artifact destination must match. Context and readback come directly from the
approved account; JSON files and hashes are not an independent digital signature.

The verifier requires both completed scenarios, the replacement with a higher finite sharpness,
the real observe/report order and continuing frame IDs. All media, trace and frame references are
relative paths inside the hash manifest. Core frame evidence uses `.bin` names; the check decodes
real PNG bytes rather than trusting extensions.

Before publishing, remove account ID, ARNs, bucket/project/log names and absolute paths; keep the
unsanitised evidence private. Public claims need only the commit, OpenCV version, scenario
results, relative evidence paths, hashes and the stated limits. A synthetic test set is not a
real traffic or document evaluation.

## Cleanup

Save and verify local ZIPs, hashes and readbacks first. Then empty only the bucket of this stack,
including all object versions and delete markers (the bucket is versioned to pin the source
version, so a plain `s3 rm` would only add delete markers), and delete the stack:

```bash
aws s3api list-object-versions --bucket <bucket>          # then delete-objects with every Key/VersionId
aws cloudformation delete-stack --stack-name <stack>
aws cloudformation wait stack-delete-complete --stack-name <stack>
aws codebuild batch-get-projects --names <project>        # expect projectsNotFound
aws s3api head-bucket --bucket <bucket>                   # expect 404 / NoSuchBucket
```

Stack deletion is proven only by `stack-delete-complete`. The S3 lifecycle rule and one-day log
retention are fallbacks, not a substitute. IAM, log group and project are removed with the
stack; CodeBuild build history can remain as service metadata.

## Reproduction by judges

Judges can reproduce the run in their own AWS account with the same commit and synthetic media,
or run the local CLI demo from [TESTING.md](TESTING.md). CodeBuild is a batch execution
environment; the interactive jury access is the public demo from `cloud/demo/`.

References: [CodeBuild runtimes](https://docs.aws.amazon.com/codebuild/latest/userguide/available-runtimes.html),
[project configuration](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-codebuild-project.html),
[build environment variables](https://docs.aws.amazon.com/codebuild/latest/userguide/build-env-ref-env-vars.html).
