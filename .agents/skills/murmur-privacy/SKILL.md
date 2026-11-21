---
name: murmur-privacy
description: Murmur data boundaries. Use when changing photo/location handling, uploads, storage, identity, notifications or release authentication.
---

# Murmur data invariants

> 状态：现行规范｜适用：Murmur 数据与认证边界｜核验：2026-09-13｜依据：581b332 存储、地点与客户端代码静态核对

These are promises `apps/ios/MurmurApp/README.md` and `PrivacyInfo.xcprivacy` already make to the
person using the app. Breaking one quietly is worse than a bug, because the declaration
goes on claiming it holds. Account for every rule below that your change touches, and
name the ones you checked.

## The original photo

The full-resolution original is temporary. It is deleted on **every** terminal path —
completion, quiet ending, cancellation, and non-retryable failure — and cold start
sweeps whatever a crash left behind. Adding a new way for a send to end means adding its
deletion in the same commit: enumerate the terminal paths in `MurmurSessionModel` and
check each one rather than trusting the happy path.

Originals and multipart bodies use complete file protection.

What survives on the server: a compressed, EXIF-stripped preview, text memory, and an
anonymised place fingerprint. Raw GPS coordinates do not.

The app may transit precise EXIF location, which is exactly why `PrivacyInfo.xcprivacy`
declares it. Changing what location data the app touches means updating that file in the
same change.

## Where a photo was taken

Location now has two layers, and they are not interchangeable:

- **The place name.** Reverse-geocoded on device (`SystemPlaceLookup`, `CLGeocoder`) at the
  moment a photo is actually sent — never while a card is merely on screen, which would
  spend Apple a coordinate for every photo the person only scrolled past. It goes into the
  model's context, because 「是不是又去那家店了」 is not information the picture holds. It is
  **not** written to `entries`: there is no named location history in the database.
- **The fingerprint.** `spot_key` in `server/murmur/moment.py`, GPS rounded to ~110m. Anonymous,
  local, and the only layer that is stored. It answers "how many times has he been here",
  nothing else.

The server does no geocoding of its own, sends no coordinate to any map service,
and does not persist raw latitude or longitude. Device-side geocoding is limited
to the photo the user sends, not browsing the library.

Because a re-encoded JPEG has no EXIF, 当年今日 states these facts explicitly in a
`provenance` multipart field rather than letting the server infer them from the image.
That field is as temporary as the original: `app_moments.provenance` is cleared on every
terminal path, alongside `image_path`.

## Release-build gates

`MurmurEnvironment.makeAPIClient()` holds two hard gates, both `#if !DEBUG`: a release
build carrying any development bypass token hits `fatalError`, and a non-HTTPS base URL
yields `UnavailableMurmurAPIClient`. Debug builds — on device too, not just the
Simulator — may use `DevelopmentAuthenticator`; release always uses App Attest. Keep
both gates intact when touching client construction.

## Identity and notifications

- Three devices per invited user; settings lists them and can revoke.
- `/v1/enrollments/recover` restores a binding when the first enrolment response is
  lost, without consuming another invite code or re-attesting the same key.
- The APNs token syncs only once the system permission is granted. Undetermined and
  denied both write `null`.

## History

Chat transcripts persist on device and restore on cold launch. Photo rooms use a
separate local archive. Maintaining these existing behaviors needs no new product
decision. Expanding retention, adding cross-device sync or server history queries
requires an explicit product/privacy decision unless already authorized.

The server retains private memory but offers no client chat-history query.
Clearing local chat or photo-room archives does not erase server memory; account
deletion follows its separate existing erase flow. See
[the data lifecycle](../../../docs/architecture/data-lifecycle.md).
