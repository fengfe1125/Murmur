"""Centralised configuration for the first-party App API and worker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _path(value: str | None, default: Path) -> Path:
    return Path(value).expanduser() if value else default


def _digests(value: str | None) -> tuple[bytes, ...]:
    """Parse comma-separated SHA-256 hex digests, colons and spacing tolerated."""
    if not value:
        return ()
    digests = []
    for item in value.replace(":", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            raw = bytes.fromhex(item)
        except ValueError as exc:
            raise RuntimeError(f"invalid signing certificate digest: {item!r}") from exc
        if len(raw) != 32:
            raise RuntimeError("signing certificate digests must be SHA-256")
        digests.append(raw)
    return tuple(digests)


@dataclass(frozen=True)
class AppSettings:
    db_path: Path
    memory_db_path: Path
    data_root: Path
    upload_dir: Path
    public_base_url: str
    app_id: str
    team_id: str
    attest_mode: str
    attest_root_path: Path | None
    allow_development: bool
    development_token: str | None
    apns_key_path: Path | None
    apns_key_id: str | None
    apns_team_id: str | None
    apns_topic: str
    apns_environment: str
    fcm_project_id: str | None = None
    fcm_service_account_path: Path | None = None
    android_enabled: bool = False
    android_package: str | None = None
    android_signature_digests: tuple[bytes, ...] = ()
    attest_google_root_path: Path | None = None
    attest_revocation_fail_open: bool = False
    max_image_bytes: int = 25 * 1024 * 1024
    max_body_bytes: int = 26 * 1024 * 1024
    max_json_body_bytes: int = 256 * 1024
    max_concurrent_uploads: int = 2
    body_read_timeout_seconds: float = 180.0
    body_idle_timeout_seconds: float = 15.0
    min_body_bytes_per_second: int = 16 * 1024
    body_speed_grace_seconds: float = 10.0
    body_gate_timeout_seconds: float = 1.0
    max_image_pixels: int = 80_000_000
    max_note_chars: int = 2_000
    requests_per_minute: int = 30
    rate_limit_max_keys: int = 4096
    event_ttl_hours: int = 24
    timezone: ZoneInfo = ZoneInfo("Asia/Shanghai")

    @property
    def production(self) -> bool:
        return self.attest_mode == "production"

    @classmethod
    def from_env(cls, cfg=None) -> AppSettings:
        """Load every app setting once; request handlers never read the environment.

        ``cfg`` is optional so the existing ``Config`` may supply the memory DB and
        timezone without coupling this module to new fields in that dataclass.
        """
        cfg_db = Path(getattr(cfg, "db_path", "murmur.db")).expanduser()
        db_path = _path(os.getenv("MURMUR_APP_DB"), cfg_db)
        memory_db = _path(os.getenv("MURMUR_APP_MEMORY_DB"), cfg_db)
        data_root = _path(os.getenv("MURMUR_APP_DATA_ROOT"), db_path.parent)
        team_id = os.getenv("MURMUR_APP_TEAM_ID", "").strip()
        bundle_id = os.getenv("MURMUR_APP_BUNDLE_ID", "com.sakura.Murmur").strip()
        app_id = os.getenv("MURMUR_APP_ID", "").strip() or (
            f"{team_id}.{bundle_id}" if team_id else ""
        )
        root_override = os.getenv("MURMUR_APP_ATTEST_ROOT_CA")
        apns_path = os.getenv("MURMUR_APP_APNS_KEY_PATH")
        fcm_account = os.getenv("MURMUR_APP_FCM_SERVICE_ACCOUNT_PATH")
        google_root = os.getenv("MURMUR_APP_ATTEST_GOOGLE_ROOT_CA")
        attest_mode = os.getenv("MURMUR_APP_ATTEST_MODE", "production").strip().lower()
        tz = getattr(cfg, "tz", None) or ZoneInfo(
            os.getenv("MURMUR_APP_TIMEZONE", "Asia/Shanghai")
        )
        return cls(
            db_path=db_path,
            memory_db_path=memory_db,
            data_root=data_root,
            upload_dir=_path(os.getenv("MURMUR_APP_TEMP_DIR"), data_root / "app-uploads"),
            public_base_url=os.getenv("MURMUR_APP_BASE_URL", "http://127.0.0.1:8766").rstrip("/"),
            app_id=app_id,
            team_id=team_id,
            attest_mode=attest_mode,
            attest_root_path=Path(root_override).expanduser() if root_override else None,
            allow_development=_bool(os.getenv("MURMUR_APP_ALLOW_DEVELOPMENT")),
            development_token=os.getenv("MURMUR_APP_DEVELOPMENT_TOKEN") or None,
            apns_key_path=Path(apns_path).expanduser() if apns_path else None,
            apns_key_id=os.getenv("MURMUR_APP_APNS_KEY_ID") or None,
            apns_team_id=os.getenv("MURMUR_APP_APNS_TEAM_ID") or team_id or None,
            apns_topic=os.getenv("MURMUR_APP_APNS_TOPIC", bundle_id),
            apns_environment=os.getenv("MURMUR_APP_APNS_ENVIRONMENT", attest_mode).lower(),
            fcm_project_id=os.getenv("MURMUR_APP_FCM_PROJECT_ID") or None,
            fcm_service_account_path=(
                Path(fcm_account).expanduser() if fcm_account else None
            ),
            android_enabled=_bool(os.getenv("MURMUR_APP_ANDROID_ENABLED")),
            android_package=os.getenv("MURMUR_APP_ANDROID_PACKAGE") or None,
            android_signature_digests=_digests(
                os.getenv("MURMUR_APP_ANDROID_SIGNING_DIGESTS")
            ),
            attest_google_root_path=(
                Path(google_root).expanduser() if google_root else None
            ),
            attest_revocation_fail_open=_bool(
                os.getenv("MURMUR_APP_ATTEST_REVOCATION_FAIL_OPEN")
            ),
            max_image_bytes=int(os.getenv("MURMUR_APP_MAX_IMAGE_BYTES", 25 * 1024 * 1024)),
            max_body_bytes=int(os.getenv("MURMUR_APP_MAX_BODY_BYTES", 26 * 1024 * 1024)),
            max_json_body_bytes=int(os.getenv(
                "MURMUR_APP_MAX_JSON_BODY_BYTES", 256 * 1024
            )),
            max_concurrent_uploads=int(os.getenv(
                "MURMUR_APP_MAX_CONCURRENT_UPLOADS", "2"
            )),
            body_read_timeout_seconds=float(os.getenv(
                "MURMUR_APP_BODY_READ_TIMEOUT_SECONDS", "180"
            )),
            body_idle_timeout_seconds=float(os.getenv(
                "MURMUR_APP_BODY_IDLE_TIMEOUT_SECONDS", "15"
            )),
            min_body_bytes_per_second=int(os.getenv(
                "MURMUR_APP_MIN_BODY_BYTES_PER_SECOND", str(16 * 1024)
            )),
            body_speed_grace_seconds=float(os.getenv(
                "MURMUR_APP_BODY_SPEED_GRACE_SECONDS", "10"
            )),
            body_gate_timeout_seconds=float(os.getenv(
                "MURMUR_APP_BODY_GATE_TIMEOUT_SECONDS", "1"
            )),
            max_image_pixels=int(os.getenv(
                "MURMUR_APP_MAX_IMAGE_PIXELS", "80000000"
            )),
            max_note_chars=int(os.getenv("MURMUR_APP_MAX_NOTE_CHARS", "2000")),
            requests_per_minute=int(os.getenv("MURMUR_APP_RATE_LIMIT_PER_MINUTE", "30")),
            rate_limit_max_keys=int(os.getenv(
                "MURMUR_APP_RATE_LIMIT_MAX_KEYS", "4096"
            )),
            event_ttl_hours=int(os.getenv("MURMUR_APP_EVENT_TTL_HOURS", "24")),
            timezone=tz,
        )

    def validate(self) -> None:
        if self.attest_mode not in {"production", "development"}:
            raise RuntimeError("MURMUR_APP_ATTEST_MODE must be production or development")
        if self.production:
            if self.allow_development or self.development_token:
                raise RuntimeError("development authentication is forbidden in production")
            if not self.app_id or not self.team_id:
                raise RuntimeError("production App Attest needs MURMUR_APP_ID and TEAM_ID")
            if not self.public_base_url.startswith("https://"):
                raise RuntimeError("production App API requires an HTTPS public base URL")
            if self.apns_environment != "production":
                raise RuntimeError("production App API cannot use the APNs sandbox")
            if self.android_enabled:
                # Half-configured Android would accept enrolments it cannot
                # verify, or verify devices it can never notify.
                self.validate_android()
                self.validate_fcm()
        else:
            if not self.allow_development:
                raise RuntimeError("development mode must be explicitly enabled")
            if not self.development_token or len(self.development_token) < 24:
                raise RuntimeError("development mode needs a random token of at least 24 chars")
        if self.apns_environment not in {"development", "production"}:
            raise RuntimeError("APNs environment must be development or production")
        if self.max_image_bytes <= 0 or self.max_body_bytes <= self.max_image_bytes:
            raise RuntimeError("invalid app upload limits")
        if not 1024 <= self.max_json_body_bytes <= self.max_body_bytes:
            raise RuntimeError("invalid App API JSON body limit")
        if self.max_concurrent_uploads < 1:
            raise RuntimeError("App API body concurrency must be positive")
        if (self.body_read_timeout_seconds <= 0 or self.body_idle_timeout_seconds <= 0
                or self.body_gate_timeout_seconds <= 0
                or self.body_speed_grace_seconds <= 0
                or self.min_body_bytes_per_second <= 0):
            raise RuntimeError("App API body timeouts must be positive")
        if self.max_image_pixels < 48_000_000:
            raise RuntimeError("App image pixel limit must accept 48MP photos")
        if self.requests_per_minute < 1 or self.rate_limit_max_keys < 128:
            raise RuntimeError("invalid App API rate limiter settings")

    def validate_android(self) -> None:
        missing = [
            name for name, value in (
                ("MURMUR_APP_ANDROID_PACKAGE", self.android_package),
                ("MURMUR_APP_ANDROID_SIGNING_DIGESTS", self.android_signature_digests),
                ("MURMUR_APP_ATTEST_GOOGLE_ROOT_CA", self.attest_google_root_path),
            ) if not value
        ]
        if missing:
            raise RuntimeError("Android attestation missing: " + ", ".join(missing))
        if not self.attest_google_root_path or not self.attest_google_root_path.is_file():
            raise RuntimeError("Android attestation root certificate does not exist")

    @property
    def fcm_configured(self) -> bool:
        return bool(self.fcm_project_id and self.fcm_service_account_path)

    def validate_fcm(self) -> None:
        missing = [
            name for name, value in (
                ("MURMUR_APP_FCM_PROJECT_ID", self.fcm_project_id),
                ("MURMUR_APP_FCM_SERVICE_ACCOUNT_PATH", self.fcm_service_account_path),
            ) if not value
        ]
        if missing:
            raise RuntimeError("FCM configuration missing: " + ", ".join(missing))
        if not self.fcm_service_account_path or not self.fcm_service_account_path.is_file():
            raise RuntimeError("FCM service account file does not exist")

    def validate_apns(self) -> None:
        missing = [
            name for name, value in (
                ("MURMUR_APP_APNS_KEY_PATH", self.apns_key_path),
                ("MURMUR_APP_APNS_KEY_ID", self.apns_key_id),
                ("MURMUR_APP_APNS_TEAM_ID", self.apns_team_id),
                ("MURMUR_APP_APNS_TOPIC", self.apns_topic),
            ) if not value
        ]
        if missing:
            raise RuntimeError("APNs configuration missing: " + ", ".join(missing))
        if not self.apns_key_path or not self.apns_key_path.is_file():
            raise RuntimeError("APNs signing key does not exist")
