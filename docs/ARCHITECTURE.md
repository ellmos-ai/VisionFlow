# VisionFlow architecture

```mermaid
flowchart LR
    UI[Local browser] --> API[Loopback HTTP API]
    CLI[CLI server commands] --> API
    LOCAL[CLI offline demo and validation] --> CORE
    PY[Python API] --> CORE[Bounded workflow controller]
    API --> CORE
    CORE --> CV[OpenCV 5 tool registry]
    CV --> MEDIA[Image, video, approved camera]
    CORE --> MODEL[Fixture or explicitly enabled provider]
    MODEL --> REQUEST[Validated follow-up request]
    REQUEST --> CORE
    CORE --> EVIDENCE[Frames, traces, reports, local drafts]
    CORE --> REVIEW[Human decision bound to content hash]
    COMMIT[Reviewed source commit] --> ZIP[Allowlisted source ZIP plus manifest]
    ZIP --> S3[Private S3 transport]
    S3 --> BUILD[AWS CodeBuild: full tests and OpenCV demos]
    BUILD --> ARTIFACT[Private evidence ZIP plus receipt]
    ARTIFACT --> VERIFY[Hashes plus independent AWS build readback]
```

The optional AWS branch is prepared infrastructure, not proof of an executed cloud run. It executes the existing controller and OpenCV tools without changing their contracts. CodeBuild supplies compute, Python and the pinned dependencies; S3 transports the exact committed input and generated evidence. No public HTTP endpoint is created. Provider calls require a separate explicit opt-in; the AWS acceptance recipe uses fixtures exclusively.

The road demonstrator uses synthetic traffic, pixel motion direction analysis, bounded recording, immutable clip hashes and human decisions. Notifications go to a simulated inbox. No real traffic-warning authority or external delivery is represented.
