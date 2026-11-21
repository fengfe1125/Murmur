---
name: murmur-run
description: Build, test or run Murmur on the iOS Simulator. Use for simulator launch, screenshots, UI verification, or iOS unit/UI tests.
---

# Running Murmur

> 状态：现行规范｜适用：iOS 本地构建、测试与授权联调｜核验：2026-09-13｜依据：581b332 工程、开发脚本与测试入口静态核对

Choose the workflow the request needs. Build and test commands live in
[the iOS README](../../../apps/ios/MurmurApp/README.md#构建与测试).
Keep its project, scheme and test targets; select a currently available simulator
by UDID rather than assuming the example device name exists. Boot a suitable
available simulator when launch or UI testing needs one.

## Build or unit tests

Build or run the relevant `MurmurTests` without starting a backend. The server's
Python tests use `scripts/check/run_tests.py`, not this iOS workflow.

## UI verification

Prefer the existing fake API for deterministic UI checks. Run the relevant
`MurmurUITests`; `-only-testing:MurmurTests` does not run UI tests.
For interactive inspection, `--murmur-ui-testing` selects `UITestMurmurAPIClient`
and redirects transcript and archive storage to temporary UI-test directories.
`--murmur-reset-transcript` clears those test stores. Both flags are Debug-only;
see `apps/ios/MurmurApp/MurmurEnvironment.swift`.

Open a simulator panel when the available tool supports it. A missing panel does
not block build, tests or other available inspection methods. Verify the requested
UI behavior yourself and report any visual checks the available tools cannot perform.
Choose states and device widths using `murmur-ui`; test coverage is specific to the
selected tests and destination, not every screen or device.

## Backend integration

Use `scripts/dev/dev-app.sh` only when the task needs backend integration. Its
header documents the flags. The script requires an already-booted simulator.

- Local mode can create `.env.dev-app`, use the local database, start/reuse an API,
  build/install/launch the app, and create a reusable invitation. Preserve existing
  configuration and data; this is not a disposable test runner.
- VPS mode opens an SSH tunnel and reads the configured development token. Use only
  within the task's authorization and the repository's production connection rules.
  A missing development token does not authorize changing production authentication.
- Keep the development token stable: the simulator's Keychain identity is bound to
  it. The script supplies `SIMCTL_CHILD_MURMUR_DEV_BYPASS_TOKEN`; avoid exposing it.

Run this long-lived script in a managed background session. Its final `wait` keeps
an API or tunnel alive: completion means the backend is ready, the current build is
installed/launched, and the requested checks are finished, not that the script exits.
Keep the service alive while it is needed; stop only processes created by this task
that are no longer needed, leaving pre-existing services intact.

## Evidence boundaries

Simulator checks cannot establish real-device App Attest or APNs behavior.
Report build, test, install, launch and observed UI results separately; production
and signed-device acceptance require their own evidence.
