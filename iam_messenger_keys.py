"""Device public keys; private keys never enter IAM.

Android SHA256withECDSA interoperates with P-256, DER SubjectPublicKeyInfo
and DER ECDSA signatures. This proves possession, NOT hardware attestation
or user presence. Sensitive approvals need a separate user-authenticated flow.
"""
import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from iam_messenger_access import AccessDenied


def decode(value, maximum):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise AccessDenied('Invalid device key or signature')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise AccessDenied('Invalid device key or signature') from exc
    if base64.b64encode(raw).decode('ascii') != value:
        raise AccessDenied('Noncanonical device encoding')
    return raw


def public_key(value):
    raw = decode(value, 256)
    try:
        key = serialization.load_der_public_key(raw)
    except (ValueError, TypeError) as exc:
        raise AccessDenied('Invalid device public key') from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise AccessDenied('P-256 device public key required')
    if key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != raw:
        raise AccessDenied('Canonical device public key required')
    return key


def fingerprint(value):
    public_key(value)
    return hashlib.sha256(decode(value, 256)).hexdigest()


def verify(value, signature, payload):
    try:
        public_key(value).verify(decode(signature, 128), payload, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as exc:
        raise AccessDenied('Device signature rejected') from exc
