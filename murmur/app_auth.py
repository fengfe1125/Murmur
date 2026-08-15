"""App Attest enrollment and per-request assertion verification.

Production never falls back to a development token.  The development path is
available only when the process was explicitly started in development mode.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtensionOID, ObjectIdentifier

from .app_settings import AppSettings
from .app_store import AppStore, AuthKey

APPLE_APP_ATTEST_ROOT_CA = b"""-----BEGIN CERTIFICATE-----
MIICITCCAaegAwIBAgIQC/O+DvHN0uD7jG5yH2IXmDAKBggqhkjOPQQDAzBSMSYw
JAYDVQQDDB1BcHBsZSBBcHAgQXR0ZXN0YXRpb24gUm9vdCBDQTETMBEGA1UECgwK
QXBwbGUgSW5jLjETMBEGA1UECAwKQ2FsaWZvcm5pYTAeFw0yMDAzMTgxODMyNTNa
Fw00NTAzMTUwMDAwMDBaMFIxJjAkBgNVBAMMHUFwcGxlIEFwcCBBdHRlc3RhdGlv
biBSb290IENBMRMwEQYDVQQKDApBcHBsZSBJbmMuMRMwEQYDVQQIDApDYWxpZm9y
bmlhMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAERTHhmLW07ATaFQIEVwTtT4dyctdh
NbJhFs/Ii2FdCgAHGbpphY3+d8qjuDngIN3WVhQUBHAoMeQ/cLiP1sOUtgjqK9au
Yen1mMEvRq9Sk3Jm5X8U62H+xTD3FE9TgS41o0IwQDAPBgNVHRMBAf8EBTADAQH/
MB0GA1UdDgQWBBSskRBTM72+aEH/pwyp5frq5eWKoTAOBgNVHQ8BAf8EBAMCAQYw
CgYIKoZIzj0EAwMDaAAwZQIwQgFGnByvsiVbpTKwSga0kP0e8EeDS4+sQmTvb7vn
53O5+FRXgeLhpJ06ysC5PrOyAjEAp5U4xDgEgllF7En3VcE3iexZZtKeYnpqtijV
oyFraWVIyd/dganmrduC1bmTBGwD
-----END CERTIFICATE-----
"""

NONCE_OID = ObjectIdentifier("1.2.840.113635.100.8.2")
PRODUCTION_AAGUID = b"appattest" + b"\x00" * 7
DEVELOPMENT_AAGUID = b"appattestdevelop"


class AppAuthError(RuntimeError):
    code = "invalid_assertion"


class AttestationRequired(AppAuthError):
    code = "attestation_required"


class AppAttestUnsupported(AppAuthError):
    code = "app_attest_unsupported"


class InvalidAttestation(AppAuthError):
    code = "invalid_attestation"


@dataclass(frozen=True)
class AttestationResult:
    public_key: bytes
    receipt: bytes | None
    counter: int
    environment: str
    platform: str = "ios"


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    device_id: str
    key_id: str


class DeviceAttestor(Protocol):
    """One client platform's hardware-attestation scheme.

    Apple splits into attestation-then-assertion; Android's Key Attestation and
    Keystore signatures land on the same two steps, so the shape is shared.
    """

    platform: str

    def verify_attestation(
        self, attestation: bytes, *, key_id: str, client_data_hash: bytes
    ) -> AttestationResult: ...

    def verify_assertion(
        self, assertion: bytes, *, public_key: bytes, client_data_hash: bytes,
        previous_counter: int,
    ) -> int: ...


def _b64decode(value: str) -> bytes:
    try:
        raw = value.encode("ascii")
        return base64.b64decode(
            raw + b"=" * (-len(raw) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise AppAuthError("invalid base64 value") from exc


def _cbor_loads(value: bytes):
    try:
        import cbor2
    except ImportError as exc:  # production startup calls ensure_available()
        raise RuntimeError("App Attest requires cbor2>=5.6") from exc
    try:
        return cbor2.loads(value)
    except Exception as exc:
        raise InvalidAttestation("malformed CBOR") from exc


def _verify_certificate_signature(cert: x509.Certificate, issuer_public_key) -> None:
    if isinstance(issuer_public_key, ec.EllipticCurvePublicKey):
        issuer_public_key.verify(
            cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm)
        )
    elif isinstance(issuer_public_key, rsa.RSAPublicKey):
        issuer_public_key.verify(
            cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(),
            cert.signature_hash_algorithm,
        )
    else:
        raise InvalidAttestation("unsupported certificate public key")


def _certificate_time_valid(cert: x509.Certificate, now: datetime) -> bool:
    before = getattr(cert, "not_valid_before_utc", None)
    after = getattr(cert, "not_valid_after_utc", None)
    if before is None:
        before = cert.not_valid_before.replace(tzinfo=UTC)
        after = cert.not_valid_after.replace(tzinfo=UTC)
    return before <= now <= after


def _der_primitive_values(data: bytes) -> list[bytes]:
    """Return primitive values from a strictly bounded DER tree.

    Apple's nonce extension is a SEQUENCE containing a context-specific field
    that in turn contains an OCTET STRING.  Parsing the TLV structure avoids a
    dangerous raw-substring match while keeping the verifier dependency-light.
    """
    values: list[bytes] = []

    def walk(blob: bytes, depth: int = 0) -> None:
        if depth > 8:
            raise InvalidAttestation("nonce extension is too deeply nested")
        offset = 0
        while offset < len(blob):
            if offset + 2 > len(blob):
                raise InvalidAttestation("truncated DER value")
            tag, first = blob[offset], blob[offset + 1]
            offset += 2
            if first & 0x80:
                count = first & 0x7F
                if count == 0 or count > 4 or offset + count > len(blob):
                    raise InvalidAttestation("invalid DER length")
                length = int.from_bytes(blob[offset:offset + count], "big")
                offset += count
            else:
                length = first
            if length < 0 or offset + length > len(blob):
                raise InvalidAttestation("DER length exceeds extension")
            value = blob[offset:offset + length]
            offset += length
            if tag & 0x20:  # constructed
                walk(value, depth + 1)
            else:
                values.append(value)

    walk(data)
    return values


class AppleAppAttestVerifier:
    """Verifier for Apple's ``apple-appattest`` WebAuthn-shaped objects."""

    platform = "ios"

    def __init__(
        self,
        *,
        app_id: str,
        environment: str = "production",
        root_pem: bytes | None = None,
        root_path: str | Path | None = None,
    ):
        if not app_id:
            raise RuntimeError("App Attest verifier requires an App ID")
        if environment not in {"production", "development"}:
            raise RuntimeError("invalid App Attest environment")
        self.app_id = app_id
        self.environment = environment
        if root_path:
            pem = Path(root_path).expanduser().read_bytes()
        else:
            pem = root_pem or APPLE_APP_ATTEST_ROOT_CA
        try:
            self.root = x509.load_pem_x509_certificate(pem)
        except ValueError as exc:
            raise RuntimeError("invalid App Attest root certificate") from exc
        if not _certificate_time_valid(self.root, datetime.now(UTC)):
            raise RuntimeError("App Attest root certificate is expired or not yet valid")
        self.rp_id_hash = hashlib.sha256(app_id.encode("utf-8")).digest()

    @staticmethod
    def ensure_available() -> None:
        try:
            import cbor2  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("production App Attest requires cbor2>=5.6") from exc

    def _verify_chain(self, values: list[bytes]) -> x509.Certificate:
        if not values:
            raise InvalidAttestation("attestation certificate chain is empty")
        try:
            certs = [x509.load_der_x509_certificate(bytes(value)) for value in values]
        except ValueError as exc:
            raise InvalidAttestation("invalid attestation certificate") from exc
        now = datetime.now(UTC)
        for cert in certs:
            if not _certificate_time_valid(cert, now):
                raise InvalidAttestation("attestation certificate is expired or not yet valid")
        try:
            for child, issuer in zip(certs, certs[1:], strict=False):
                if child.issuer != issuer.subject:
                    raise InvalidAttestation("attestation certificate issuer mismatch")
                _verify_certificate_signature(child, issuer.public_key())
            last = certs[-1]
            if last.fingerprint(hashes.SHA256()) == self.root.fingerprint(hashes.SHA256()):
                _verify_certificate_signature(last, last.public_key())
            else:
                if last.issuer != self.root.subject:
                    raise InvalidAttestation("attestation chain does not end at Apple root")
                _verify_certificate_signature(last, self.root.public_key())
        except InvalidSignature as exc:
            raise InvalidAttestation("invalid attestation certificate signature") from exc
        for intermediate in certs[1:]:
            try:
                if not intermediate.extensions.get_extension_for_oid(
                    ExtensionOID.BASIC_CONSTRAINTS
                ).value.ca:
                    raise InvalidAttestation("non-CA certificate in attestation chain")
            except x509.ExtensionNotFound as exc:
                raise InvalidAttestation("intermediate certificate lacks CA constraint") from exc
        return certs[0]

    def verify_attestation(
        self, attestation: bytes, *, key_id: str, client_data_hash: bytes
    ) -> AttestationResult:
        if len(client_data_hash) != 32:
            raise InvalidAttestation("client data hash must be SHA-256")
        obj = _cbor_loads(attestation)
        if not isinstance(obj, dict) or obj.get("fmt") != "apple-appattest":
            raise InvalidAttestation("unexpected attestation format")
        auth_data = obj.get("authData")
        statement = obj.get("attStmt")
        if not isinstance(auth_data, bytes) or not isinstance(statement, dict):
            raise InvalidAttestation("attestation object is incomplete")
        leaf = self._verify_chain(statement.get("x5c") or [])
        try:
            if leaf.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value.ca:
                raise InvalidAttestation("attestation leaf certificate cannot be a CA")
        except x509.ExtensionNotFound as exc:
            raise InvalidAttestation("attestation leaf certificate lacks Basic Constraints") from exc
        if len(auth_data) < 55 or auth_data[:32] != self.rp_id_hash:
            raise InvalidAttestation("App ID hash does not match")
        flags = auth_data[32]
        if not flags & 0x40:
            raise InvalidAttestation("attested credential data is missing")
        sign_count = struct.unpack(">I", auth_data[33:37])[0]
        if sign_count != 0:
            raise InvalidAttestation("initial attestation counter must be zero")
        aaguid = auth_data[37:53]
        expected_aaguid = (
            PRODUCTION_AAGUID if self.environment == "production" else DEVELOPMENT_AAGUID
        )
        if not hmac.compare_digest(aaguid, expected_aaguid):
            raise InvalidAttestation("App Attest environment does not match")
        credential_length = struct.unpack(">H", auth_data[53:55])[0]
        end = 55 + credential_length
        if credential_length != 32 or len(auth_data) <= end:
            raise InvalidAttestation("invalid credential ID")
        credential_id = auth_data[55:end]
        if not hmac.compare_digest(credential_id, _b64decode(key_id)):
            raise InvalidAttestation("credential ID does not match key ID")

        public_key = leaf.public_key()
        if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
            public_key.curve, ec.SECP256R1
        ):
            raise InvalidAttestation("App Attest key must be P-256")
        point = public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        if not hmac.compare_digest(hashlib.sha256(point).digest(), credential_id):
            raise InvalidAttestation("key ID is not the hash of the attested public key")

        cose = _cbor_loads(auth_data[end:])
        numbers = public_key.public_numbers()
        if not isinstance(cose, dict) or cose.get(1) != 2 or cose.get(3) != -7:
            raise InvalidAttestation("credential public key is not EC2/ES256")
        if cose.get(-1) != 1 or cose.get(-2) != numbers.x.to_bytes(32, "big") \
                or cose.get(-3) != numbers.y.to_bytes(32, "big"):
            raise InvalidAttestation("credential and certificate public keys differ")

        nonce = hashlib.sha256(auth_data + client_data_hash).digest()
        try:
            raw_extension = leaf.extensions.get_extension_for_oid(NONCE_OID).value.value
        except (x509.ExtensionNotFound, AttributeError) as exc:
            raise InvalidAttestation("attestation certificate has no nonce") from exc
        if not any(hmac.compare_digest(value, nonce)
                   for value in _der_primitive_values(raw_extension) if len(value) == 32):
            raise InvalidAttestation("attestation nonce does not match")

        spki = public_key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        receipt = statement.get("receipt")
        if receipt is not None and not isinstance(receipt, bytes):
            raise InvalidAttestation("invalid App Attest receipt")
        return AttestationResult(
            spki, receipt, sign_count, self.environment, self.platform
        )

    def verify_assertion(
        self, assertion: bytes, *, public_key: bytes, client_data_hash: bytes,
        previous_counter: int,
    ) -> int:
        obj = _cbor_loads(assertion)
        if not isinstance(obj, dict):
            raise AppAuthError("malformed assertion")
        auth_data, signature = obj.get("authenticatorData"), obj.get("signature")
        if not isinstance(auth_data, bytes) or len(auth_data) < 37 \
                or not isinstance(signature, bytes):
            raise AppAuthError("assertion is incomplete")
        if not hmac.compare_digest(auth_data[:32], self.rp_id_hash):
            raise AppAuthError("assertion App ID does not match")
        counter = struct.unpack(">I", auth_data[33:37])[0]
        if counter <= previous_counter:
            raise AppAuthError("assertion counter did not advance")
        try:
            key = serialization.load_der_public_key(public_key)
        except ValueError as exc:
            raise AppAuthError("stored public key is invalid") from exc
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise AppAuthError("stored public key is not elliptic-curve")
        try:
            key.verify(signature, auth_data + client_data_hash, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise AppAuthError("invalid assertion signature") from exc
        return counter


class AppAuthenticator:
    def __init__(
        self, settings: AppSettings, store: AppStore,
        verifier: DeviceAttestor | None = None,
        *,
        attestors: Mapping[str, DeviceAttestor] | None = None,
    ):
        self.settings = settings
        self.store = store
        registry: dict[str, DeviceAttestor] = dict(attestors or {})
        if verifier is not None:
            registry.setdefault(getattr(verifier, "platform", "ios"), verifier)
        if settings.production and "ios" not in registry:
            AppleAppAttestVerifier.ensure_available()
            registry["ios"] = AppleAppAttestVerifier(
                app_id=settings.app_id,
                environment="production",
                root_path=settings.attest_root_path,
            )
        self.attestors = registry

    def _attestor(self, platform: str) -> DeviceAttestor:
        attestor = self.attestors.get(platform)
        if attestor is None:
            raise AttestationRequired(f"no attestor is configured for {platform}")
        return attestor

    def _check_development_token(self, supplied: str | None) -> None:
        expected = self.settings.development_token
        if self.settings.production or not self.settings.allow_development or not expected:
            raise AttestationRequired("development authentication is disabled")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise AppAuthError("invalid development token")

    def enroll(
        self,
        *,
        challenge_id: str,
        key_id: str,
        attestation_b64: str | None,
        development_token: str | None,
        platform: str = "ios",
    ) -> AttestationResult:
        challenge = self.store.consume_challenge(challenge_id, "enrollment", key_id=None)
        client_data_hash = hashlib.sha256(challenge).digest()
        if self.settings.production:
            if not attestation_b64:
                raise AttestationRequired("a device attestation is required")
            # The client declares its platform only here.  Declaring the wrong
            # one fails closed: the chain will not verify against that root.
            return self._attestor(platform).verify_attestation(
                _b64decode(attestation_b64), key_id=key_id,
                client_data_hash=client_data_hash,
            )
        self._check_development_token(development_token)
        if not key_id.startswith("dev-") or not 8 <= len(key_id) <= 180:
            raise AppAuthError("development key ID must start with dev-")
        return AttestationResult(b"", None, 0, "development", platform)

    @staticmethod
    def request_client_data_hash(
        challenge: bytes,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        body_digest: bytes | None = None,
    ) -> bytes:
        if body_digest is None:
            body_digest = hashlib.sha256(body or b"").digest()
        if len(body_digest) != 32:
            raise AppAuthError("request body digest must be SHA-256")
        return hashlib.sha256(
            challenge + method.upper().encode("ascii") + path.encode("utf-8") + body_digest
        ).digest()

    def authenticate(
        self,
        *,
        method: str,
        path: str,
        body: bytes | None,
        body_digest: bytes | None = None,
        key_id: str | None,
        challenge_id: str | None,
        assertion_b64: str | None,
        development_token: str | None,
    ) -> AuthContext:
        if not key_id or not challenge_id:
            raise AttestationRequired("App Attest headers are required")
        try:
            key: AuthKey = self.store.auth_key(key_id)
        except Exception as exc:
            raise AppAuthError("unknown app key") from exc
        challenge = self.store.consume_challenge(
            challenge_id, "request", key_id=key_id
        )
        client_hash = self.request_client_data_hash(
            challenge, method, path, body, body_digest=body_digest
        )
        if self.settings.production:
            if not assertion_b64 or not key.public_key:
                raise AttestationRequired("a device assertion is required")
            # The platform comes from the enrolled key, never from the request.
            # Letting a caller pick its own verifier here would be a downgrade.
            counter = self._attestor(key.platform).verify_assertion(
                _b64decode(assertion_b64), public_key=key.public_key,
                client_data_hash=client_hash, previous_counter=key.counter,
            )
            if counter > key.counter:
                self.store.advance_counter(key_id, key.counter, counter)
        else:
            self._check_development_token(development_token)
        return AuthContext(key.user_id, key.device_id, key.key_id)
