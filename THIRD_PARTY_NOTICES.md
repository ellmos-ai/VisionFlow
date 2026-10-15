# Third-party components and artwork provenance

VisionFlow's original source is released under the [MIT License](LICENSE), selected by
the project owner on 15 October 2026. This does not replace upstream component licenses.
The VisionFlow wheel does not vendor the dependency or executable distributions below.

| Component | Use | License information |
|---|---|---|
| NumPy 2.5.3 | Runtime arrays and numerical operations | Installed distribution metadata declares `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`; retain that distribution's full notices. |
| opencv-python 5.0.0.93 | Runtime computer vision and media decoding | Distribution metadata declares Apache 2.0. Its `cv2/LICENSE.txt` and `cv2/LICENSE-3RD-PARTY.txt` distinguish OpenCV from bundled third-party components. |
| FFmpeg with libx264 | Separately installed road-clip encoder | Licensing depends on the executable build. The Windows acceptance executable reports `--enable-gpl --enable-version3 --enable-libx264`; it is not a VisionFlow MIT component or bundled in our wheel. Consult the executable's `ffmpeg -L` output and [FFmpeg's licensing information](https://www.ffmpeg.org/legal.html). |
| pytest 9.1.1 | Development tests | MIT, according to installed distribution metadata. |
| Playwright 1.60.0 | Optional browser acceptance | Apache-2.0, according to installed distribution metadata. Browser executables are installed separately and retain their own licenses. |

The eight interface illustrations were generated for this project with OpenAI image
generation on 14 October 2026. Their origin, fictional subjects and separation from
analysis/evidence inputs are recorded in [ASSET_PROVENANCE.md](docs/media/ASSET_PROVENANCE.md).
This provenance record is separate from the upstream software notices above. No third-party
photographs or brand assets are represented as original VisionFlow source.

Synthetic road images and demonstration clips are rendered by VisionFlow's RoadWorld;
they are not recordings of real road users. User-supplied local media is not part of
the distributed project and does not acquire a new license through use of the application.
