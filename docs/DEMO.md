# TruthLens demo script (about 5 minutes)

**Setup before judging.**

- Start the app with `run.bat` (Windows) or `./run.sh`, then open http://localhost:8000.
- Check that the left-rail status says the engine is available.
- It works offline, and no API key is needed.
- Every step below uses bundled files, opened from **Sample cases** in the top bar.
- The expected values were re-measured on 20 Nov from a clean export of commit `54aa97f`, with `c2pa-python` installed. Without that library the C2PA check cannot validate manifests, so the first sample will not show "credentials intact".
- Keep one downloaded PDF per sample case on the desktop in case the laptop misbehaves.
- If you registered test files earlier, reset the registry before judging, with `curl -X POST localhost:8000/api/registry/reset`. Never reset during the demo: it removes the registered originals the samples rely on.

## 1. The problem (20 s)
"Most deepfake tools give you one number: fake or real. Newsrooms and investigators need to know where a file came from
and what exactly changed. TruthLens doesn't just detect the fake, it investigates its origin."

## 2. Verified original (40 s) — Sample cases: *verified original*
- Expect **0.11, low**. The SHA-256 is an exact match to registered original **R003**.
- C2PA reads "Content credentials intact (signer not on trust list)". The manifest signature and content hash validate, so the file is unchanged since signing. The signer is a throw-away demo certificate, so signer identity is **not** trusted.
- The footer shows the SHA-256, "Exact match · R003" and the custody state.
- "The hash proves it's the same file, not that the event is real. C2PA proves it wasn't changed after signing, not who signed it."

## 3. Edited copy of a registered photo (60 s) — Sample cases: *edited copy of a registered photo*
- Expect **0.74, high**. The perceptual hash finds the original even though the SHA-256 differs.
- **Visual** tab: the registered original, the uploaded copy and the measured difference, with boxes on the changed regions.
  Click a box to open its finding.
- "Every finding has a location, an explanation, and innocent alternatives."

## 4. Full multimodal case (75 s) — Sample cases: *viral rally post (photo + clip + caption)*
- Expect **0.95, high**. One investigation holds a photo, a video, its audio track and a caption.
- **Cross-modal** tab:
  - claimed date vs the photo and video metadata;
  - "Unedited / real" claim vs the detected edits;
  - "Live / continuous" claim vs the hard cuts;
  - the audio anomaly lining up with the video cut at 4.00 s.
- **Audio** tab: the spectrogram and the flagged segment.
- **Text** tab: the extracted claims and the urgency framing.

## 5. Trust and report (45 s)
- **Overview** tab, then *Full case details*, then **Re-verify the whole custody log now**. It shows "Verified: N entries".
  "If someone edits a log entry or deletes one from the middle, verification fails at that point." (Tested. Deleting the newest entry is **not** detected, so don't claim that; see [QA.md](QA.md).)
- Footer: **Preview**, then **Generate PDF**. The PDF holds the same findings, the correlations, what the report cannot establish, and the custody log with entry hashes.

## 6. Close (20 s)
"Provenance first, forensics second, and honest about the limits." Show the slide of what is implemented and tested vs what is planned.

## Optional: AI images (only if asked) — Sample cases: *unregistered synthetic-looking portrait*
- Expect **0.39, medium**. Not registered, no metadata and no C2PA are each shown as information with no weight.
- Say what it is: a **constructed** test image with up-sampling artefacts, not real generator output.
  Real AI images were not reliably detected in our tests (0 of 16; see [QA.md](QA.md)). A trained, benchmarked detector is planned work.

## Avoid live (each gave a wrong or weak result in testing; see [QA.md](QA.md))
- Uploading a real AI-generated image to "catch the fake".
- Resizing, cropping or WhatsApp-forwarding a registered photo and re-uploading it.
- Making your own jump cut or audio splice on the spot.
- The People panel on photos of flowers or textures.
- Non-English captions.

## Honest answers for judge Q&A
- **Accuracy?** We do not claim a single figure. The measured results, including the misses, are in [QA.md](QA.md).
  Matching against registered originals is the reliable part. Blind detection is weak.
- **Is the score a probability?** No. It is a heuristic indicator built from the listed signals, capped at 0.95 and shown with a range.
- **Can it trace any image on the internet?** No. The registry only knows originals registered in TruthLens.
- **Which AI generator made it?** We don't attribute generators. The frequency analysis is experimental and never a verdict on its own.
- **Is missing C2PA or EXIF suspicious?** No. Most real images have neither, so we show them as neutral information.
- **Audio deepfakes?** We use acoustic heuristics (noise floor, pitch steadiness), not a trained voice-clone classifier.
- **What is experimental?** The People panel (YuNet face detector, never scored) and the frequency-based AI check.
