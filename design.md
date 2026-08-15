# Design — Murmur

Murmur is a native iOS app for a small invited audience. This file is the
locked visual and interaction system for every app screen. SwiftUI code must
consume named native tokens rather than inventing local colours, spacing, or
motion values.

## Product moment

- One job: send a photo, a short note, or both, then receive Murmur's current
  response.
- One visible moment: never render a transcript, conversation list, fake
  recents, or persisted chat history.
- The product should feel quiet, personal, and exact rather than conversational
  chrome wrapped around a generic chat client.

## Genre and structure

- Genre: editorial, with an austere native application voice.
- App macrostructure: Workbench — the selected image/current input and current
  response are the working surfaces.
- Compact width: one vertical workspace.
- Regular width: asymmetric two-column workspace, image/input on the leading
  side and current response on the trailing side.
- Top chrome: N9 edge-aligned minimal — Murmur at the leading edge, connection
  state and Settings at the trailing edge, no middle navigation row.
- Bottom/status treatment: Ft2 inline rule — one quiet status line, no tab bar
  or footer columns.
- Enrichment: none. The user's image and Murmur's words carry the screen.

## Native colour tokens

The values below describe the intended perceptual palette. SwiftUI provides
light/dark named colours with the same roles and keeps the same hue family.

- Paper: `oklch(97% 0.012 86)`
- Paper raised: `oklch(93.5% 0.018 86)`
- Ink: `oklch(22% 0.018 65)`
- Muted ink: `oklch(48% 0.020 70)`
- Rule: `oklch(82% 0.015 84)`
- Olive accent/focus: `oklch(49% 0.090 126)`
- Coral signal/error: `oklch(63% 0.140 35)`

Accent colour occupies no more than five percent of a view. There are no mesh
gradients, glass panels, coloured glows, or pure black/white surfaces.

## Typography

- Display and wordmark: New York/system serif, roman, bold only for hierarchy.
- Body and controls: SF Pro/system body.
- Dynamic Type is mandatory; no fixed text frames and no font below the system
  caption floor.
- Headings remain roman. Numbers that communicate status use monospaced digits.

## Space and shape

- Four-point scale: 4, 8, 12, 16, 24, 32, 40, 64 points.
- Touch targets: at least 44 by 44 points; primary controls target 50 points.
- Containment: one surface layer only. Do not nest bordered cards.
- Corners are restrained (12–18 points) and never used to turn every element
  into a pill.

## Motion and interaction

- Motion communicates only phase changes: a short opacity crossfade and a
  tactile press response. No scrolling choreography or decorative loops.
- Reduce Motion collapses spatial movement to an opacity change of at most
  150 ms.
- Focus, error, disabled, loading, success, cancellation, retry, and pressed
  states are explicit and do not rely on colour alone.
- Success is silent when the result is already visible. Failures say what
  failed and offer the next action.

## Shared copy voice

- Chinese-first, short, concrete verbs: “选一张照片”, “发送此刻”, “再试一次”.
- Never use fake timestamps, invented conversation copy, or celebratory toasts.
- “安静” is a valid Murmur response state, not an error and not a fabricated
  filler message.

## What every screen must share

- The same paper/ink/olive/coral token roles.
- The same display/body pairing and four-point space scale.
- The same edge-aligned top chrome and quiet status treatment.
- No locally persisted user photo, note, or response content.

