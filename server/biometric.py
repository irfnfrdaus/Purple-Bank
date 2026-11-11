"""Verify device-key proofs; the server never receives face/fingerprint images.

The client uses Android Keystore auth-per-use keys. Without hardware attestation,
a signature proves possession of the enrolled key, not the security properties
of an arbitrary third-party client or civil identity of the account owner.
"""
import base64
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def public_key(encoded):
    raw = base64.b64decode(encoded, validate=True)
    try:
        key = serialization.load_der_public_key(raw)
    except UnsupportedAlgorithm as exc:
        raise ValueError("Unsupported public key") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("An EC P-256 public key is required")
    return key


def valid_signature(encoded_key, message, signature):
    try:
        key = public_key(encoded_key)
        key.verify(base64.b64decode(signature, validate=True), message.encode('utf-8'), ec.ECDSA(hashes.SHA256()))
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False
