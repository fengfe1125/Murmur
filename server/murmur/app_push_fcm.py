"""FCM HTTP v1 delivery for Android devices.

Mirrors :class:`murmur.app_push.APNsProvider` deliberately: same ``send``
signature, same two-attempt loop, same refusal to put the conversation into a
notification.  The differences are all in the wire format.

The OAuth2 access token is minted here from the service account key rather than
through ``google-auth``.  That library would pull in a large dependency tree for
one signed assertion, and Murmur keeps its dependency table deliberately short --
the same reason :meth:`APNsProvider._provider_token` hand-rolls its ES256 JWT.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .app_push import PushResult, PushTransport, _b64url
from .app_settings import AppSettings

log = logging.getLogger("murmur.app_push_fcm")

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# The token is spent, not the attempt.  SENDER_ID_MISMATCH means the token was
# minted for another Firebase project and will never work for ours.
DEAD_TOKEN_CODES = {"UNREGISTERED", "SENDER_ID_MISMATCH"}
# Permanent, but not necessarily the token's fault, so the row is retired
# without clearing the device's registration -- APNs treats its 400 the same way.
PERMANENT_CODES = DEAD_TOKEN_CODES | {"INVALID_ARGUMENT", "THIRD_PARTY_AUTH_ERROR"}


class FCMProvider:
    """Service-account authenticated FCM HTTP v1 provider with a token cache."""

    platform = "android"

    def __init__(
        self,
        *,
        project_id: str,
        client_email: str,
        private_key_pem: bytes,
        private_key_id: str = "",
        token_uri: str = TOKEN_URI,
        transport: PushTransport | None = None,
        on_invalid_token: Callable[[str], None] | None = None,
    ):
        if not project_id:
            raise RuntimeError("FCM requires a project ID")
        if not client_email:
            raise RuntimeError("FCM requires a service account client_email")
        self.project_id = project_id
        self.client_email = client_email
        self.private_key_id = private_key_id
        self.token_uri = token_uri
        if transport is None:
            from .app_push import HttpxPushTransport

            transport = HttpxPushTransport()
        self.transport = transport
        self.on_invalid_token = on_invalid_token
        try:
            key = serialization.load_pem_private_key(private_key_pem, password=None)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("could not load the FCM service account key") from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise RuntimeError("FCM service account key must be RSA")
        self._key = key
        self._cached_token: str | None = None
        self._token_expires_at = 0

    @classmethod
    def from_service_account(
        cls, path: str | Path, *, project_id: str | None = None, **kwargs
    ) -> FCMProvider:
        try:
            account = json.loads(Path(path).expanduser().read_bytes())
        except (OSError, ValueError) as exc:
            raise RuntimeError("could not read the FCM service account file") from exc
        if not isinstance(account, dict):
            raise RuntimeError("FCM service account file must be a JSON object")
        return cls(
            project_id=project_id or str(account.get("project_id") or ""),
            client_email=str(account.get("client_email") or ""),
            private_key_pem=str(account.get("private_key") or "").encode("utf-8"),
            private_key_id=str(account.get("private_key_id") or ""),
            token_uri=str(account.get("token_uri") or TOKEN_URI),
            **kwargs,
        )

    @classmethod
    def from_settings(
        cls, settings: AppSettings, *, transport: PushTransport | None = None,
        on_invalid_token: Callable[[str], None] | None = None,
    ) -> FCMProvider:
        settings.validate_fcm()
        return cls.from_service_account(
            settings.fcm_service_account_path,
            project_id=settings.fcm_project_id,
            transport=transport,
            on_invalid_token=on_invalid_token,
        )

    def _assertion(self, now: int) -> str:
        header = {"alg": "RS256", "typ": "JWT"}
        if self.private_key_id:
            header["kid"] = self.private_key_id
        head = _b64url(json.dumps(header, separators=(",", ":")).encode())
        claims = _b64url(json.dumps({
            "iss": self.client_email,
            "scope": SCOPE,
            "aud": self.token_uri,
            "iat": now,
            "exp": now + 3600,
        }, separators=(",", ":")).encode())
        signing_input = f"{head}.{claims}".encode("ascii")
        signature = self._key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{claims}.{_b64url(signature)}"

    def _access_token(self, now: int | None = None) -> str:
        now = int(time.time()) if now is None else int(now)
        if self._cached_token and now < self._token_expires_at:
            return self._cached_token
        response = self.transport.post(
            self.token_uri,
            headers={"content-type": "application/x-www-form-urlencoded"},
            content=urlencode({
                "grant_type": JWT_BEARER, "assertion": self._assertion(now),
            }).encode("ascii"),
        )
        if response.status_code != 200:
            # The assertion embeds no secret, but the response may name the
            # service account; keep the status only.
            raise RuntimeError(f"FCM token exchange failed status={response.status_code}")
        try:
            payload = response.json()
            token = str(payload["access_token"])
            lifetime = int(payload.get("expires_in") or 3600)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("FCM token exchange returned no access token") from exc
        # Renew a minute early so a token cannot expire mid-flight.
        self._token_expires_at = now + max(60, lifetime) - 60
        self._cached_token = token
        return token

    @staticmethod
    def payload(project_id: str, device_token: str, moment_id: str, preview: str) -> bytes:
        # Same restraint as APNs: the preview is a nudge, not the conversation.
        text = preview.strip()[:180]
        while True:
            body = json.dumps({"message": {
                "token": device_token,
                "notification": {"body": text},
                "data": {"moment_id": moment_id},
                "android": {
                    "priority": "HIGH",
                    "collapse_key": moment_id[:64],
                    # Tapping the tray notification opens MainActivity with the
                    # data payload (moment_id) as extras — the client's deep
                    # link (OPEN_MOMENT intent-filter) pulls that moment.
                    "notification": {"click_action": "OPEN_MOMENT"},
                },
            }}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) <= 4096:
                return body
            text = text[:-8]

    @staticmethod
    def _error_code(response) -> str:
        try:
            error = response.json().get("error") or {}
        except Exception:
            return "FCMError"
        for detail in error.get("details") or []:
            if isinstance(detail, dict) and detail.get("errorCode"):
                return str(detail["errorCode"])
        return str(error.get("status") or "FCMError")

    def send(self, device_token: str, *, moment_id: str, preview: str) -> PushResult:
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        for attempt in range(2):
            response = self.transport.post(
                url,
                headers={
                    "authorization": f"Bearer {self._access_token()}",
                    "content-type": "application/json; charset=utf-8",
                },
                content=self.payload(self.project_id, device_token, moment_id, preview),
            )
            if response.status_code == 200:
                return PushResult(True, 200)
            reason = self._error_code(response)
            if response.status_code == 401 and attempt == 0:
                self._cached_token = None
                continue
            if reason in DEAD_TOKEN_CODES and self.on_invalid_token:
                self.on_invalid_token(device_token)
            return PushResult(
                False, response.status_code, reason,
                permanent=reason in PERMANENT_CODES,
            )
        return PushResult(False, 401, "UNAUTHENTICATED")
