# Design — Murmur

> 状态：归档｜适用：历史追溯｜归档核验：2026-08-28｜依据：重组前文件快照。
> 以下保留当时描述、路径和判断，不代表当前能力、部署状态或新开发要求。现行说明见 [文档导航](../README.md)。

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
- Bounded animation only. A gesture-triggered animation with a known end (a
  KeyframeAnimator track, a spring) may redraw every frame while it runs; a
  resident per-frame redraw (TimelineView(.animation), always-on shader
  timelines) may not exist at all. The send-off dissolve in 当年今日 is the
  reference case: it runs once, on the photo card alone, and ends.
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

## 当年今日 (On This Day)

A history-reading feature, acknowledged here as a product decision rather than
slipped in as an implementation detail.

- The third floating disc in the top chrome opens it: one photo from this day
  in an earlier year, full screen. Down-swipe shows the next candidate;
  up-swipe carries the photo into a room of its own. One card at a time — a
  grid would make it a photo app.
- The shelf runs forward and never wraps. Past the last photo from this day it
  carries on with photos drawn at random from the album, and so does a day that
  is blank in every earlier year. Each card says which it is — 「去年的今天」 or
  「相册里翻到的」 — because dressing an ordinary Tuesday up as an anniversary is
  a lie told about someone's own memory. The one honest dead end left is a
  library with no photos in it at all.
- The library is read on-device, at browse time, through PhotoKit. Browsing
  pixels live in a memory-only NSCache and die with the view. No thumbnail,
  index, or "last viewed" marker is written anywhere.
- A file appears only when the person swipes a photo up: it enters the same
  temporary-upload lifecycle as any photo they picked themselves, under a
  prefix the cold-start sweeper knows, and is deleted on every terminal path.
- Photo permission is requested in context, with copy that says the truth:
  "Murmur 想在本机翻找你相册里同一天的旧照片；发送哪一张，由你决定。"
- Limited library access is treated as "the feature does not exist": the entry
  disc hides itself. Showing an empty shelf there would be a lie about what
  the person photographed that day.

## 照片房间 (the photo room)

Where a photo swiped up out of 当年今日 goes. It does not go into the
conversation: the conversation is for whatever is in front of you now, and an
old photo is a different kind of thing to bring up.

- The card comes apart into particles in the browser and the same particles
  come back together at the top of the room, so the two halves read as one
  motion rather than two screens. Both live inside one presentation; under
  Reduce Motion, or without the shader, the photo simply appears.
- The photo stays pinned at the top for the life of the screen. Everything
  below it is about that one picture.
- The server reads the image and opens with a guess at what the person came to
  say — 「这是……刚下过雨？」 — with three short openers under it. An opener is a
  door, not a message: one tap lifts it into the field, where the person decides
  whether it leaves. They close behind the first line.
- After that it is an ordinary exchange: Murmur catches what is said and asks
  back, drawing out what the photo means rather than describing it. Those turns
  carry no photo — the picture is already in Murmur's memory from the opening
  upload.
- The room's own scrollback is a working surface and ends with the screen. What
  survives is on the server, in Murmur's memory, which is the point of saying
  it. A second persisted history on the device is a product decision and has
  not been taken.
- The three openers appear here and nowhere else. The conversation used to
  carry them under a photo reply; it no longer does.
