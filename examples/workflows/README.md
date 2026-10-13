# Example workflows

Each file is a complete, validated VisionFlow workflow. They share one controller and one tool
registry (`quality`, `motion`, `direction`); only the configuration differs.

| File | Input | Flow |
|---|---|---|
| `document-quality.json` | image | measure sharpness → request a replacement photo → measure again → report |
| `motion-observation.json` | video | measure motion → ask the model → bounded follow-up observation → report |
| `direction-review.json` | video | direction analysis → observe until opposing motion → candidate branch → report |
| `road-monitor.json` | camera | the same direction flow on a live camera source |
| `road-review.json` | camera | direction → observe → evidence clip → model review → human decision with deadline |

`USER_PROVIDED` and `ROAD_SOURCE_PLACEHOLDER` are placeholders. Replace them in a copy with an
image or video path, an approved camera name (`serve --camera NAME=INDEX`) or a road simulation
source id, then validate:

```bash
python -m visionflow.cli validate examples/workflows/document-quality.json
```

The road review workflow is started by the server for each road case (`road case start`). No
example sends a real notification; notifications are local drafts or go to the simulated inbox.
