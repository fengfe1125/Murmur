---
name: murmur-figma
description: Murmur token mapping. Use when translating Murmur screens or tokens between Figma and SwiftUI.
---

# Murmur in Figma

> 状态：现行规范｜适用：Murmur 与 Figma 互转｜核验：2026-09-13｜依据：581b332 本地主题与映射核对；未核验在线 Figma 文件

Load `/figma-swiftui` for the mechanics of either direction, and `/figma-use` before any
`use_figma` call. This skill carries only the Murmur-specific half — the naming
contract, so a round-trip lands on the same names it left with.

## Variables mirror `MurmurTheme`

One collection named `Murmur`, with `Light` and `Dark` modes. Those two modes are the
Figma equivalent of adaptive colours in code. Intentional fixed colours use the
same value in both modes.

| Figma variable | `MurmurTheme` member |
|---|---|
| `color/paper` | `paper` |
| `color/paper-raised` | `raisedPaper` |
| `color/ink` | `ink` |
| `color/ink-secondary` | `secondaryInk` |
| `color/rule` | `rule` |
| `color/accent` | `accent` |
| `color/accent-ink` | `accentInk` |
| `color/on-accent` | `onAccent` |
| `color/coral` | `coral` |
| `color/bubble-outgoing` | `outgoingBubble` |
| `color/glass` | `glassTint` |
| `color/glass-edge` | `glassEdge` |
| `size/disc` | `disc` |
| `size/disc-floating` | `floatingDisc` |
| `size/page-inset` | `pageInset` |
| `size/content-width` | `contentWidth` |
| `size/tab-bar` | — (the bar hugs its content) |
| `radius/corner` | `corner` |
| `radius/glass` | `glassCorner` |

`olive` is gone. A file that still carries it is from before the cyan.

The recorded target file is [Murmur 2.0](https://www.figma.com/design/mrMevXBneAjhgCM3MKDTbC).

Map spacing used by the requested screen, including 20-point insets where present;
read actual values from `apps/ios/MurmurApp/MurmurChatView.swift`.

Read values from `MurmurTheme` and check this name mapping when synchronizing.
Update both sides only when Figma synchronization is part of the requested task;
ordinary SwiftUI changes do not require opening or editing Figma. Preserve design
objects outside the requested scope, including variables without Swift counterparts.

## Type

Two ramps only: `display` is the system serif at semibold, `body` is the system sans.
Figma text styles name the SwiftUI `Font.TextStyle` they came from — `display/title2`,
`body/callout` — because the SwiftUI side takes a text style rather than a point size,
and the round-trip has to preserve Dynamic Type.

## Coming from Figma into SwiftUI

Default to the existing `murmur-ui` system: paper and ink surfaces, filled accent under ~5%, one
surface layer, restrained corners, Dynamic Type. Glass is part of the system now, but
only for chrome that floats — the top bar, the composer, the tab bar — and always
through `murmurGlass()`, never hand-rolled per screen. A file that arrives carrying a
mesh gradient, coloured glow, pure black or white surface, glass on ordinary content, or
a third accent has drifted — implement the Murmur equivalent and tell the user exactly
what you substituted. If the user explicitly requests a visual redesign, follow
the agreed new direction while preserving accessibility requirements.

Contrast is not negotiable in either direction: `accent` never carries small text or a
glyph (2.2:1 on paper — use `accentInk`), and nothing white ever sits on `accent`
(2.4:1 — use `onAccent`). A comp that does either is wrong, however it was produced.
