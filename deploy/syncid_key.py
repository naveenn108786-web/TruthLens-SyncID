"""Materialise the Sync ID Ed25519 signing key from an environment secret (free hosting, no persistent disk).

SYNCID_SIGNING_SEED: 32 random bytes, base64 (standard or URL-safe), e.g. Render's `generateValue: true`
or `python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`.
The seed is the private key: keep it only in the hosting platform's secret settings, never in Git.
Writes a 0600 PKCS#8 PEM to SYNCID_KEY_PATH and prints the public key (base64url) for pinning.
Without a seed it prints nothing and Sync ID creates a random key that changes on every restart.
"""
import base64
import hashlib
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

seed_text = os.environ.get("SYNCID_SIGNING_SEED", "").strip()
if not seed_text:
    sys.exit(0)
raw = base64.b64decode(seed_text.replace("-", "+").replace("_", "/") + "=" * (-len(seed_text) % 4))
# Accept any secret length: hash anything that is not exactly 32 bytes down to a 32-byte seed.
seed = raw if len(raw) == 32 else hashlib.sha256(raw).digest()
key = Ed25519PrivateKey.from_private_bytes(seed)
path = os.environ["SYNCID_KEY_PATH"]
os.makedirs(os.path.dirname(path), exist_ok=True)
if os.path.exists(path):
    os.remove(path)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "wb") as f:
    f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
print(base64.urlsafe_b64encode(public).rstrip(b"=").decode("ascii"))
