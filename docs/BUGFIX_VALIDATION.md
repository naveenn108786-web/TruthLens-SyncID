# Video and image bug fixes - verified scope

## Original reported inputs are still missing

Recursive searches including hidden files under `C:/Users/N Naveen/Downloads/TruthLens-source-a626863/`
found neither **Fault.pdf** nor **problematic_video.mp4**. The pasted task text was available and read.
The exact reported video failure and deer-image observations have not been reproduced on the user's original files.
The fixes below address independently reproduced faults and measured regression cases; they do not establish that the missing video is fixed.

## Confirmed causes and fixes

1. **Uncaught FFprobe process failures:** Before the fix, injecting an unavailable FFprobe executable at the real upload-validation
   subprocess call raised `FileNotFoundError` and returned HTTP 500 / `Internal Server Error`. Timeouts could escape through the same path.
   These now become explicit HTTP 503 (tool unavailable) or 422 (timeout), with actionable JSON detail and server log messages.
   Probe temporary files are closed before subprocess execution on Windows and removed on both success and failure.
2. **Metadata-only video validation:** A video stream declaration did not establish decoder availability. Validation now also asks FFmpeg
   to decode the first video frame. Corrupt/unsupported streams are rejected with a meaningful 415 response; accepted originals remain unchanged.
3. **Player removed on codec error:** The old inline handler replaced the entire `<video>` element with text. The player is now retained,
   with a visible error, original download, and an optional separately generated H.264/AAC compatible preview. The derivative never overwrites evidence.
4. **Small preview and MIME mismatch:** Video CSS imposed very small viewport-height limits. Playback now uses a responsive contain layout
   and a larger viewport allowance. Original video resolution is retained. Evidence MIME is derived from detected content where available,
   so an MP4 renamed `.jpg` is served as video. Inline delivery and byte ranges support browser seeking.
5. **Audio failure discarded video findings:** Extraction/analysis was inside the video's outer exception block. An audio failure now marks
   analysis partial and keeps decoded video results, timestamps and original evidence. Failed checks produce an explicit incomplete-analysis warning.
6. **Sampling limits and incomplete frame records:** Sampling is configurable, frame-limit coverage uses actual FPS, timestamps are recorded,
   all retained sampled frames are represented, and adjacent flagged samples form explicitly labelled candidate review intervals.
   Spatial analysis copies are limited to 1280px to bound memory; originals/playback are not downscaled. Existing anomaly thresholds and weights are preserved.
7. **Overbroad AI metadata tokens:** Generic `c2pa`, `generated`, `flux` and `imagen` substring matches could trigger an AI metadata finding
   on ordinary workflows. An explicit tool/generation declaration is now required, and it is labelled editable/unverified. The existing strength
   for an actual explicit declaration is preserved. Missing metadata and low sensor noise do not establish AI origin.
8. **Reference retrieval and edit-direction ambiguity:** Local registry matching relies on pHash and can miss large changes. An explicit optional
   reference bypasses retrieval and measures localized RGB differences directly. Both files are preserved and hashed. The reference is comparison
   context, excluded from submitted-content scoring/correlation and aggregate AI assessment. It remains inspectable. Its unrelated metadata cannot
   cause the app to select it as the default suspect. No artificial score boost is assigned to measured differences.

## User workflow

- For video, upload normally and use the native player. Frame buttons/timeline seek to the analyzed timestamp.
- If the browser cannot decode the original, choose **Use compatible preview (derived)**. **Download original video** remains available.
- To compare photographs, upload the submitted image normally and choose a file in **OPTIONAL ORIGINAL / REFERENCE IMAGE**.
  Use **Reference difference map** or open **Visual → Supplied reference comparison**. The measured changed regions and explanations are separate
  from AI-generation assessment and from the legacy forensic indicator. Crops/unmatched aspect ratios are reported as not comparable.

Server configuration:

- `TRUTHLENS_FFMPEG` / `TRUTHLENS_FFPROBE`: optional full executable paths. If PATH is unavailable, launch with Python/Uvicorn directly;
  the pre-existing Windows launcher still checks PATH.
- `TRUTHLENS_VIDEO_SAMPLE_INTERVAL`: requested seconds between spatial samples, 0.1-60; default 0.5. Long clips can use a wider interval
  to stay within 40 retained spatial samples and the 9000-frame decoding limit.
- FFprobe/first-frame validation: 30-second timeout. Compatible preview conversion: 120 seconds. Audio extraction: 60 seconds.
- A 90-second loop budget limits ordinary video decoding work; it is not process isolation for a blocked native decoder call.

## Actual results

Complete suite: `.venv/Scripts/python.exe -m pytest -q tests syncid/tests`

**94 passed, 2 skipped, 1 warning** in the final run (28.87 seconds).
Both skips require absent `pdftotext`. A PyMuPDF compatibility import emitted a deprecation warning in an optional PDF test.
PDF generation/text checks still passed; a reference-comparison PDF was rendered and its first page inspected.

Focused runs included:

- 20 passed for initial video fixes plus upload validation.
- 13 passed for the expanded video regression suite before its final two cleanup/preview-timeout tests were added.
- 30 passed for reference comparison plus separate assessment tests.
- All final additions, including cleanup and preview-timeout cases, are included in the complete 94-pass run.

Coverage includes real locally encoded H.264 MP4, MPEG-4 MOV with audio, FFV1 MKV without audio, corrupt MP4 headers, explicit decoder-unavailable
fault injection, missing tools/timeouts, upload-limit rejection, temporary cleanup, range responses, preview conversion, preservation after analysis failure,
and configurable sample timestamps. Size rejection is tested with lowered limits; no claim is made of a full 200 MB stress test.

Browser observations:

- Original H.264 playback reported `videoWidth=1920`, `videoHeight=1080`, native controls, and ready state 4.
- Play and pause toggled correctly. Clicking the 1.00-second sampled frame sought to approximately 1.02 seconds and played.
- A real FFV1/MKV source triggered the browser codec error while retaining the video player. Its derived H.264 preview loaded at 640x360,
  ready state 4, and seeking to 1.00 seconds played successfully.
- The supplied-reference workflow selected the submitted image, displayed its measured difference map, and reported one region at
  `[49, 21, 28, 25]` for the controlled edit. The page explicitly did not attribute the change to AI. No browser console errors were captured in that check.
- Live Sync ID DemoAI registration/duplicate/original/modified verification still passed: modified bytes mismatched while the signed registration stayed valid.

Image tests include source-documented Nikon, Canon and Sony camera metadata; generic metadata false-positive controls; a genuine explicit AI-tool declaration;
missing metadata; and controlled added, removed, OpenCV-inpainted and normal edited regions. These controlled operations are **not** labelled as actual AI object edits.
The earlier source-labelled AI/camera evaluation and its misses/abstentions remain documented in [QA.md](QA.md).

## Remaining limitations

- The missing original video/Fault PDF and actual deer originals/AI edits still require reproduction. No claim is made that their exact issues are resolved.
- No trained/calibrated AI-image or AI-object detector was added. Blind localized forensics can miss convincing AI edits and can flag benign processing.
- Direct comparison identifies changed pixels, not an AI-added deer or any other semantic object. It assumes comparable scene layout and does not perform arbitrary perspective/crop registration.
- Model confidence and independently verified creator/capture time remain unestablished. Signed declarations, issuer trust and cryptographic file identity remain separate.
- Preview codec support depends on local FFmpeg and browser capabilities; long conversions can time out without altering originals.
- Existing local-demo authentication/deployment limitations remain unchanged.

## Modified implementation files

- `truthlens/app.py`
- `truthlens/media.py` (new)
- `truthlens/validation.py`
- `truthlens/pipeline.py`
- `truthlens/assessment.py`
- `truthlens/investigation.py`
- `truthlens/pdf.py`
- `truthlens/forensics/video.py`
- `truthlens/forensics/image.py`
- `truthlens/forensics/provenance.py`
- `truthlens/forensics/comparison.py` (new)
- `truthlens/static/index.html`
- `truthlens/static/app.js`
- `truthlens/static/style.css`
- `tests/test_video_regressions.py` (new)
- `tests/test_reference_comparison.py` (new)
- This validation document (new)

Sync ID's engine, UUID generation, SHA-256 implementation, Ed25519 implementation and independent database schema were not modified.
TruthLens's existing registry IDs and database schema were preserved. The optional reference upload and compatible-preview route are additive API features.
