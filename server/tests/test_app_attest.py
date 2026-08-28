"""Offline synthetic certificate/CBOR fixtures for AppleAppAttestVerifier."""

from __future__ import annotations

import base64
import hashlib
import struct
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

import murmur.app_auth as auth_module  # noqa: E402
from murmur.app_auth import (  # noqa: E402
    DEVELOPMENT_AAGUID,
    NONCE_OID,
    AppAuthError,
    AppleAppAttestVerifier,
    InvalidAttestation,
)


def cbor(value) -> bytes:
    def head(major: int, length: int) -> bytes:
        if length < 24:
            return bytes([(major << 5) | length])
        if length < 256:
            return bytes([(major << 5) | 24, length])
        if length < 65536:
            return bytes([(major << 5) | 25]) + struct.pack(">H", length)
        return bytes([(major << 5) | 26]) + struct.pack(">I", length)

    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode()
        return head(3, len(raw)) + raw
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(cbor(item) for item in value)
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(
            cbor(key) + cbor(item) for key, item in value.items()
        )
    raise TypeError(type(value))


def decode_cbor(data: bytes):
    def read(offset: int):
        first = data[offset]
        major, extra = first >> 5, first & 31
        offset += 1
        if extra < 24:
            length = extra
        elif extra == 24:
            length, offset = data[offset], offset + 1
        elif extra == 25:
            length, offset = struct.unpack(">H", data[offset:offset + 2])[0], offset + 2
        elif extra == 26:
            length, offset = struct.unpack(">I", data[offset:offset + 4])[0], offset + 4
        else:
            raise ValueError("unsupported test CBOR")
        if major == 0:
            return length, offset
        if major == 1:
            return -1 - length, offset
        if major in {2, 3}:
            value, offset = data[offset:offset + length], offset + length
            return (value if major == 2 else value.decode()), offset
        if major == 4:
            result = []
            for _ in range(length):
                item, offset = read(offset)
                result.append(item)
            return result, offset
        if major == 5:
            result = {}
            for _ in range(length):
                key, offset = read(offset)
                item, offset = read(offset)
                result[key] = item
            return result, offset
        raise ValueError("unsupported test CBOR")

    value, _ = read(0)
    return value


def certificate_name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


class AppAttestVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_decoder = auth_module._cbor_loads
        try:
            import cbor2

            auth_module._cbor_loads = cbor2.loads
        except ImportError:
            # Production still requires cbor2.  This decoder only keeps the
            # synthetic offline fixture runnable in an old developer virtualenv.
            auth_module._cbor_loads = decode_cbor

    @classmethod
    def tearDownClass(cls):
        auth_module._cbor_loads = cls.original_decoder

    def fixture(self, *, bad_nonce: bool = False, bad_aaguid: bool = False):
        app_id = "TEAM.com.sakura.Murmur"
        client_hash = hashlib.sha256(b"challenge").digest()
        root_key = ec.generate_private_key(ec.SECP256R1())
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(UTC)
        root_name = certificate_name("Synthetic App Attest Root")
        root = (
            x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
            .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
            .sign(root_key, hashes.SHA256())
        )
        numbers = leaf_key.public_key().public_numbers()
        point = leaf_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        credential = hashlib.sha256(point).digest()
        key_id = base64.urlsafe_b64encode(credential).rstrip(b"=").decode()
        cose_key = cbor({
            1: 2, 3: -7, -1: 1,
            -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big"),
        })
        aaguid = b"wrong-aaguid-000" if bad_aaguid else DEVELOPMENT_AAGUID
        auth_data = (
            hashlib.sha256(app_id.encode()).digest() + b"\x40" + struct.pack(">I", 0)
            + aaguid + struct.pack(">H", len(credential)) + credential + cose_key
        )
        nonce = hashlib.sha256(auth_data + client_hash).digest()
        if bad_nonce:
            nonce = b"x" * 32
        # App Attest extension: SEQUENCE -> [1] -> OCTET STRING(32-byte nonce).
        extension = b"\x30\x24\xa1\x22\x04\x20" + nonce
        leaf_name = certificate_name("Synthetic App Attest Key")
        leaf = (
            x509.CertificateBuilder().subject_name(leaf_name).issuer_name(root_name)
            .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=5))
            .add_extension(x509.UnrecognizedExtension(NONCE_OID, extension), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(root_key, hashes.SHA256())
        )
        attestation = cbor({
            "fmt": "apple-appattest", "authData": auth_data,
            "attStmt": {"x5c": [leaf.public_bytes(serialization.Encoding.DER)],
                         "receipt": b"receipt"},
        })
        verifier = AppleAppAttestVerifier(
            app_id=app_id, environment="development",
            root_pem=root.public_bytes(serialization.Encoding.PEM),
        )
        return verifier, leaf_key, key_id, client_hash, attestation

    def test_valid_attestation_extracts_key_and_receipt(self):
        verifier, _key, key_id, client_hash, attestation = self.fixture()
        result = verifier.verify_attestation(
            attestation, key_id=key_id, client_data_hash=client_hash
        )
        self.assertEqual(result.receipt, b"receipt")
        self.assertEqual(result.counter, 0)
        self.assertEqual(result.environment, "development")

    def test_nonce_and_aaguid_mismatches_fail_closed(self):
        verifier, _key, key_id, client_hash, attestation = self.fixture(bad_nonce=True)
        with self.assertRaises(InvalidAttestation):
            verifier.verify_attestation(attestation, key_id=key_id, client_data_hash=client_hash)
        verifier, _key, key_id, client_hash, attestation = self.fixture(bad_aaguid=True)
        with self.assertRaises(InvalidAttestation):
            verifier.verify_attestation(attestation, key_id=key_id, client_data_hash=client_hash)

    def test_leaf_without_basic_constraints_is_rejected(self):
        app_id = "TEAM.com.sakura.Murmur"
        client_hash = hashlib.sha256(b"challenge").digest()
        root_key = ec.generate_private_key(ec.SECP256R1())
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(UTC)
        root_name = certificate_name("Synthetic App Attest Root")
        root = (
            x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
            .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
            .sign(root_key, hashes.SHA256())
        )
        point = leaf_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        credential = hashlib.sha256(point).digest()
        key_id = base64.urlsafe_b64encode(credential).rstrip(b"=").decode()
        numbers = leaf_key.public_key().public_numbers()
        cose_key = cbor({
            1: 2, 3: -7, -1: 1,
            -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big"),
        })
        auth_data = (
            hashlib.sha256(app_id.encode()).digest() + b"\x40" + struct.pack(">I", 0)
            + DEVELOPMENT_AAGUID + struct.pack(">H", len(credential)) + credential + cose_key
        )
        nonce = hashlib.sha256(auth_data + client_hash).digest()
        extension = b"\x30\x24\xa1\x22\x04\x20" + nonce
        leaf_name = certificate_name("Synthetic App Attest Key")
        leaf = (
            x509.CertificateBuilder().subject_name(leaf_name).issuer_name(root_name)
            .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=5))
            .add_extension(x509.UnrecognizedExtension(NONCE_OID, extension), critical=False)
            .sign(root_key, hashes.SHA256())
        )
        attestation = cbor({
            "fmt": "apple-appattest", "authData": auth_data,
            "attStmt": {"x5c": [leaf.public_bytes(serialization.Encoding.DER)],
                         "receipt": b"receipt"},
        })
        verifier = AppleAppAttestVerifier(
            app_id=app_id, environment="development",
            root_pem=root.public_bytes(serialization.Encoding.PEM),
        )
        with self.assertRaises(InvalidAttestation):
            verifier.verify_attestation(
                attestation, key_id=key_id, client_data_hash=client_hash
            )

    def test_assertion_signature_rp_id_and_counter(self):
        verifier, key, key_id, client_hash, attestation = self.fixture()
        result = verifier.verify_attestation(
            attestation, key_id=key_id, client_data_hash=client_hash
        )
        assertion_hash = hashlib.sha256(b"asserted request").digest()
        auth_data = verifier.rp_id_hash + b"\x00" + struct.pack(">I", 1)
        signature = key.sign(auth_data + assertion_hash, ec.ECDSA(hashes.SHA256()))
        assertion = cbor({"authenticatorData": auth_data, "signature": signature})
        self.assertEqual(verifier.verify_assertion(
            assertion, public_key=result.public_key, client_data_hash=assertion_hash,
            previous_counter=0,
        ), 1)
        with self.assertRaises(AppAuthError):
            verifier.verify_assertion(
                assertion, public_key=result.public_key, client_data_hash=assertion_hash,
                previous_counter=1,
            )
        with self.assertRaises(AppAuthError):
            verifier.verify_assertion(
                assertion, public_key=result.public_key,
                client_data_hash=hashlib.sha256(b"tampered").digest(), previous_counter=0,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
