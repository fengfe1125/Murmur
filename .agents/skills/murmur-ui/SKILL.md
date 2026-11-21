---
name: murmur-ui
description: Murmur visual rules. Use when changing iOS layout, styling, animation, accessibility or visible copy under apps/ios/.
---

# Murmur UI

> 状态：现行规范｜适用：Murmur iOS 界面｜核验：2026-09-22｜依据：用户确认的全 App 原生 UI 重构；具体运行验收见 docs/validation/native-ui-2026-09-22.md

## Native components

Use system `TabView`, `NavigationStack`, toolbars, `Form`, `List`, menus,
confirmation dialogs and sheets. Keep the four destinations and existing capability
gates. Each tab owns its navigation state; session sync and room polling stay above
individual tab lifecycles so switching tabs cannot duplicate background work.

`MurmurTheme` in `apps/ios/MurmurApp/MurmurChatView.swift` maps shared surfaces and
text to system semantic colors and Dynamic Type fonts. Keep the cyan brand accent;
use the darker `accentInk` for readable controls and `onAccent` on cyan message
bubbles. Do not restore cream paper, serif display fonts, custom tab capsules or
forced glass backgrounds. Let the OS choose the native chrome for its version.

Chat bubbles and photo content may keep dedicated layout where no system component
fits. Use a thin `UICalendarView` wrapper for marked dates; clip its native paging
content and preserve local-day semantics, date marks and accessibility.

## Keyboard and motion

Native safe areas position composers. Do not add keyboard-height padding or a
second animation on top of system keyboard avoidance. Measured keyboard geometry
is observation only, not a replacement layout engine. Verify the actual composer
position and visible message after focus, dismissal, refocus and sending.

Navigation, presentation and control feedback use system behavior. Custom content
transitions are brief opacity changes; no particle shaders, repeating decorative
motion or press-scale styles. Respect `accessibilityReduceMotion` in the same change
that introduces motion. Streaming text must not restart whole-list animations.

## Accessibility and copy

Use Dynamic Type without fixed text-bearing heights. Custom interactive content
needs a 44-point target. Keep standard system control sizing unless real testing
shows a reachability issue. Error, loading, retry and disabled states need readable
labels in addition to color. Counting numbers use monospaced digits.

Chinese first: concrete short verbs. Failures explain what failed and expose the
next action. Preserve distinctions among chat history, photo-room archives, daily
reviews and user-confirmed diaries; current behavior is in `docs/product/current-state.md`.

## Verification

Use `murmur-run` for builds and isolated fake-API UI checks. Native controls may have
a different accessibility hierarchy from custom controls; test behavior, not a
hand-drawn shape. On system tabs use an unambiguous first matching native item.

For shared theme or navigation changes, verify phone and iPad, portrait and
landscape, light/dark, Accessibility XXXL and Reduce Motion. Cover draft retention,
archive navigation, keyboard geometry, hidden-tab accessibility and capability
gates. Report build, automated tests, visual inspection and unavailable OS/device
coverage separately. Do not substitute simulator results for production acceptance.
