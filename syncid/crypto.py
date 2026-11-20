"""Portable record verification: no database, network or private-key access."""
import base64
import hashlib
import uuid

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DOMAIN = b"SyncID/v1\x00"


def encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def public_bytes(key):
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(raw):
    return "ed25519:" + hashlib.sha256(raw).hexdigest()


def signing_bytes(record):
    return DOMAIN + rfc8785.dumps({"protected": record["protected"], "payload": record["payload"]})


def verify_record(record, public_key):
    """Check the v1 envelope and signature against explicitly supplied public-key bytes.

    This proves record integrity under that key, not issuer trust or content origin.
    """
    try:
        if set(record) != {"protected", "payload", "signature"}:
            return False
        protected, payload = record["protected"], record["payload"]
        if set(protected) != {"type", "version", "alg", "kid", "issuer"}:
            return False
        if (protected["type"], protected["version"], protected["alg"]) != ("syncid.registration", "1", "Ed25519"):
            return False
        if protected["kid"] != key_id(public_key) or not protected["issuer"]:
            return False
        if set(payload) != {"sync_id", "sha256", "hash_algorithm", "size_bytes", "media_type", "registered_at", "producer_label", "producer_simulated"}:
            return False
        if uuid.UUID(payload["sync_id"]).version != 7 or payload["hash_algorithm"] != "sha-256":
            return False
        digest = payload["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            return False
        if type(payload["size_bytes"]) is not int or payload["size_bytes"] <= 0:
            return False
        Ed25519PublicKey.from_public_bytes(public_key).verify(decode(record["signature"]), signing_bytes(record))
        return True
    except (InvalidSignature, ValueError, TypeError, KeyError, AttributeError, rfc8785.CanonicalizationError):
        return False
