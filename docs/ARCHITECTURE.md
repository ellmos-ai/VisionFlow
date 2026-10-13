# VisionFlow architecture

## Components

```mermaid
flowchart LR
    UI[Browser interface] --> API[HTTP API<br/>loopback bind, session + CSRF]
    CLI[CLI client] --> API
    PY[Python API] --> CORE
    API --> CORE[Bounded workflow controller<br/>frame, time and model-call budgets]
    CORE --> CV[OpenCV 5 tool registry<br/>quality · motion · direction]
    CV --> MEDIA[Image · video · approved camera · road simulation]
    CORE --> REC[Recorder<br/>H.264 clips + SHA-256]
    CORE --> MODEL[Model router<br/>offline profiles · Gemini video opt-in]
    MODEL --> POLICY[Decision policy<br/>dismiss · escalate · human deadline]
    POLICY --> INBOX[Simulated authority inbox<br/>reports and linked all-clears]
    CORE --> EVIDENCE[Traces · evidence frames · reports]
```

## Agent workflow: perception, decision, action

```mermaid
flowchart TD
    F[Next frame] --> D[OpenCV direction tool<br/>patch tracks vs. lane vector]
    D -->|no opposing track| F
    D -->|opposing track, 3 real frames| STOP[Observation stops<br/>candidate branch selected]
    STOP --> CLIP[Clip 5 s before + 10 s after<br/>hash bound to the case]
    CLIP --> ASK[Model request with the same clip<br/>max 2 calls per case]
    ASK -->|hazard = false| DIS[Dismissed]
    ASK -->|vehicle, hazard, confidence ≥ 0.90,<br/>OpenCV confirmed, clip valid| REP[First report]
    ASK -->|unsure, error, timeout| HUM[Human review, 20 s deadline]
    HUM -->|confirm| REP
    HUM -->|reject| DIS
    HUM -->|deadline expires| REP
    REP -->|later rejection| CORR[Linked all-clear]
```

The visual result decides every branch: no opposing track keeps the observation going; an
opposing track stops it, cuts the clip and triggers the model request; the model answer and the
human decision select the action. A model answer can never add a media source or an output
destination, and every transition is written to the trace.

## Deployment on AWS

```mermaid
flowchart LR
    J[Judge's browser] -->|HTTPS| CF[Amazon CloudFront<br/>no caching, secret origin header]
    CF -->|HTTP, CloudFront prefix list only| NG[nginx on EC2 t4g.small<br/>AWS Graviton arm64, Ubuntu 24.04]
    NG -->|maps Host/Origin to loopback,<br/>rate-limits sessions and uploads| VF[visionflow serve --public-demo<br/>127.0.0.1:8765, systemd]
    S3[(Private S3<br/>source ZIP)] -->|instance role, read only| NG
    SRC[Committed HEAD] -->|cloud/acceptance.py package| ZIP[Allowlisted source ZIP<br/>+ per-file SHA-256 manifest]
    ZIP --> S3
    ZIP --> S3B[(Private S3)]
    S3B --> CB[AWS CodeBuild<br/>full pytest suite + both OpenCV demos]
    CB --> EV[Evidence ZIP + receipt<br/>hash-checked readback]
```

* **Public demo** (`cloud/demo/demo-stack.yaml`): one Graviton instance runs the unchanged
  server. It still binds to loopback; nginx forwards only requests that carry CloudFront's
  secret header and rewrites Host/Origin to the loopback values the server expects. In
  `--public-demo` mode workflows can only read the road simulation and media uploaded to this
  server, uploads share a 200 MB quota, at most four live road sessions run at once, and systemd
  restarts the service with an empty data directory every 24 hours. No SSH; the instance is
  managed through SSM. Provider calls and cameras are disabled in this mode.
* **Cloud acceptance** (`cloud/template.json`, `cloud/buildspec.yml`): CodeBuild installs the
  pinned dependencies from an exact source ZIP, runs the full test suite and both OpenCV demos,
  and returns hashed evidence ([procedure](AWS_ACCEPTANCE.md)).

## Direction analysis

Bounded native-resolution grayscale patch matching: corners with eight-pixel spacing (at most
240 per lane), 21×21 patches searched within ±80 px, NCC ≥ 0.80 with a 0.05 gap to any distinct
second peak, reverse match within 1.5 px and local corner support at the end point. Tracks need
three distinct frames, at most 0.75 s and 80 px between observations. Matches run on a small
thread pool (OpenCV releases the GIL); each match is independent, so results do not depend on
the pool. The detector only sees pixels; scenario labels never enter it, and its candidates are
opposing motion, not vehicle classifications.

The road scene is synthetic and deterministic per seed. Static scenery is rendered once and
kept smooth so that it does not compete with moving vehicles for the per-lane corner budget.
