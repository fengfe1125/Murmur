"""Android Key Attestation verifier tests.

Builds synthetic chains the way ``test_app_attest.py`` builds synthetic Apple
ones -- a local root, a leaf carrying a hand-encoded KeyDescription extension --
so nothing here needs a device or the network.

Run directly: ``python tests/test_app_attest_android.py``.
"""

from __future__ import annotations

import base64
import hashlib
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from murmur.app_attest_android import (  # noqa: E402
    ATTESTATION_OID,
    AndroidKeyAttestor,
)
from murmur.app_auth import AppAuthError, InvalidAttestation  # noqa: E402

PACKAGE = "com.sakura.murmur"
RELEASE_DIGEST = hashlib.sha256(b"release-signing-certificate").digest()
DEBUG_DIGEST = hashlib.sha256(b"debug-signing-certificate").digest()


# ── a very small DER encoder, enough to build a KeyDescription ──────────────

def der_len(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def identifier(tag_class: int, constructed: bool, number: int) -> bytes:
    first = (tag_class << 6) | (0x20 if constructed else 0)
    if number < 0x1F:
        return bytes([first | number])
    out, value = [], number
    chunks = []
    while value:
        chunks.append(value & 0x7F)
        value >>= 7
    chunks = chunks[::-1] or [0]
    for index, chunk in enumerate(chunks):
        out.append(chunk | (0x80 if index < len(chunks) - 1 else 0))
    return bytes([first | 0x1F]) + bytes(out)


def tlv(tag_class: int, constructed: bool, number: int, content: bytes) -> bytes:
    return identifier(tag_class, constructed, number) + der_len(len(content)) + content


def der_int(value: int) -> bytes:
    raw = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big", signed=True)
    return tlv(0, False, 2, raw)


def der_enum(value: int) -> bytes:
    return tlv(0, False, 10, bytes([value]))


def der_octet(value: bytes) -> bytes:
    return tlv(0, False, 4, value)


def der_bool(value: bool) -> bytes:
    return tlv(0, False, 1, b"\xff" if value else b"\x00")


def der_seq(*items: bytes) -> bytes:
    return tlv(0, True, 16, b"".join(items))


def der_set(*items: bytes) -> bytes:
    return tlv(0, True, 17, b"".join(items))


def explicit(number: int, inner: bytes) -> bytes:
    return tlv(2, True, number, inner)


def application_id(package: str, digests: list[bytes]) -> bytes:
    inner = der_seq(
        der_set(der_seq(der_octet(package.encode()), der_int(1))),
        der_set(*[der_octet(d) for d in digests]),
    )
    return der_octet(inner)


def authorization_list(
    *, purpose=(2,), algorithm=3, digest=(4,), ec_curve=1, origin=0,
    root_of_trust=None, app_id=None,
) -> bytes:
    items = []
    if purpose is not None:
        items.append(explicit(1, der_set(*[der_int(p) for p in purpose])))
    if algorithm is not None:
        items.append(explicit(2, der_int(algorithm)))
    if digest is not None:
        items.append(explicit(5, der_set(*[der_int(d) for d in digest])))
    if ec_curve is not None:
        items.append(explicit(10, der_int(ec_curve)))
    if origin is not None:
        items.append(explicit(702, der_int(origin)))
    if root_of_trust is not None:
        items.append(explicit(704, root_of_trust))
    if app_id is not None:
        items.append(explicit(709, app_id))
    return der_seq(*items)


def root_of_trust(*, locked=True, boot_state=0) -> bytes:
    return der_seq(
        der_octet(b"\x01" * 32),   # verifiedBootKey
        der_bool(locked),
        der_enum(boot_state),
        der_octet(b"\x02" * 32),   # verifiedBootHash
    )


def key_description(challenge: bytes, *, security_level=1, tee=None, software=None) -> bytes:
    return der_seq(
        der_int(200),                       # attestationVersion
        der_enum(security_level),           # attestationSecurityLevel
        der_int(200),                       # keyMintVersion
        der_enum(security_level),           # keyMintSecurityLevel
        der_octet(challenge),
        der_octet(b""),                     # uniqueId
        software if software is not None else der_seq(),
        tee if tee is not None else authorization_list(
            root_of_trust=root_of_trust(),
            app_id=application_id(PACKAGE, [RELEASE_DIGEST]),
        ),
    )


def name(common: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


class AndroidKeyAttestorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_key = ec.generate_private_key(ec.SECP256R1())
        cls.root_name = name("Google Hardware Attestation Root (test)")
        now = datetime.now(UTC)
        cls.root_cert = (
            x509.CertificateBuilder().subject_name(cls.root_name)
            .issuer_name(cls.root_name).public_key(cls.root_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
            .sign(cls.root_key, hashes.SHA256())
        )
        cls.root_pem = cls.root_cert.public_bytes(serialization.Encoding.PEM)

    def build(self, *, challenge=None, description=None, leaf_key=None,
              serial=None, expired=False):
        """Return (attestation_der, key_id, leaf_public_key)."""
        challenge = challenge if challenge is not None else b"c" * 32
        leaf_key = leaf_key or ec.generate_private_key(ec.SECP256R1())
        extension = description if description is not None else key_description(challenge)
        now = datetime.now(UTC)
        not_after = now - timedelta(days=1) if expired else now + timedelta(days=30)
        leaf = (
            x509.CertificateBuilder().subject_name(name("droid"))
            .issuer_name(self.root_name).public_key(leaf_key.public_key())
            .serial_number(serial or x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2))
            .not_valid_after(not_after)
            .add_extension(
                x509.UnrecognizedExtension(ATTESTATION_OID, extension), critical=False
            )
            .sign(self.root_key, hashes.SHA256())
        )
        chain = der_seq(
            der_octet(leaf.public_bytes(serialization.Encoding.DER)),
            der_octet(self.root_cert.public_bytes(serialization.Encoding.DER)),
        )
        # The chain is a SEQUENCE of OCTET STRINGs; the verifier reads their
        # contents as DER certificates.
        spki = leaf_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        key_id = base64.urlsafe_b64encode(
            hashlib.sha256(spki).digest()
        ).rstrip(b"=").decode()
        return chain, key_id, leaf_key

    def attestor(self, **kwargs):
        options = dict(
            package_name=PACKAGE, signature_digests=[RELEASE_DIGEST],
            root_pem=self.root_pem,
        )
        options.update(kwargs)
        return AndroidKeyAttestor(**options)

    # ── the happy path ────────────────────────────────────────────────────

    def test_valid_attestation_yields_the_leaf_key(self):
        challenge = hashlib.sha256(b"nonce").digest()
        chain, key_id, leaf_key = self.build(challenge=challenge)
        result = self.attestor().verify_attestation(
            chain, key_id=key_id, client_data_hash=challenge
        )
        self.assertEqual(result.platform, "android")
        self.assertEqual(result.environment, "production")
        self.assertEqual(result.counter, 0)
        self.assertEqual(
            result.public_key,
            leaf_key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ),
        )

    def test_assertion_verifies_against_the_enrolled_key(self):
        challenge = hashlib.sha256(b"nonce").digest()
        chain, key_id, leaf_key = self.build(challenge=challenge)
        attestor = self.attestor()
        enrolled = attestor.verify_attestation(
            chain, key_id=key_id, client_data_hash=challenge
        )
        request_hash = hashlib.sha256(b"GET/v1/proactive/current").digest()
        signature = leaf_key.sign(request_hash, ec.ECDSA(hashes.SHA256()))
        # No counter exists to advance; the single-use challenge is the guard.
        self.assertEqual(
            attestor.verify_assertion(
                signature, public_key=enrolled.public_key,
                client_data_hash=request_hash, previous_counter=9,
            ),
            9,
        )

    def test_assertion_from_another_key_is_rejected(self):
        challenge = hashlib.sha256(b"nonce").digest()
        chain, key_id, _ = self.build(challenge=challenge)
        attestor = self.attestor()
        enrolled = attestor.verify_attestation(
            chain, key_id=key_id, client_data_hash=challenge
        )
        impostor = ec.generate_private_key(ec.SECP256R1())
        request_hash = hashlib.sha256(b"GET/v1/proactive/current").digest()
        with self.assertRaises(AppAuthError):
            attestor.verify_assertion(
                impostor.sign(request_hash, ec.ECDSA(hashes.SHA256())),
                public_key=enrolled.public_key, client_data_hash=request_hash,
                previous_counter=0,
            )

    # ── the things that must fail closed ──────────────────────────────────

    def test_challenge_mismatch_is_rejected(self):
        chain, key_id, _ = self.build(challenge=b"c" * 32)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=b"d" * 32
            )

    def test_another_application_is_rejected(self):
        challenge = b"c" * 32
        description = key_description(challenge, tee=authorization_list(
            root_of_trust=root_of_trust(),
            app_id=application_id("com.evil.clone", [RELEASE_DIGEST]),
        ))
        chain, key_id, _ = self.build(challenge=challenge, description=description)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_unknown_signing_certificate_is_rejected(self):
        challenge = b"c" * 32
        description = key_description(challenge, tee=authorization_list(
            root_of_trust=root_of_trust(),
            app_id=application_id(PACKAGE, [DEBUG_DIGEST]),
        ))
        chain, key_id, _ = self.build(challenge=challenge, description=description)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )
        # The same attestation is fine for a server that allows the debug cert.
        allowed = self.attestor(signature_digests=[RELEASE_DIGEST, DEBUG_DIGEST])
        self.assertEqual(
            allowed.verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            ).platform,
            "android",
        )

    def test_unlocked_bootloader_is_rejected_in_production(self):
        challenge = b"c" * 32
        description = key_description(challenge, tee=authorization_list(
            root_of_trust=root_of_trust(locked=False),
            app_id=application_id(PACKAGE, [RELEASE_DIGEST]),
        ))
        chain, key_id, _ = self.build(challenge=challenge, description=description)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_unverified_boot_state_is_rejected_in_production(self):
        challenge = b"c" * 32
        description = key_description(challenge, tee=authorization_list(
            root_of_trust=root_of_trust(boot_state=2),   # Unverified
            app_id=application_id(PACKAGE, [RELEASE_DIGEST]),
        ))
        chain, key_id, _ = self.build(challenge=challenge, description=description)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_software_only_attestation_is_rejected_in_production(self):
        challenge = b"c" * 32
        description = key_description(challenge, security_level=0)
        chain, key_id, _ = self.build(challenge=challenge, description=description)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_imported_key_is_rejected(self):
        challenge = b"c" * 32
        description = key_description(challenge, tee=authorization_list(
            origin=2,  # IMPORTED
            root_of_trust=root_of_trust(),
            app_id=application_id(PACKAGE, [RELEASE_DIGEST]),
        ))
        chain, key_id, _ = self.build(challenge=challenge, description=description)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_key_that_cannot_sign_is_rejected(self):
        challenge = b"c" * 32
        description = key_description(challenge, tee=authorization_list(
            purpose=(3,),  # VERIFY only
            root_of_trust=root_of_trust(),
            app_id=application_id(PACKAGE, [RELEASE_DIGEST]),
        ))
        chain, key_id, _ = self.build(challenge=challenge, description=description)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_key_id_must_be_the_hash_of_the_attested_key(self):
        challenge = b"c" * 32
        chain, _, _ = self.build(challenge=challenge)
        forged = base64.urlsafe_b64encode(b"z" * 32).rstrip(b"=").decode()
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=forged, client_data_hash=challenge
            )

    def test_chain_not_reaching_the_configured_root_is_rejected(self):
        challenge = b"c" * 32
        chain, key_id, _ = self.build(challenge=challenge)
        stranger = ec.generate_private_key(ec.SECP256R1())
        other_name = name("Some Other Root")
        now = datetime.now(UTC)
        other_root = (
            x509.CertificateBuilder().subject_name(other_name).issuer_name(other_name)
            .public_key(stranger.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
            .sign(stranger, hashes.SHA256())
        )
        attestor = self.attestor(
            root_pem=other_root.public_bytes(serialization.Encoding.PEM)
        )
        with self.assertRaises(InvalidAttestation):
            attestor.verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_expired_certificate_is_rejected(self):
        challenge = b"c" * 32
        chain, key_id, _ = self.build(challenge=challenge, expired=True)
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_leaf_without_the_attestation_extension_is_rejected(self):
        now = datetime.now(UTC)
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        leaf = (
            x509.CertificateBuilder().subject_name(name("bare"))
            .issuer_name(self.root_name).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .sign(self.root_key, hashes.SHA256())
        )
        chain = der_seq(
            der_octet(leaf.public_bytes(serialization.Encoding.DER)),
            der_octet(self.root_cert.public_bytes(serialization.Encoding.DER)),
        )
        spki = leaf_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        key_id = base64.urlsafe_b64encode(
            hashlib.sha256(spki).digest()
        ).rstrip(b"=").decode()
        with self.assertRaises(InvalidAttestation):
            self.attestor().verify_attestation(
                chain, key_id=key_id, client_data_hash=b"c" * 32
            )

    def test_malformed_extension_does_not_escape_as_an_unexpected_error(self):
        challenge = b"c" * 32
        for broken in (b"", b"\x30\x80", b"\x30\x05\x02\x7f\x00", b"\xbf\xff\xff\xff"):
            chain, key_id, _ = self.build(challenge=challenge, description=broken)
            with self.assertRaises(InvalidAttestation):
                self.attestor().verify_attestation(
                    chain, key_id=key_id, client_data_hash=challenge
                )

    # ── revocation ────────────────────────────────────────────────────────

    def test_revoked_certificate_is_rejected(self):
        challenge = b"c" * 32
        serial = 0x1234ABCD
        chain, key_id, _ = self.build(challenge=challenge, serial=serial)
        attestor = self.attestor(status_source=lambda: {
            "entries": {f"{serial:x}": {"status": "REVOKED", "reason": "KEY_COMPROMISE"}}
        })
        with self.assertRaises(InvalidAttestation):
            attestor.verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )

    def test_unreachable_status_list_fails_closed_by_default(self):
        challenge = b"c" * 32
        chain, key_id, _ = self.build(challenge=challenge)

        def unreachable():
            raise OSError("network down")

        with self.assertRaises(InvalidAttestation):
            self.attestor(status_source=unreachable).verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            )
        # ...and is survivable only when the operator explicitly opts in.
        relaxed = self.attestor(
            status_source=unreachable, revocation_fail_open=True
        )
        self.assertEqual(
            relaxed.verify_attestation(
                chain, key_id=key_id, client_data_hash=challenge
            ).platform,
            "android",
        )

    # ── construction guards ───────────────────────────────────────────────

    def test_a_root_certificate_is_required(self):
        with self.assertRaises(RuntimeError):
            AndroidKeyAttestor(
                package_name=PACKAGE, signature_digests=[RELEASE_DIGEST]
            )

    def test_a_signing_digest_is_required(self):
        with self.assertRaises(RuntimeError):
            AndroidKeyAttestor(
                package_name=PACKAGE, signature_digests=[], root_pem=self.root_pem
            )

    def test_development_environment_tolerates_an_unlocked_device(self):
        challenge = b"c" * 32
        description = key_description(
            challenge, security_level=0,
            tee=authorization_list(
                root_of_trust=root_of_trust(locked=False, boot_state=2),
                app_id=application_id(PACKAGE, [DEBUG_DIGEST]),
            ),
        )
        chain, key_id, _ = self.build(challenge=challenge, description=description)
        attestor = self.attestor(
            environment="development", signature_digests=[DEBUG_DIGEST]
        )
        result = attestor.verify_attestation(
            chain, key_id=key_id, client_data_hash=challenge
        )
        self.assertEqual(result.environment, "development")


if __name__ == "__main__":
    unittest.main(verbosity=2)
