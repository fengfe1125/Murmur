"""Android Key Attestation enrollment and per-request assertion verification.

The Android counterpart to :class:`murmur.app_auth.AppleAppAttestVerifier`.
Both prove the same two things, in different formats:

* enrolment -- the signing key was generated inside the device's secure
  hardware, in *our* app, on a device whose boot chain verified.  Apple ships
  that as a CBOR/WebAuthn attestation object; Android ships an X.509 chain
  whose leaf carries a ``KeyDescription`` extension.
* every request -- the same key signs.  Apple wraps it in CBOR with a counter;
  Android's Keystore emits a bare ECDSA signature.

The missing counter is not a gap.  Replay is already prevented by the
single-use challenge that :meth:`AppStore.consume_challenge` burns on the way
in; the App Attest counter is a second lock that Android simply does not offer.
``verify_assertion`` therefore returns the counter it was given.

There is deliberately no built-in root certificate.  Google publishes the
hardware attestation roots at
https://developer.android.com/privacy-and-security/security-key-attestation and
the operator pins the one they verified themselves, through
``MURMUR_APP_ATTEST_GOOGLE_ROOT_CA``.  A root baked into this file would be a
trust anchor nobody in the deployment ever looked at.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtensionOID, ObjectIdentifier

from .app_auth import (
    AppAuthError,
    AttestationResult,
    InvalidAttestation,
    _b64decode,
    _certificate_time_valid,
    _verify_certificate_signature,
)

log = logging.getLogger("murmur.app_attest_android")

ATTESTATION_OID = ObjectIdentifier("1.3.6.1.4.1.11129.2.1.17")

# KeyDescription field order.
CHALLENGE_INDEX = 4
SOFTWARE_ENFORCED_INDEX = 6
TEE_ENFORCED_INDEX = 7

# AuthorizationList context tags we care about.
TAG_PURPOSE = 1
TAG_ALGORITHM = 2
TAG_DIGEST = 5
TAG_EC_CURVE = 10
TAG_ORIGIN = 702
TAG_ROOT_OF_TRUST = 704
TAG_ATTESTATION_APPLICATION_ID = 709

# Keymaster/KeyMint enumerations.
SECURITY_LEVEL_SOFTWARE = 0
SECURITY_LEVEL_TRUSTED_ENVIRONMENT = 1
SECURITY_LEVEL_STRONGBOX = 2
HARDWARE_SECURITY_LEVELS = {SECURITY_LEVEL_TRUSTED_ENVIRONMENT, SECURITY_LEVEL_STRONGBOX}
PURPOSE_SIGN = 2
ALGORITHM_EC = 3
DIGEST_SHA_2_256 = 4
EC_CURVE_P_256 = 1
ORIGIN_GENERATED = 0
VERIFIED_BOOT_VERIFIED = 0


STATUS_LIST_URL = "https://android.googleapis.com/attestation/status"


class GoogleAttestationStatus:
    """Google's revoked-key list, fetched at most once per ``ttl`` seconds.

    Only enrolment consults this, so one slow fetch an hour never lands on the
    per-request path.
    """

    def __init__(self, *, url: str = STATUS_LIST_URL, ttl: float = 3600.0, timeout: float = 10.0):
        self.url, self.ttl, self.timeout = url, ttl, timeout
        self._cached: dict | None = None
        self._fetched_at = 0.0

    def __call__(self) -> dict:
        import time

        now = time.monotonic()
        if self._cached is not None and now - self._fetched_at < self.ttl:
            return self._cached
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("the attestation status list requires httpx") from exc
        response = httpx.get(
            self.url, timeout=self.timeout, headers={"cache-control": "max-age=0"}
        )
        if response.status_code != 200:
            raise RuntimeError(f"status list fetch failed status={response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("status list is not a JSON object")
        self._cached, self._fetched_at = payload, now
        return payload


@dataclass(frozen=True)
class DerValue:
    tag_class: int
    constructed: bool
    number: int
    content: bytes


def _read_tlv(data: bytes, offset: int) -> tuple[DerValue, int]:
    if offset >= len(data):
        raise InvalidAttestation("truncated DER value")
    first = data[offset]
    tag_class, constructed, number = first >> 6, bool(first & 0x20), first & 0x1F
    offset += 1
    if number == 0x1F:
        # High-tag-number form.  The AuthorizationList fields we need live at
        # tags 702-709, so this branch is not optional.
        number = 0
        while True:
            if offset >= len(data):
                raise InvalidAttestation("truncated DER tag")
            byte = data[offset]
            offset += 1
            number = (number << 7) | (byte & 0x7F)
            if number > 0xFFFFF:
                raise InvalidAttestation("implausible DER tag number")
            if not byte & 0x80:
                break
    if offset >= len(data):
        raise InvalidAttestation("truncated DER length")
    first_length = data[offset]
    offset += 1
    if first_length & 0x80:
        count = first_length & 0x7F
        if count == 0 or count > 4 or offset + count > len(data):
            raise InvalidAttestation("invalid DER length")
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    else:
        length = first_length
    if offset + length > len(data):
        raise InvalidAttestation("DER length exceeds the buffer")
    return DerValue(tag_class, constructed, number, data[offset:offset + length]), offset + length


def _der_values(data: bytes, *, limit: int = 64) -> list[DerValue]:
    values, offset = [], 0
    while offset < len(data):
        if len(values) >= limit:
            raise InvalidAttestation("DER sequence has too many elements")
        value, offset = _read_tlv(data, offset)
        values.append(value)
    return values


def _der_one(data: bytes) -> DerValue:
    values = _der_values(data, limit=2)
    if len(values) != 1:
        raise InvalidAttestation("expected exactly one DER value")
    return values[0]


def _explicit(value: DerValue) -> DerValue:
    """Unwrap an ``[n] EXPLICIT`` context tag to the value it encloses."""
    if not value.constructed:
        raise InvalidAttestation("context tag is not constructed")
    return _der_one(value.content)


def _der_int(value: DerValue) -> int:
    if not value.content:
        raise InvalidAttestation("empty DER integer")
    return int.from_bytes(value.content, "big", signed=True)


def _der_bool(value: DerValue) -> bool:
    if len(value.content) != 1:
        raise InvalidAttestation("malformed DER boolean")
    return value.content[0] != 0


def _int_set(value: DerValue) -> set[int]:
    return {_der_int(item) for item in _der_values(value.content)}


def _authorization_list(value: DerValue) -> dict[int, DerValue]:
    fields: dict[int, DerValue] = {}
    for item in _der_values(value.content, limit=128):
        if item.tag_class != 2:
            raise InvalidAttestation("AuthorizationList holds a non-context tag")
        fields[item.number] = _explicit(item)
    return fields


@dataclass(frozen=True)
class RootOfTrust:
    verified_boot_state: int
    device_locked: bool


def _root_of_trust(value: DerValue) -> RootOfTrust:
    parts = _der_values(value.content)
    if len(parts) < 3:
        raise InvalidAttestation("RootOfTrust is incomplete")
    # verifiedBootKey, deviceLocked, verifiedBootState, [verifiedBootHash]
    return RootOfTrust(_der_int(parts[2]), _der_bool(parts[1]))


def _application_id(value: DerValue) -> tuple[set[str], set[bytes]]:
    """Return the package names and signing certificate digests it attests to."""
    outer = _der_one(value.content)
    parts = _der_values(outer.content)
    if len(parts) < 2:
        raise InvalidAttestation("AttestationApplicationId is incomplete")
    packages = set()
    for info in _der_values(parts[0].content):
        fields = _der_values(info.content)
        if not fields:
            raise InvalidAttestation("AttestationPackageInfo is empty")
        packages.add(fields[0].content.decode("utf-8", "replace"))
    digests = {bytes(item.content) for item in _der_values(parts[1].content)}
    return packages, digests


class AndroidKeyAttestor:
    """Verifier for Android Key Attestation certificate chains."""

    platform = "android"

    def __init__(
        self,
        *,
        package_name: str,
        signature_digests: set[bytes] | list[bytes],
        root_path: str | Path | None = None,
        root_pem: bytes | None = None,
        environment: str = "production",
        status_source=None,
        revocation_fail_open: bool = False,
    ):
        if not package_name:
            raise RuntimeError("Android attestation requires an application ID")
        digests = {bytes(value) for value in signature_digests}
        if not digests:
            raise RuntimeError("Android attestation requires a signing certificate digest")
        if any(len(value) != 32 for value in digests):
            raise RuntimeError("Android signing certificate digests must be SHA-256")
        if environment not in {"production", "development"}:
            raise RuntimeError("invalid Android attestation environment")
        if not root_pem and not root_path:
            raise RuntimeError(
                "Android attestation requires a Google root certificate; set "
                "MURMUR_APP_ATTEST_GOOGLE_ROOT_CA"
            )
        self.package_name = package_name
        self.signature_digests = digests
        self.environment = environment
        self.status_source = status_source
        self.revocation_fail_open = revocation_fail_open
        pem = root_pem or Path(root_path).expanduser().read_bytes()  # type: ignore[arg-type]
        try:
            self.root = x509.load_pem_x509_certificate(pem)
        except ValueError as exc:
            raise RuntimeError("invalid Android attestation root certificate") from exc
        if not _certificate_time_valid(self.root, datetime.now(UTC)):
            raise RuntimeError("Android attestation root certificate is not currently valid")

    @classmethod
    def from_settings(cls, settings, *, status_source=None) -> AndroidKeyAttestor:
        settings.validate_android()
        return cls(
            package_name=settings.android_package or "",
            signature_digests=list(settings.android_signature_digests),
            root_path=settings.attest_google_root_path,
            environment="production" if settings.production else "development",
            status_source=status_source or GoogleAttestationStatus(),
            revocation_fail_open=settings.attest_revocation_fail_open,
        )

    def _verify_chain(self, values: list[bytes]) -> list[x509.Certificate]:
        if not values:
            raise InvalidAttestation("attestation certificate chain is empty")
        if len(values) > 8:
            raise InvalidAttestation("attestation certificate chain is too long")
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
                    raise InvalidAttestation("attestation chain does not end at the Google root")
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
        try:
            if certs[0].extensions.get_extension_for_oid(
                ExtensionOID.BASIC_CONSTRAINTS
            ).value.ca:
                raise InvalidAttestation("attestation leaf certificate cannot be a CA")
        except x509.ExtensionNotFound:
            pass  # Android leaves frequently omit Basic Constraints entirely.
        self._check_revocation(certs)
        return certs

    def _check_revocation(self, certs: list[x509.Certificate]) -> None:
        if self.status_source is None:
            return
        try:
            entries = (self.status_source() or {}).get("entries") or {}
        except Exception as error:
            if self.revocation_fail_open:
                log.warning(
                    "attestation status list unavailable error_type=%s",
                    type(error).__name__,
                )
                return
            raise InvalidAttestation("attestation status list is unavailable") from error
        for cert in certs:
            entry = entries.get(f"{cert.serial_number:x}")
            if entry:
                raise InvalidAttestation(
                    f"attestation certificate is revoked: {entry.get('reason', 'unknown')}"
                )

    def _check_key_properties(self, tee: dict[int, DerValue]) -> None:
        purposes = _int_set(tee[TAG_PURPOSE]) if TAG_PURPOSE in tee else set()
        if PURPOSE_SIGN not in purposes:
            raise InvalidAttestation("attested key may not sign")
        if TAG_ALGORITHM not in tee or _der_int(tee[TAG_ALGORITHM]) != ALGORITHM_EC:
            raise InvalidAttestation("attested key is not elliptic-curve")
        if TAG_EC_CURVE not in tee or _der_int(tee[TAG_EC_CURVE]) != EC_CURVE_P_256:
            raise InvalidAttestation("attested key is not P-256")
        digests = _int_set(tee[TAG_DIGEST]) if TAG_DIGEST in tee else set()
        if DIGEST_SHA_2_256 not in digests:
            raise InvalidAttestation("attested key cannot use SHA-256")
        if TAG_ORIGIN in tee and _der_int(tee[TAG_ORIGIN]) != ORIGIN_GENERATED:
            # An imported key was not born in the secure element, so it proves
            # nothing about the device holding it.
            raise InvalidAttestation("attested key was imported, not generated")

    def verify_attestation(
        self, attestation: bytes, *, key_id: str, client_data_hash: bytes
    ) -> AttestationResult:
        if len(client_data_hash) != 32:
            raise InvalidAttestation("client data hash must be SHA-256")
        # Wire format: SEQUENCE OF OCTET STRING, leaf first, each holding one
        # DER certificate -- what the client gets from KeyStore.getCertificateChain
        # with every entry passed through Certificate.getEncoded().
        outer = _der_one(attestation)
        if not outer.constructed:
            raise InvalidAttestation("attestation chain is not a sequence")
        chain = []
        for value in _der_values(outer.content, limit=8):
            if value.constructed or value.number != 4:
                raise InvalidAttestation("attestation chain holds a non-certificate")
            chain.append(value.content)
        certs = self._verify_chain(chain)
        leaf = certs[0]

        try:
            raw = leaf.extensions.get_extension_for_oid(ATTESTATION_OID).value.value
        except (x509.ExtensionNotFound, AttributeError) as exc:
            raise InvalidAttestation("leaf certificate has no key attestation") from exc
        description = _der_values(_der_one(raw).content, limit=16)
        if len(description) <= TEE_ENFORCED_INDEX:
            raise InvalidAttestation("KeyDescription is incomplete")

        challenge = description[CHALLENGE_INDEX].content
        if not hmac.compare_digest(challenge, client_data_hash):
            raise InvalidAttestation("attestation challenge does not match")

        security_level = _der_int(description[1])
        tee = _authorization_list(description[TEE_ENFORCED_INDEX])
        software = _authorization_list(description[SOFTWARE_ENFORCED_INDEX])

        # The application identity is recorded by the OS rather than the TEE, so
        # on real devices it lives in the software-enforced list.  It is inside
        # the TEE-signed blob and so cannot be edited afterwards, but an OS that
        # was already compromised when the key was generated could have lied
        # about it.  The verified-boot check below is what rules that out, which
        # is why production does not treat it as optional.
        app_id = tee.get(TAG_ATTESTATION_APPLICATION_ID) or software.get(
            TAG_ATTESTATION_APPLICATION_ID
        )
        if app_id is None:
            raise InvalidAttestation("attestation does not name an application")
        packages, digests = _application_id(app_id)
        if self.package_name not in packages:
            raise InvalidAttestation("attestation is for another application")
        if not any(
            any(hmac.compare_digest(seen, allowed) for allowed in self.signature_digests)
            for seen in digests
        ):
            raise InvalidAttestation("application is signed by an unknown certificate")

        self._check_key_properties(tee)

        if self.environment == "production":
            if security_level not in HARDWARE_SECURITY_LEVELS:
                raise InvalidAttestation("attestation is not hardware-backed")
            root = tee.get(TAG_ROOT_OF_TRUST)
            if root is None:
                raise InvalidAttestation("attestation carries no root of trust")
            trust = _root_of_trust(root)
            if trust.verified_boot_state != VERIFIED_BOOT_VERIFIED:
                raise InvalidAttestation("device did not boot a verified image")
            if not trust.device_locked:
                raise InvalidAttestation("device bootloader is unlocked")

        public_key = leaf.public_key()
        if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
            public_key.curve, ec.SECP256R1
        ):
            raise InvalidAttestation("attested key must be P-256")
        spki = public_key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        # Android has no "key ID is the hash of the key" rule of its own, so we
        # impose Apple's, keeping one invariant across both platforms.
        if not hmac.compare_digest(hashlib.sha256(spki).digest(), _b64decode(key_id)):
            raise InvalidAttestation("key ID is not the hash of the attested public key")

        return AttestationResult(spki, None, 0, self.environment, self.platform)

    def verify_assertion(
        self, assertion: bytes, *, public_key: bytes, client_data_hash: bytes,
        previous_counter: int,
    ) -> int:
        try:
            key = serialization.load_der_public_key(public_key)
        except ValueError as exc:
            raise AppAuthError("stored public key is invalid") from exc
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise AppAuthError("stored public key is not elliptic-curve")
        try:
            key.verify(assertion, client_data_hash, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise AppAuthError("invalid assertion signature") from exc
        # Keystore signatures carry no counter.  The single-use challenge is
        # what stops a replay here; returning the stored value keeps it still.
        return previous_counter
