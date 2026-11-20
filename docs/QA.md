# TruthLens QA results and known limitations

## Separate assessment update - 20 Nov 2026

The new assessment layer separates manipulation, AI-generation evidence, content provenance, and explicit Sync ID verification.
It does not change existing forensic detection rules, finding weights, correlations, or manipulation scores. The legacy manipulation
indicator still includes its existing metadata/provenance contributions; it is not a pure AI detector or a camera-capture probability.

AI-generation statuses describe available evidence, not ground-truth classification:

- **Evidence of AI generation detected:** an intact C2PA credential declares AI-generated/AI-assisted content. This establishes
  the existence of a signed declaration, not independent truth or issuer trust.
- **Evidence suggests possible AI generation:** an editable explicit generation declaration, an unvalidated C2PA declaration,
  or an existing experimental synthetic-frequency finding is present.
- **No AI-generation evidence detected:** available metadata/intact credentials were examined without finding positive generation evidence.
  This does not prove camera capture.
- **Inconclusive:** analysis is incomplete or there is insufficient usable metadata/credential evidence and no positive generation cue.
- **Not assessed:** no still-image evidence was supplied, or an older saved report predates the assessment layer.

No calibrated AI-image detector is configured. No numerical AI confidence is emitted. The optional LLM claims reviewer is not an image detector.
Video-frame generation is not assessed by this still-image summary. Existing Sync ID verification remains explicit and separate from case scoring.
Old reports are not rewritten or re-hashed; their dashboard/PDF shows that the separate assessment was not performed.

### Actual validation results

Command: `.venv/Scripts/python.exe -m pytest -q tests syncid/tests`

**65 passed, 2 skipped** in 98.65 seconds. Both skips require unavailable `pdftotext`. PDF generation and assessment-text extraction
were separately checked with PyMuPDF, and the first three pages of a five-page report were rendered; the first two were visually inspected.
One optional PDF-test dependency emitted a `fitz` deprecation warning. The live Sync ID DemoAI HTTP demonstration passed through both services.
The browser showed a source-labelled AI example with manipulation indicator 0.00 and separate AI-generation status **Inconclusive**;
its methods/limitations disclosure and link to the existing Sync ID tool worked.
The existing local case with indicator 0.05 was also re-submitted through the live API: the indicator remained exactly **0.05**,
while the new AI-generation assessment was **Inconclusive**, with confidence unset. The original saved report was left intact.

Source-labelled evaluation: `.venv/Scripts/python.exe samples/eval_assessment.py --fetch`

| Group | Count | Actual AI-generation assessment |
|---|---:|---|
| Upstream Stable Diffusion examples | 3 | 3 inconclusive |
| Those AI examples with a controlled visible edit | 3 | 3 inconclusive |
| Those AI examples re-encoded without metadata | 3 | 3 inconclusive |
| Source-documented camera photographs | 3 | 3 no AI-generation evidence detected |
| Those photographs with a controlled visible edit | 3 | 3 inconclusive |
| Those photographs re-encoded without metadata | 3 | 3 inconclusive |
| AI examples with evaluation-added truthful generation declarations | 3 | 3 possible AI generation |
| Camera photo with an evaluation-added misleading generation declaration | 1 | 1 possible AI generation |

For the 18 cases excluding the deliberately added declaration controls: **0/9 AI-derived cases raised positive AI evidence**
(nine misses/abstentions), and **0/9 camera-derived cases raised positive AI evidence**. Fifteen cases abstained as inconclusive;
the remaining three were camera originals with no AI-generation evidence found. An inconclusive result is not a negative/camera verdict.
The misleading metadata control illustrates a false-positive origin inference if declarations were treated as proof; the assessment
instead explicitly reports only a possible, editable declaration. No reliable AI detection claim follows from these results.

This is a small convenience sample, **not a benchmark**: six source files and correlated variants. AI inputs are published text-to-image
example montages from [CompVis](https://github.com/CompVis/stable-diffusion#text-to-image-with-stable-diffusion), not independent samples
per variant. Camera labels rely on the [Exif sample documentation](https://github.com/ianare/exif-samples/blob/master/jpg/README);
the upstream photographs were resized in GIMP and do not have independently attested capture origins. Edited and stripped variants
also change encoding to RGB PNG, so this is not an isolated metadata-removal experiment. Local downloaded media remain in ignored
`samples/real/assessment-evaluation/`; URLs, SHA-256 hashes, per-case statuses and methods are recorded in `data/assessment-validation.json`.

Trained model integration, calibration, and a larger licensed, independently labelled benchmark remain future work. No trained model was
downloaded or used in this evaluation, and no new pixel detector was introduced.

## Historical submission baseline

Tested on 19–20 Nov 2026 at commit `54aa97f`, the submission baseline.

- **Automated tests:** `python -m pytest -q` reports 36 passed. This was re-run on 20 Nov from a clean export of `54aa97f`.
- **Manual QA:** 108 cases in two rounds, all sent through the same `/api/analyze` route the upload form uses.
  - **Round 1** (image and People): 60 single-image cases, all returned HTTP 200.
  - **Round 2** (provenance, C2PA, video, audio, text, upload validation): 48 cases. 42 returned 200. The other 6 were deliberately invalid uploads and were rejected as designed: 2 with 400, 4 with 415.
  - **No case** produced a server error (5xx).
- **Test conditions:** no API key, and a throw-away database for each round.
- **Thresholds:** none were adjusted to improve these results.
- **Test files:** none are committed. Public files came from GitHub-hosted sample sets: camera originals from metadata-extractor-images, model sample outputs from the Stable Diffusion, SDXL and StyleGAN repos, and C2PA fixtures from c2pa-rs.
- **Controlled edits:** the object, video and audio edits were made by us with OpenCV and ffmpeg. They are labelled as controlled edits, not as AI.

Scores are heuristic indicators (low below 0.35, medium from 0.35, high from 0.65), not probabilities.

## What works (measured)

| Capability | Result |
|---|---|
| Exact SHA-256 match to a registered original | Correct in every test |
| Locating a real content edit against a registered original | 2 of 2 found, boxed on the edited area, scored high (0.65, 0.66) |
| C2PA manifest integrity | 3 of 3 with the c2pa-python library installed. (1) Untouched signed fixture: manifest signature and content hash validate (status `valid_untrusted`). (2) 64 image bytes altered after signing: content hash mismatch (`assertion.dataHash.mismatch`), so status `invalid`, finding 0.7. (3) Re-saved without the manifest: status `none`, shown with no weight, not called fake |
| C2PA signer trust | Not established in any test. The fixtures and the demo samples are signed with test certificates that are not on a trust list (`signingCredential.untrusted`). A valid manifest therefore shows the file is unchanged since signing; it does **not** show who signed it is trustworthy |
| Custody hash chain | Tested on copies of the database after Round 2 (390 entries) and in the automated test `tests/test_custody.py`. **Detected:** a changed `detail` text (6th entry), a changed stored file hash (1st entry with a file hash), and deletion of an entry in the **middle** of the chain (the 8th entry; reported at the next entry). **Not detected:** deletion of the **newest** entry. **Not tested:** re-ordering, insertion of new entries, deletion of the first entry |
| Claimed date vs EXIF | Wrong date contradicted; correct date confirmed |
| "Unedited" claim vs editing-software metadata | Contradicted (Lightroom export) |
| Authentic camera photos (19, including 11 full-resolution originals) | 19 of 19 low, or correctly reported as edited where the EXIF shows Lightroom or GIMP |
| Upload validation | Empty, random-byte, fake-executable, PDF, no-file and 13-file uploads rejected cleanly (400/415) |
| PDF and dashboard | Checked against the JSON report:<br>• Round 1, 60 PDFs: label, score, every finding, every person ID<br>• Round 2, 42 PDFs: label, every finding, every correlation<br>• 18 dashboards (9 per round): score, label and every finding shown, with no browser errors |
| People panel (experimental) | Face counts correct on every group photo and portrait. Never changes the score: identical results in 10 of 10 runs with People on, failing, or forced to flag everyone |
| Speed (4 vCPU, no GPU, cloud test machine) | 0.0–4.6 s per case, including 22 MP photos. PDF generation took 0.1–8.9 s (timed in Round 1 only) |

## Known limitations (measured)

1. **Blind AI-image detection is not reliable.** 0 of 16 AI-generated or AI-edited images were flagged. The set was 13 Stable Diffusion, SDXL and StyleGAN samples, 2 Latent Diffusion inpaintings and 1 AI-edited group photo. The frequency-based generator check is experimental. Its raw score tops out below its trigger on real images, so it did not separate AI images from camera photos.
2. **Blind edit detection is weak without a registered original.**
   - Images: 3 of 4 controlled object edits missed (splice, copy-move, pasted face). A partial removal got only a weak box.
   - Video: a 30 s jump cut and a pasted patch were missed.
   - Audio: a splice from another recording and a band-limited section were missed. Inserted digital silence was found at 5.0–6.0 s.
3. **Harmless processing can raise scores.**
   - A cropped photo scored high (0.66) from noise false positives.
   - Quality 50/30 re-saves of one photo scored medium.
   - A resized or WhatsApp-style copy of a registered original is reported as "Modified version" (medium) even when almost no pixels changed.
   - Real speech can show weak (0.35) segments.
4. **Perceptual matching misses some copies.** It misses mirrored images, heavy crops and large edits (distances 30, 22 and 14; the cut-off is 10 of 64 bits).
5. **People analysis is experimental and never scored.**
   - Faces under 28 px are skipped.
   - The detector once took bees for faces (confidence 0.74–0.81 vs 0.90–0.96 for real faces) and highlighted one for review.
   - A highlight is never a finding that a person was inserted.
6. **Text rules are English-only.** A Hindi caption produced no findings. The deeper claim review needs an Anthropic API key.
7. **The custody chain is tamper-evident, not tamper-proof.** Deleting the newest entry is not detected, because the latest (head) hash is not anchored outside the database. Someone with write access could also rebuild the whole chain. Re-ordering, inserted entries and deleting the first entry were not tested.
8. **C2PA shows integrity, not signer trust.** No trust list is configured. Without the c2pa-python library, the check only scans for a manifest marker and reports "present, not verified".
9. **A truncated JPEG passes validation.** Its analysis fails, and the failure appears only as a limitation line under a "No strong indicators" headline.

## Planned (not built)

The plan for after the hackathon is to benchmark first:

- **Harness:** an offline benchmark harness for image, video, audio and text, with separate model and dataset catalogues.
- **Before any download:** check licences, model sizes and dependencies.
- **Thresholds:** acceptance thresholds set before evaluation.
- **Metrics:** precision, recall, F1, false-positive rate, time and memory, plus robustness to compression and resizing.
- **Integration:** a model that passes is added as an experimental, unscored module first.
- **Candidates to evaluate:**
  - trained AI-image and splice-localisation detectors;
  - copy-move matching;
  - a face-swap classifier on detected faces;
  - a lip-sync check;
  - voice-spoof detection;
  - speech-to-text;
  - offline claim and caption consistency.
