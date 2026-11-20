# Sync ID — runnable hackathon prototype

Sync ID is an independent digital content identity service. TruthLens is one HTTP client.
This is a proposed protocol/reference prototype, not an adopted standard or a production identity authority.

## Run now (Windows)

From the project root, run these commands in separate terminals:

```powershell
.\run-syncid.bat
.\run.bat
```

The prepared `.venv` contains the dependencies. For a fresh checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Sync ID: http://127.0.0.1:8001/docs and http://127.0.0.1:8001/health

TruthLens: http://127.0.0.1:8000 — choose **Sync ID** in the toolbar.

Independent installation (does not require TruthLens/forensic dependencies):

```powershell
python -m pip install -r syncid/requirements.txt
python -m uvicorn syncid.app:app --host 127.0.0.1 --port 8001
```

Use one Uvicorn worker for this MVP. Launch from the project root so the `syncid` package is on the import path.

## End-to-end DemoAI demonstration

DemoAI is explicitly a **simulated producer**, not an actual external AI company integration.
The script supplies a generated demonstration graphic; it does not call an AI image model.

```powershell
.\.venv\Scripts\python.exe -m syncid.demo --truthlens-url http://127.0.0.1:8000
```

The script uses real HTTP requests and asserts that:

1. The original PNG receives a UUIDv7 and an Ed25519-signed record.
2. Registering the same bytes reuses the identity and signed record.
3. The original file matches the registered SHA-256.
4. A visible rectangular edit produces a hash mismatch and rejected content verification.
5. The original registration signature remains valid after the image is modified.
6. TruthLens independently calculates both file hashes and checks both record signatures.

Artifacts appear in `syncid-demo-output/`: `original.png`, `modified.png`, `identity.json`,
`public-key.json`, and `results.json`. They contain no private key.

For a browser demonstration: open TruthLens → **Sync ID**, select `original.png`, click **Register with Sync ID**,
then **Verify file**. Select `modified.png` while retaining the ID and click **Verify file**.
Show **MISMATCH**, signature **valid**, registration **registered**, and origin **not established** together.
Do not register the modified image during this comparison: that intentionally creates a separate valid identity.

## API

| Method | Endpoint | Request / response |
|---|---|---|
| POST | `/v1/register` | Multipart `file`, optional self-reported `producer_label`, `producer_simulated`; 201 new / 200 duplicate |
| GET | `/v1/identity/{sync_id}` | Signed record and signature/registration status; 404 if unknown |
| POST | `/v1/verify` | Multipart `file`, `sync_id`; independent content comparison plus record signature validity |
| GET | `/v1/public-key` | Public Ed25519 key, fingerprint/key ID, registry issuer |
| GET | `/health` | Database connectivity, version, registration-auth mode |

There is no endpoint that accepts a client-provided hash as registration evidence. All registered digests
and byte lengths are calculated by the server. Media type is a best-effort header classification;
unknown bytes use `application/octet-stream`. It is not a full media validity or safety check.
File contents are processed transiently and are not retained in the Sync ID registry.

```powershell
curl.exe -F "file=@syncid-demo-output/original.png" http://127.0.0.1:8001/v1/register
curl.exe http://127.0.0.1:8001/v1/identity/PASTE-SYNC-ID
curl.exe -F "file=@syncid-demo-output/modified.png" -F "sync_id=PASTE-SYNC-ID" http://127.0.0.1:8001/v1/verify
```

An expected content mismatch returns HTTP 200 with `hash_match: false`, `content_verified: false`,
and `verification_status: rejected`. The registration can still be valid. HTTP errors distinguish empty files (400),
unknown IDs (404), oversized files (413), and invalid request fields (422).

TruthLens adds only `/api/syncid/register` and `/api/syncid/verify`; its local registry, IDs, evidence,
pipeline, and scoring are preserved. Sync ID runs independently and never imports TruthLens.
TruthLens performs explicit uploaded-file checks through the tool panel; it does not automatically register
case evidence or add verification snapshots to existing forensic reports/PDFs.

## Signed record and public verification

The envelope contains exactly `protected`, `payload`, and `signature`. The protected fields bind the
record type, version, algorithm, issuer label, and public-key fingerprint. The payload binds the UUIDv7,
SHA-256, byte length, media type, registration timestamp, and self-reported producer labels.

The signed bytes are:

```text
UTF8("SyncID/v1\0") || RFC8785_JCS({"protected": protected, "payload": payload})
```

The signature uses Ed25519 through `cryptography`; UUIDv7 uses `uuid6`; canonicalization uses `rfc8785`.
Signatures/public keys use unpadded base64url. The key ID is `ed25519:` plus the SHA-256 of the raw public key.
The record is detached from the file, avoiding embedding/hash circularity.

Offline record verification with a separately authenticated public key:

```python
import json
from pathlib import Path
from syncid.crypto import decode, verify_record

record = json.loads(Path("syncid-demo-output/identity.json").read_text(encoding="utf-8"))
key = json.loads(Path("syncid-demo-output/public-key.json").read_text(encoding="utf-8"))
assert verify_record(record, decode(key["public_key"]))
```

This checks only the record. Independently hash the file to establish whether its bytes match.
A downloaded self-published key is not proof of issuer identity. TruthLens reports `not_established`
unless `SYNCID_PUBLIC_KEY` is configured with an independently obtained base64url key. Even a pinned key
does not prove capture origin, ownership, or the truth of producer claims.

## Configuration and keys

| Variable | Default |
|---|---|
| `SYNCID_DB` | `%USERPROFILE%/.syncid/registry.sqlite3` |
| `SYNCID_KEY_PATH` | `%USERPROFILE%/.syncid/signing-key.pem` |
| `SYNCID_ISSUER` | `syncid-local-demo` (registry label, not authenticated real-world identity) |
| `SYNCID_URL` | `http://127.0.0.1:8001` (TruthLens adapter) |
| `SYNCID_PUBLIC_KEY` | Unset; optional trusted-key pin in TruthLens |
| `SYNCID_API_TOKEN` | Unset; optional server-side bearer secret for registration |

Private keys are generated once outside source control and are never returned to the frontend.
The development PEM is unencrypted. POSIX creation requests owner-only permissions; Windows uses
the enclosing user's directory ACLs. Production secret storage, Windows ACL hardening, and hardware key custody are not implemented.
Back up the database **and** key. Startup refuses a missing/mismatched key for a populated registry.

Without a token, registration is intentionally open for a local demo. The supplied launch command binds
only to loopback. If setting `SYNCID_API_TOKEN`, give the same value to the service and TruthLens processes;
do not put it in frontend JavaScript. Existing TruthLens endpoints, including the registration proxy,
still lack user authentication, so this is not a public deployment design.

## Tests and observed results

```powershell
.\.venv\Scripts\python.exe -m pytest -q syncid/tests tests/test_syncid_integration.py
.\.venv\Scripts\python.exe -m pytest -q tests
```

Implementation-session results:

- Focused Sync ID/integration suite: **15 passed**.
- TruthLens suite including the 4 integration tests: **38 passed, 2 skipped**.
- The 2 skipped tests require `pdftotext`, absent on this machine. PDF generation is exercised by other tests.
- Live DemoAI HTTP demonstration through both services: **passed**.
- Concurrent registration test: 16 submissions, one identity and exactly one new registration.
- Restart persistence, signature tampering, wrong keys, optional token, size limits, and missing-key behavior are tested.

The suites overlap in the four integration tests; do not add their pass counts as unique cases.

## Deliberately incomplete

- No real AI-company, camera, or manufacturer integration or independently verified origin.
- No issuer enrollment, per-user authorization, production rate limiting, private registry, or ownership adjudication.
- No key rotation/revocation, trusted timestamp authority, or signed lifecycle/status events.
- No derivative graph, federation, global duplicate discovery, or independent cross-language conformance implementation.
- No embedded identity binding, screenshot recovery, or cryptographic identity persistence across byte changes.
- No production migration system, immutable audit store, HA database, or comprehensive upload-abuse protection.
- No Sync ID section persisted into TruthLens case JSON/PDF; the explicit UI verification is separate.

SQLite deduplication is transaction-protected within this registry. Independent registries can issue different
IDs for identical bytes. Registration time is a registry clock assertion, not proven capture time.
