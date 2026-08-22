// Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4
// Hallmark · native app · genre: editorial · macrostructure: Workbench · theme: Garden · chrome: N9 · status: Ft2
import PhotosUI
import SwiftUI
import UIKit

@MainActor
enum MurmurTheme {
    static let paper = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.08, green: 0.08, blue: 0.07, alpha: 1)
            : UIColor(red: 0.96, green: 0.95, blue: 0.91, alpha: 1)
    })
    static let raisedPaper = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.12, green: 0.12, blue: 0.10, alpha: 1)
            : UIColor(red: 0.99, green: 0.98, blue: 0.95, alpha: 1)
    })
    static let ink = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.92, green: 0.91, blue: 0.86, alpha: 1)
            : UIColor(red: 0.12, green: 0.12, blue: 0.10, alpha: 1)
    })
    static let secondaryInk = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.62, green: 0.62, blue: 0.56, alpha: 1)
            : UIColor(red: 0.38, green: 0.38, blue: 0.33, alpha: 1)
    })
    static let rule = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.24, green: 0.24, blue: 0.20, alpha: 1)
            : UIColor(red: 0.81, green: 0.80, blue: 0.73, alpha: 1)
    })
    static let olive = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.66, green: 0.70, blue: 0.47, alpha: 1)
            : UIColor(red: 0.32, green: 0.37, blue: 0.18, alpha: 1)
    })
    static let coral = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.93, green: 0.53, blue: 0.43, alpha: 1)
            : UIColor(red: 0.76, green: 0.27, blue: 0.20, alpha: 1)
    })
    /// Outgoing chat bubbles only.  Kept separate from `olive`, which is the
    /// app-wide tint, so softening the bubble does not wash out every control.
    /// Both values stay above 4.5:1 against the white bubble text; the dark
    /// variant deepens rather than lightens, because the light olive used as a
    /// tint there only reaches 2.3:1 behind white.
    static let outgoingBubble = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.26, green: 0.33, blue: 0.18, alpha: 1)
            : UIColor(red: 0.42, green: 0.48, blue: 0.25, alpha: 1)
    })

    /// One diameter for every standalone icon control, so the chrome reads
    /// as one family: the toolbar mark, the gear, add-photo and send.
    static let disc: CGFloat = 36
    /// The free-standing discs at the top of the screen.  They carry their own
    /// background, so the drawn circle and the tap target are the same box and
    /// it has to clear 44pt on its own.
    static let floatingDisc: CGFloat = 44
    static let pageInset: CGFloat = 20
    static let contentWidth: CGFloat = 1_080
    static let corner: CGFloat = 18

    static func display(_ style: Font.TextStyle) -> Font {
        .system(style, design: .serif, weight: .semibold)
    }

    static func body(_ style: Font.TextStyle = .body, weight: Font.Weight = .regular) -> Font {
        .system(style, design: .default, weight: weight)
    }
}

struct MurmurChatView: View {
    @ObservedObject var model: MurmurSessionModel
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @State private var showSettings = false
    @State private var showCamera = false
    @State private var showOnThisDay = false
    @StateObject private var onThisDay = OnThisDayModel()
    @Environment(\.scenePhase) private var scenePhase
    @State private var topChromeHeight: CGFloat = 0
    @State private var topFadeHeight: CGFloat = 0
    /// Ties the settings sheet to the gear it comes from.  See the transition
    /// on the sheet below.
    @Namespace private var settingsZoom

    var body: some View {
        // The chrome floats: the conversation owns the whole screen and the two
        // discs sit on top of it, rather than a band that pushes the chat down.
        // `safeAreaPadding` is what keeps that honest — the content is inset by
        // exactly the height of the discs, so at rest nothing is behind them,
        // and scrolling passes the conversation under two small circles instead
        // of under a full-width slab.
        // The VStack is load-bearing: left to itself a ScrollView at the root
        // of the scene draws all the way up behind the status bar, and the
        // conversation ends up tangled in the clock.  Wrapped, its frame stops
        // at the safe area, and the discs float inside that frame.
        VStack(spacing: 0) {
            Group {
                if model.connection == .checking {
                    ConnectionLoadingView()
                } else if model.identity == nil {
                    EnrollmentView(model: model)
                } else if model.requiresDeviceReconnect {
                    DeviceReconnectView(model: model)
                } else {
                    MomentWorkbench(model: model, showCamera: $showCamera)
                }
            }
            .safeAreaPadding(.top, topChromeHeight)
        }
        // A scroll view draws all the way up behind the status bar whatever
        // frame it is given, and a bubble tangled in the clock is worse than
        // either problem this is trying to solve.  Fading is the way to keep
        // the conversation full-height without laying anything over it: the
        // text dissolves as it passes the discs instead of being cut by a bar.
        .mask(alignment: .top) {
            VStack(spacing: 0) {
                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0),
                        .init(color: .clear, location: 0.42),
                        .init(color: .black, location: 1)
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .frame(height: max(topFadeHeight, 1))
                Rectangle().fill(.black)
            }
            .ignoresSafeArea()
        }
        .overlay(alignment: .top) {
            MurmurTopChrome(
                connection: model.connection,
                showsSettings: model.identity != nil,
                showsOnThisDay: model.identity != nil && onThisDay.entryVisible,
                settingsZoom: settingsZoom,
                onSettings: { showSettings = true },
                onOnThisDay: { showOnThisDay = true }
            )
            .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { frame in
                topChromeHeight = frame.height
                // Where the discs end in the window is where the chat becomes
                // fully legible again.
                topFadeHeight = frame.maxY
            }
        }
        .background(MurmurTheme.paper.ignoresSafeArea())
        // The gear grows under the finger exactly as the mark does — measured
        // at the same 1.36× — and then nobody sees it, because presenting a
        // sheet scales the whole screen *down* behind it in the same breath.
        // The press was not weaker; it was overrun.  Zooming the sheet out of
        // the gear itself is what lets it finish: the disc keeps growing, into
        // the sheet, instead of being shrunk away mid-spring.
        .sheet(isPresented: $showSettings) {
            MurmurSettingsView(model: model)
                .environmentObject(notifications)
                .navigationTransition(.zoom(sourceID: Self.settingsSource, in: settingsZoom))
        }
        .sheet(isPresented: $showCamera) {
            CameraPicker(
                onCaptureFile: { model.preparePhoto(at: $0) },
                onCaptureImage: { model.prepareCapturedPhoto($0) }
            )
            .ignoresSafeArea()
        }
        .fullScreenCover(isPresented: $showOnThisDay) {
            // The photo swiped up here does not land in the composer.  It opens
            // a room of its own, where the server reads it and the exchange is
            // about that one picture — see `PhotoRoomView`.
            OnThisDayFlowView(model: onThisDay) { image in
                model.makePhotoRoom(image: image)
            }
        }
        // The disc hides itself under .limited, so the chrome has to re-ask
        // whenever the app comes back — that is when a settings change lands.
        .task { await onThisDay.refreshAuthorization() }
        .onChange(of: scenePhase, initial: false) { _, phase in
            guard phase == .active else { return }
            Task { await onThisDay.refreshAuthorization() }
        }
        .tint(MurmurTheme.olive)
    }

    fileprivate static let settingsSource = "murmur-settings-disc"
}

/// Independent discs floating over the transcript: the mark, 当年今日 and the
/// gear.  Nothing behind them is painted, so the only thing between the reader
/// and the conversation is the 44pt of each disc.
private struct MurmurTopChrome: View {
    let connection: MurmurConnectionState
    let showsSettings: Bool
    let showsOnThisDay: Bool
    let settingsZoom: Namespace.ID
    let onSettings: () -> Void
    let onOnThisDay: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            // No action of its own — the mark answers a press the way the
            // gear does, and that is all.
            Button {} label: {
                MurmurFloatingDisc { MurmurMark(size: 38) }
            }
            .murmurDiscButtonStyle()
            .accessibilityLabel("Murmur")
            Spacer(minLength: 0)
            // "已连接" is the normal case and just adds noise; a broken
            // connection still has to be visible.
            if connection != .connected {
                Text(connection.label)
                    .font(MurmurTheme.body(.caption2, weight: .medium))
                    .foregroundStyle(connectionCaptionColor)
                    .padding(.horizontal, 10)
                    .frame(height: 28)
                    .background(MurmurTheme.raisedPaper, in: Capsule())
                    .overlay { Capsule().stroke(MurmurTheme.rule, lineWidth: 1) }
            }
            if showsOnThisDay {
                // The third disc belongs to the top chrome, not the composer:
                // the composer's row is "one action at a time", and browsing
                // old photos is not an act of composing.
                Button(action: onOnThisDay) {
                    MurmurFloatingDisc {
                        Image(systemName: "memories")
                            .font(.system(size: 20, weight: .regular))
                            .foregroundStyle(MurmurTheme.ink)
                    }
                }
                .murmurDiscButtonStyle()
                .accessibilityLabel("当年今日")
                .accessibilityIdentifier("onthisday-button")
            }
            if showsSettings {
                Button(action: onSettings) {
                    MurmurFloatingDisc {
                        Image(systemName: "gearshape")
                            .font(.system(size: 23, weight: .regular))
                            .foregroundStyle(MurmurTheme.ink)
                    }
                }
                .murmurDiscButtonStyle()
                // The disc the settings sheet grows out of, and shrinks back
                // into.  On the whole button, so the sheet leaves from the
                // same 44pt circle the finger pressed.
                .matchedTransitionSource(id: MurmurChatView.settingsSource, in: settingsZoom)
                .accessibilityLabel("设置，\(connection.label)")
                .accessibilityIdentifier("settings-button")
            }
        }
        .frame(maxWidth: MurmurTheme.contentWidth)
        .padding(.horizontal, MurmurTheme.pageInset)
        .padding(.top, 6)
        .padding(.bottom, 8)
        .frame(maxWidth: .infinity)
    }

    private var connectionCaptionColor: Color {
        switch connection {
        case .connected, .checking: MurmurTheme.secondaryInk
        case .needsEnrollment, .offline: MurmurTheme.coral
        }
    }
}

/// Which build this device is actually running.
///
/// The app is side-loaded, and `CFBundleVersion` never moves between builds, so
/// there is otherwise no way to tell a fresh install from a stale one — the
/// question "did the update land" cannot be answered by looking at the screen.
/// The executable's own modification date is the build's timestamp.
enum MurmurBuild {
    static let summary: String = {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "—"
        let build = info?["CFBundleVersion"] as? String ?? "—"
        return "\(version) (\(build)) · \(stamp)"
    }()

    private static var stamp: String {
        guard let url = Bundle.main.executableURL,
              let date = try? url.resourceValues(forKeys: [.contentModificationDateKey])
                  .contentModificationDate
        else { return "构建时间未知" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_Hans_CN")
        formatter.dateFormat = "MM-dd HH:mm"
        return formatter.string(from: date)
    }
}

/// A floating 44pt control: the whole disc is both the drawn shape and the
/// tap target, which is the part a navigation bar would not give up.
private struct MurmurDisc<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .frame(width: MurmurTheme.floatingDisc, height: MurmurTheme.floatingDisc)
            .background(MurmurTheme.raisedPaper, in: Circle())
            .overlay { Circle().strokeBorder(MurmurTheme.rule, lineWidth: 1) }
            .shadow(color: MurmurTheme.ink.opacity(0.08), radius: 6, y: 2)
            .contentShape(Circle())
    }
}

/// The face of a floating disc.  On iOS 26 the system's glass draws the
/// disc — applied as an effect on the exact 44pt circle, because the glass
/// *button style* sizes its capsule to its own metrics and dwarfs the icon
/// inside — and before that the drawn paper disc does it.
private struct MurmurFloatingDisc<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        if #available(iOS 26.0, *) {
            content
                .frame(width: MurmurTheme.floatingDisc, height: MurmurTheme.floatingDisc)
                .glassEffect(.regular.interactive(), in: Circle())
                .contentShape(Circle())
        } else {
            MurmurDisc { content }
        }
    }
}

extension View {
    /// The floating discs' press behaviour.  On iOS 26 the interactive glass
    /// supplies all of it — the finger's light, the grow, the spring home —
    /// so the button itself keeps quiet and lets it.  Before that, the
    /// plain press style is all there is.
    @ViewBuilder
    fileprivate func murmurDiscButtonStyle() -> some View {
        if #available(iOS 26.0, *) {
            self.buttonStyle(MurmurQuietStyle())
        } else {
            self.buttonStyle(MurmurPressStyle())
        }
    }
}

/// A button with no opinions: the interactive glass supplies all of the
/// press feedback, and a second one from the style would double it.
private struct MurmurQuietStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View { configuration.label }
}

private struct DeviceReconnectView: View {
    @ObservedObject var model: MurmurSessionModel
    @State private var confirmReset = false

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "iphone.slash")
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(MurmurTheme.coral)
                .accessibilityHidden(true)
            Text("这台设备需要重新连接。")
                .font(MurmurTheme.display(.largeTitle))
                .foregroundStyle(MurmurTheme.ink)
                .accessibilityAddTraits(.isHeader)
            Text("本机的 App Attest 安全身份已经失效。重置只移除本机绑定，不会删除 Murmur 的账号或记忆。")
                .font(MurmurTheme.body(.body))
                .foregroundStyle(MurmurTheme.secondaryInk)
                .fixedSize(horizontal: false, vertical: true)
            Button("重新连接此设备", role: .destructive) { confirmReset = true }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .frame(minHeight: 44)
                .accessibilityIdentifier("reset-device-identity")
            Text("重置后，请向管理员索取新的设备码。")
                .font(MurmurTheme.body(.footnote))
                .foregroundStyle(MurmurTheme.secondaryInk)
        }
        .frame(maxWidth: 560, alignment: .leading)
        .padding(MurmurTheme.pageInset)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .alert("重置本机安全身份？", isPresented: $confirmReset) {
            Button("取消", role: .cancel) {}
            Button("确认重置", role: .destructive) {
                Task { await model.resetLocalDeviceIdentity() }
            }
        } message: {
            Text("账号与服务端记忆不会被删除；再次连接需要管理员签发的新设备码。")
        }
    }
}

private struct ConnectionLoadingView: View {
    var body: some View {
        VStack(spacing: 18) {
            MurmurMark(size: 58)
            ProgressView()
                .tint(MurmurTheme.olive)
            Text("正在确认这台设备")
                .font(MurmurTheme.body(.subheadline))
                .foregroundStyle(MurmurTheme.secondaryInk)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
    }
}

private struct EnrollmentView: View {
    @ObservedObject var model: MurmurSessionModel
    @State private var inviteCode = ""
    @FocusState private var focused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Spacer(minLength: 42)
                Text("把 Murmur 带到这里。")
                    .font(MurmurTheme.display(.largeTitle))
                    .foregroundStyle(MurmurTheme.ink)
                    .accessibilityAddTraits(.isHeader)
                Text("首次连接使用邀请码；已有账号的新设备使用管理员签发的设备码。设备会通过 App Attest 安全绑定。")
                    .font(MurmurTheme.body(.body))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .fixedSize(horizontal: false, vertical: true)

                VStack(alignment: .leading, spacing: 10) {
                    Text("邀请码或设备码")
                        .font(MurmurTheme.body(.caption, weight: .semibold))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                        .textCase(.uppercase)
                    TextField("输入邀请码或设备码", text: $inviteCode)
                        // Codes are case-sensitive base64url from token_urlsafe;
                        // auto-capitalising silently rewrites them and every
                        // redemption fails with "invalid invite".
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textContentType(.oneTimeCode)
                        .focused($focused)
                        .submitLabel(.continue)
                        .onSubmit(connect)
                        .padding(.horizontal, 16)
                        .frame(minHeight: 52)
                        .background(MurmurTheme.raisedPaper)
                        .overlay {
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(MurmurTheme.rule, lineWidth: 1)
                        }
                }

                if let failure = model.failure {
                    MurmurNotice(message: failure.message, identifier: "enrollment-error")
                }

                Button(action: connect) {
                    HStack {
                        if model.phase.isBusy { ProgressView().tint(MurmurTheme.paper) }
                        Text(model.phase.isBusy ? "正在连接" : "连接这台设备")
                    }
                    .font(MurmurTheme.body(.body, weight: .semibold))
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .foregroundStyle(MurmurTheme.paper)
                    .background(MurmurTheme.ink, in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(MurmurPressStyle())
                .disabled(inviteCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.phase.isBusy)
                .opacity(inviteCode.isEmpty ? 0.55 : 1)
                .accessibilityIdentifier("enroll-button")

                Text("聊天记录只留在这台设备上，删除 App 就一并消失。服务端保存的是私有记忆，不是对话本身。")
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .padding(.top, 10)
                Spacer(minLength: 40)
            }
            .frame(maxWidth: 520)
            .padding(.horizontal, 28)
            .frame(maxWidth: .infinity)
        }
        .onAppear { focused = true }
    }

    private func connect() {
        guard !model.phase.isBusy else { return }
        Task { await model.enroll(inviteCode: inviteCode) }
    }
}

private struct MomentWorkbench: View {
    @ObservedObject var model: MurmurSessionModel
    @Binding var showCamera: Bool
    @State private var showPhotoSource = false
    @State private var openPhoto: MurmurPhotoPreview?
    /// Bumped whenever the field takes focus.  The keyboard notification alone
    /// is not enough: tapping a field that is already first responder raises no
    /// notification at all, and that is exactly when the transcript was left
    /// sitting behind the keyboard.
    @State private var focusPulse = 0
    /// The picker is presented from here rather than from the button that asks
    /// for it.  That button lives under the add-photo menu, which is being torn
    /// down in the same turn the picker is asked for, and a presentation
    /// started from a view on its way out never appears.
    @State private var showLibrary = false
    @State private var selectedItem: PhotosPickerItem?
    /// The composer's focus lives up here, where the conversation can reach it.
    ///
    /// It used to be private to the composer, and a tap on the conversation had
    /// to travel down as a counter the composer watched.  That round trip cost a
    /// whole update pass, and the deferred `onChange` it woke landed on whatever
    /// pass came next — which, when the reader tapped the field again, was the
    /// pass that had just taken focus.  The keyboard was dropped before it had
    /// risen: the recorder caught `tapped field` and then a bare `DidHide`, with
    /// the composer never leaving its resting position.  One piece of shared
    /// focus state has no such gap.
    @FocusState private var composerFocused: Bool
    /// The keyboard, on its own clock.  See `MurmurKeyboardInset`.
    @ObservedObject private var keyboard = MurmurKeyboardInset.shared
    /// Only to know when we have come back from the background, which is the
    /// other moment the keyboard has to be loaded from scratch.
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private func closePhotoSource() {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.82)) {
            showPhotoSource = false
        }
    }

    var body: some View {
        MurmurTranscriptView(
            model: model,
            focusPulse: focusPulse,
            onOpenImage: { openPhoto = $0 },
            onDismissKeyboard: {
#if DEBUG
                MurmurDiagnostics.record("tapped conversation")
#endif
                composerFocused = false
            }
        )
            // A tap anywhere off the menu closes it, the way a popover does.
            // The catcher covers the transcript and nothing else: over the
            // whole screen it would sit on top of the menu it is meant to be
            // outside of and eat the taps meant for it.
            .overlay {
                if showPhotoSource {
                    Color.clear
                        .contentShape(Rectangle())
                        .onTapGesture { closePhotoSource() }
                        .accessibilityLabel("关闭添加照片菜单")
                }
            }
            // The photo waits here, pinned over the conversation just above
            // the field, at the size of a thing you are about to send.  It is
            // not part of the strip below: whatever grows the inset bar joins
            // the safe-area accounting the keyboard lift lives in, and past a
            // point SwiftUI adds the overflow back on top of that lift — the
            // field ended up a band of paper above the keyboard.  A floating
            // tile is not part of that accounting.  The transcript keeps a
            // spacer of the same height at its foot so nothing hides under it.
            .overlay(alignment: .bottom) {
                if model.draftPhoto != nil || model.isPreparingPhoto {
                    DraftPhotoTile(
                        photo: model.draftPhoto,
                        onOpen: { photo in
                            openPhoto = .init(id: photo.id.uuidString, url: photo.originalURL)
                        },
                        onRemove: { model.removeDraftPhoto() }
                    )
                    .frame(maxWidth: MurmurTheme.contentWidth, alignment: .leading)
                    .padding(.leading, MurmurTheme.pageInset)
                    .padding(.bottom, 2)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }
            }
            .animation(.spring(response: 0.32, dampingFraction: 0.86), value: model.draftPhoto?.id)
            .animation(.spring(response: 0.32, dampingFraction: 0.86), value: model.isPreparingPhoto)
            // The strip carries no paper of its own — only the rounded field,
            // and above it a single line when the draft itself is the problem.
            // A failed *send* is not shown here at all: it belongs to its own
            // bubble up in the conversation, where the person can see which
            // line it was and press the mark to send it again.  The inset is
            // what reserves the room, so at rest nothing is hidden, and the
            // padding at its foot is what moves the field when the keyboard
            // arrives — on the keyboard's own curve, not SwiftUI's.
            .safeAreaInset(edge: .bottom, spacing: 0) {
                VStack(spacing: 0) {
                    if let draftFailure = model.draftFailure {
                        DraftFailureLine(message: draftFailure)
                            .transition(.opacity)
                    }
                    MomentComposer(
                        model: model,
                        showCamera: $showCamera,
                        showPhotoSource: $showPhotoSource,
                        onFocus: { focusPulse += 1 },
                        onPickFromLibrary: { showLibrary = true },
                        focused: $composerFocused
                    )
                }
                // The one thing that moves for the keyboard.  Growing the inset
                // rather than sliding the composer means the scroll view's
                // bottom anchor carries the conversation with it, in step,
                // instead of correcting itself afterwards.
                .padding(.bottom, keyboard.overlap)
                .animation(
                    reduceMotion ? nil : .easeInOut(duration: 0.18),
                    value: model.draftFailure
                )
            }
            // SwiftUI's own avoidance would be a second, differently timed
            // motion on top of the one above; two of them are what left the
            // band of paper behind.
            .ignoresSafeArea(.keyboard, edges: .bottom)
        .photosPicker(isPresented: $showLibrary, selection: $selectedItem, matching: .images)
        .fullScreenCover(item: $openPhoto) { photo in
            MurmurPhotoLightbox(url: photo.url)
        }
        .onChange(of: selectedItem, initial: false) { _, item in
            guard let item else { return }
            model.beginPhotoSelection()
            Task {
                defer { selectedItem = nil }
                do {
                    guard let file = try await item.loadTransferable(type: PhotoPickerFile.self) else {
                        model.failPhotoSelection()
                        return
                    }
                    model.preparePhoto(at: file.url)
                } catch {
                    model.failPhotoSelection()
                }
            }
        }
        .task {
            await model.loadTranscript()
            await model.checkProactive()
#if DEBUG
            stubPhotoIfAsked()
#endif
        }
        // The keyboard is loaded before it is wanted, not when the field is
        // tapped.  Once on arrival, and again on the way back from the
        // background, where iOS may have reclaimed it while we were away.
        .onAppear { warmKeyboard() }
        .onChange(of: scenePhase, initial: false) { _, phase in
            guard phase == .active else { return }
            warmKeyboard()
            Task { await model.checkProactive() }
        }
    }

    /// Warming borrows first responder for a turn, which is fine on an idle
    /// screen and not fine over a half-written sentence: coming back from the
    /// background with the field still focused, that turn would take away the
    /// keyboard iOS is in the middle of restoring.
    private func warmKeyboard() {
        guard !composerFocused else { return }
        keyboard.warm()
    }

#if DEBUG
    /// `--murmur-stub-photo` hangs a generated picture on the draft at launch,
    /// through the same prepare path the picker uses, so a UI test can put the
    /// composer in the photo-and-keyboard state without driving the
    /// out-of-process picker.
    private func stubPhotoIfAsked() {
        guard ProcessInfo.processInfo.arguments.contains("--murmur-stub-photo") else { return }
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 400, height: 300))
        let image = renderer.image { ctx in
            UIColor.systemMint.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 400, height: 300))
        }
        guard let data = image.pngData() else { return }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("murmur-stub-photo.png")
        try? data.write(to: url)
        model.beginPhotoSelection()
        model.preparePhoto(at: url)
    }
#endif
}

private struct MomentComposer: View {
    @ObservedObject var model: MurmurSessionModel
    @Binding var showCamera: Bool
    @Binding var showPhotoSource: Bool
    let onFocus: () -> Void
    let onPickFromLibrary: () -> Void
    /// Owned by the workbench, so tapping the conversation can drop focus in the
    /// same turn the tap is seen rather than a pass later.
    @FocusState.Binding var focused: Bool
    @State private var menuHeight: CGFloat = 0
    /// A box rather than plain `@State`: the two paths that see a reach for the
    /// field fire in the same update pass, and a `@State` write is not visible
    /// to the second one, so both signalled and two scrolls fought each other.
    @State private var focusClock = FocusClock()

    var body: some View {
        // Both controls live inside the field as equal discs, so the row is
        // one container instead of a square button beside a taller pill.
        HStack(alignment: .bottom, spacing: 6) {
                Button {
                    focused = false
                    withAnimation(.spring(response: 0.32, dampingFraction: 0.78)) {
                        showPhotoSource.toggle()
                    }
                } label: {
                    Image(systemName: "plus")
                        .rotationEffect(.degrees(showPhotoSource ? 45 : 0))
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(MurmurTheme.ink)
                        .frame(width: MurmurTheme.disc, height: MurmurTheme.disc)
                        .background(MurmurTheme.paper, in: Circle())
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(MurmurPressStyle())
                .disabled(model.isPreparingPhoto)
                .accessibilityLabel("添加照片")

                    // Never disabled: a reply still arriving is no reason to
                    // take the keyboard away mid-thought.
                    TextField("发一张图，或说点什么", text: $model.draftText, axis: .vertical)
                        .font(MurmurTheme.body(.body))
                        .foregroundStyle(MurmurTheme.ink)
                        .lineLimit(1...4)
                        .focused($focused)
                        .submitLabel(.send)
                        .onSubmit(submit)
                        .onChange(of: model.draftText, initial: false) { _, newValue in
                            guard newValue.contains("\n") else { return }
                            model.draftText = newValue.replacingOccurrences(of: "\n", with: "")
                            submit()
                        }
                        .padding(.leading, 8)
                        .padding(.vertical, 12)
                        // Both paths are needed and neither is enough alone:
                        // the gesture catches a tap on a field that is already
                        // first responder (coming back from history), the focus
                        // change catches focus arriving any other way.  They
                        // overlap on the ordinary tap, so the signal is
                        // coalesced — two scrolls milliseconds apart fought
                        // over the same position.
                        .simultaneousGesture(TapGesture().onEnded {
#if DEBUG
                            MurmurDiagnostics.record("field tap gesture")
#endif
                            signalFocus()
                        })
                        .accessibilityLabel("这一刻的文字")
                        .accessibilityIdentifier("moment-composer")

                    Button(action: submit) {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(model.canSubmit ? MurmurTheme.paper : MurmurTheme.secondaryInk)
                            // Inset from the 44pt hit area so the disc sits
                            // inside the field; the tap target keeps its size.
                            .frame(width: MurmurTheme.disc, height: MurmurTheme.disc)
                            .background(
                                model.canSubmit ? MurmurTheme.ink : MurmurTheme.rule,
                                in: Circle()
                            )
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(MurmurPressStyle())
                    .disabled(!model.canSubmit)
                    .accessibilityLabel("发送这一刻")
                    .accessibilityIdentifier("send-moment")
            }
            .padding(.horizontal, 5)
            .padding(.vertical, 4)
            .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 26))
            .overlay { RoundedRectangle(cornerRadius: 26).stroke(MurmurTheme.rule, lineWidth: 1) }
            // Rising out of the field rather than dropping over it: the menu's
            // own bottom is pinned just above the pill, so it grows upward from
            // the button that opened it.
            .overlay(alignment: .topLeading) {
                if showPhotoSource {
                    PhotoSourceMenu(
                        onLibrary: onPickFromLibrary,
                        onCamera: { showCamera = true },
                        onDismiss: { closePhotoSource() }
                    )
                    // Lift by its own measured height so the card's bottom
                    // rests just above the pill, whatever rows it ends up with.
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: {
                        menuHeight = $0
                    }
                    .offset(y: -(menuHeight + 10))
                    .transition(
                        .scale(scale: 0.86, anchor: .bottomLeading)
                        .combined(with: .opacity)
                    )
                }
            }
        .frame(maxWidth: MurmurTheme.contentWidth)
        .padding(.horizontal, MurmurTheme.pageInset)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity)
        .accessibilitySortPriority(3)
#if DEBUG
        .onGeometryChange(for: CGFloat.self) { $0.frame(in: .global).minY } action: {
            MurmurDiagnostics.composerTop = $0
        }
#endif
        .onChange(of: focused, initial: false) { _, isFocused in
#if DEBUG
            MurmurDiagnostics.record("field focus -> \(isFocused)")
#endif
            if isFocused { signalFocus() }
        }
    }

    /// One scroll per reach for the field, however many ways the tap is seen.
    ///
    /// The window has to clear the gap between the two, and it is wider than it
    /// looks: the recorder puts the tap gesture at 16:57:10.619 and the focus
    /// change at 16:57:10.975, 356ms apart.  At the old 0.15s both got through,
    /// every time, so one tap started two scrolls — and each of those scheduled
    /// a second one 300ms later, landing four animated scrolls in the middle of
    /// the keyboard's rise.  That was the stutter.
    private func signalFocus() {
        let now = Date()
        guard now.timeIntervalSince(focusClock.last) > 0.5 else { return }
        focusClock.last = now
        onFocus()
    }

    private func closePhotoSource() {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.82)) {
            showPhotoSource = false
        }
    }

    /// The keyboard stays up.  Sending a line is not the end of the thought —
    /// the next one is usually already half-written — and dropping focus made
    /// every sentence cost a fresh tap on the field and a fresh rise of the
    /// keyboard.  Nothing here needs focus gone: the send button rides above
    /// the keyboard on the inset, the transcript scrolls itself to the newest
    /// line, and putting the keyboard away is still one tap on the
    /// conversation.
    private func submit() {
        guard model.canSubmit else { return }
        model.submit()
    }
}

/// Mutable across a single update pass, which `@State` is not.
@MainActor
private final class FocusClock {
    var last = Date.distantPast
}

private struct MurmurSettingsView: View {
    @ObservedObject var model: MurmurSessionModel
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDelete = false
    @State private var confirmReconnect = false
    @State private var deviceToRemove: MurmurDevice?
    @State private var connectingPush = false
    @State private var confirmClearTranscript = false

    var body: some View {
        NavigationStack {
            Form {
                Section("连接") {
                    LabeledContent("状态", value: model.connection.label)
                    LabeledContent("版本", value: MurmurBuild.summary)
                        .accessibilityIdentifier("build-stamp")
                    if model.requiresDeviceReconnect {
                        Text("本机安全身份已失效。重置只会移除本机绑定，不会删除 Murmur 的记忆。")
                            .font(.footnote)
                            .foregroundStyle(MurmurTheme.coral)
                        Button("重新连接此设备", role: .destructive) { confirmReconnect = true }
                    }
                    Text("每个邀请用户最多可绑定 3 台设备。新增设备需要管理员签发设备码。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("设备") {
                    if !model.devicesLoaded {
                        HStack {
                            ProgressView()
                            Text("正在读取设备")
                        }
                        .foregroundStyle(.secondary)
                    } else if model.devices.isEmpty {
                        Text("还没有绑定设备")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(model.devices) { (device: MurmurDevice) in
                            HStack(spacing: 12) {
                                Image(systemName: device.deviceName?.contains("iPad") == true ? "ipad" : "iphone")
                                    .frame(width: 28)
                                    .foregroundStyle(MurmurTheme.olive)
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack(spacing: 6) {
                                        Text(device.deviceName ?? "Apple 设备")
                                        if device.id == model.identity?.deviceID {
                                            Text("当前")
                                                .font(.caption2.weight(.semibold))
                                                .foregroundStyle(MurmurTheme.olive)
                                        }
                                    }
                                    Text(device.pushEnabled ? "推送已连接" : "推送未连接")
                                        .font(.caption)
                                        .foregroundStyle(
                                            device.pushEnabled || device.id != model.identity?.deviceID
                                                ? AnyShapeStyle(.secondary)
                                                : AnyShapeStyle(MurmurTheme.coral)
                                        )
                                }
                                Spacer()
                                Button(role: .destructive) { deviceToRemove = device } label: {
                                    Image(systemName: "trash")
                                        .frame(width: 44, height: 44)
                                }
                                .accessibilityLabel("移除 \(device.deviceName ?? "设备")")
                            }
                        }
                    }
                }

                Section("主动消息") {
                    Picker("每天最多", selection: $model.preferences.dailyFrequency) {
                        Text("关闭").tag(0)
                        Text("2 条").tag(2)
                        Text("3 条").tag(3)
                        Text("4 条").tag(4)
                    }
                    DatePicker("安静从", selection: quietStart, displayedComponents: .hourAndMinute)
                    DatePicker("安静到", selection: quietEnd, displayedComponents: .hourAndMinute)
                    Button("保存频率与时段") { Task { await model.savePreferences() } }
                }

                Section {
                    Toggle("允许通知", isOn: notificationsAllowed)
                        .accessibilityIdentifier("notification-toggle")
                    LabeledContent("系统通知", value: notificationLabel)
                    LabeledContent("这台设备的推送", value: currentDevicePushEnabled ? "已连接" : "未连接")
                    if notifications.authorization == .denied {
                        Button("前往系统设置") { openSystemSettings() }
                    } else if !currentDevicePushEnabled {
                        Button {
                            Task { await connectPush() }
                        } label: {
                            HStack {
                                Text(connectingPush ? "正在连接推送" : "连接推送")
                                if connectingPush {
                                    Spacer()
                                    ProgressView()
                                }
                            }
                        }
                        .disabled(connectingPush)
                        .accessibilityIdentifier("connect-push")
                    }
                } header: {
                    Text("通知")
                } footer: {
                    // "推送未连接" on its own reads like a fault in the app; the
                    // usual cause is simply that iOS has not been asked yet.
                    if !currentDevicePushEnabled {
                        Text("Murmur 只有拿到这台设备的推送凭证，才能主动送来一条此刻。允许通知后凭证会自动登记。")
                    }
                }

                Section("聊天记录") {
                    Button("清空这一刻") { model.clearCurrent() }
                        .disabled(!model.hasCurrentMoment && model.draftPhoto == nil && model.draftText.isEmpty)
                    Button("清空聊天记录", role: .destructive) { confirmClearTranscript = true }
                        .disabled(model.messages.isEmpty)
                        .accessibilityIdentifier("clear-transcript")
                    Text("聊天记录连同其中的照片只存在这台设备上，删除 App 就一并消失。服务端保存的是私有记忆，不是对话本身。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Button("删除账号与全部记忆", role: .destructive) { confirmDelete = true }
                } footer: {
                    Text("删除会移除设备身份、服务端记忆、dossier、照片预览和待处理任务，无法撤销。")
                }

                if let message = model.settingsMessage {
                    Section { Text(message).foregroundStyle(.secondary) }
                }
            }
            .scrollContentBackground(.hidden)
            .background(MurmurTheme.paper)
            .task {
                await model.loadPreferences()
                await model.refreshDevices()
            }
            .navigationTitle("设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } }
            }
            .alert("删除账号与全部记忆？", isPresented: $confirmDelete) {
                Button("取消", role: .cancel) {}
                Button("确认删除", role: .destructive) {
                    Task {
                        await model.deleteAccount()
                        if model.identity == nil { dismiss() }
                    }
                }
            } message: {
                Text("这个操作无法撤销。")
            }
            .alert("清空聊天记录？", isPresented: $confirmClearTranscript) {
                Button("取消", role: .cancel) {}
                Button("确认清空", role: .destructive) {
                    Task { await model.clearTranscript() }
                }
            } message: {
                Text("这台设备上的对话和其中的照片会被删除，服务端的记忆不受影响。")
            }
            .alert("重置本机安全身份？", isPresented: $confirmReconnect) {
                Button("取消", role: .cancel) {}
                Button("确认重置", role: .destructive) {
                    Task {
                        await model.resetLocalDeviceIdentity()
                        if model.identity == nil { dismiss() }
                    }
                }
            } message: {
                Text("重置后需要管理员签发新的设备码才能再次连接；账号与服务端记忆不会被删除。")
            }
            .alert(item: $deviceToRemove) { device in
                let isCurrent = device.id == model.identity?.deviceID
                return Alert(
                    title: Text(isCurrent ? "移除当前设备？" : "移除这台设备？"),
                    message: Text(isCurrent
                        ? "当前设备会立即退出，需要管理员签发新的设备码才能再次连接。服务端记忆不会因此删除。"
                        : "这台设备之后不能再连接 Murmur。"),
                    primaryButton: .destructive(Text("确认移除")) {
                        Task {
                            await model.removeDevice(device)
                            if isCurrent && model.identity == nil { dismiss() }
                        }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
    }

    private var quietStart: Binding<Date> { timeBinding(\.quietStart) }
    private var quietEnd: Binding<Date> { timeBinding(\.quietEnd) }

    private func timeBinding(_ keyPath: WritableKeyPath<MurmurPreferences, String>) -> Binding<Date> {
        Binding(
            get: { Self.date(from: model.preferences[keyPath: keyPath]) },
            set: { model.preferences[keyPath: keyPath] = Self.time(from: $0) }
        )
    }

    private static func date(from value: String) -> Date {
        let parts = value.split(separator: ":").compactMap { Int($0) }
        return Calendar.current.date(bySettingHour: parts.first ?? 8, minute: parts.dropFirst().first ?? 30, second: 0, of: .now) ?? .now
    }

    private static func time(from date: Date) -> String {
        let components = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", components.hour ?? 0, components.minute ?? 0)
    }

    private var notificationLabel: String {
        switch notifications.authorization {
        case .allowed: "已允许"
        case .denied: "已关闭"
        case .notDetermined: "尚未询问"
        case .unknown: "未知"
        }
    }

    private var notificationsAllowed: Binding<Bool> {
        Binding(
            get: { notifications.authorization == .allowed },
            set: { enabled in
                Task {
                    if enabled {
                        if notifications.authorization == .notDetermined {
                            await notifications.requestAuthorizationIfNeeded()
                        } else if notifications.authorization != .allowed {
                            openSystemSettings()
                        }
                    } else if notifications.authorization == .allowed {
                        openSystemSettings()
                    }
                }
            }
        )
    }

    private var currentDevicePushEnabled: Bool {
        guard let deviceID = model.identity?.deviceID else { return false }
        return model.devices.first { $0.id == deviceID }?.pushEnabled ?? false
    }

    /// Walks the whole chain in one tap: permission, an APNs token, the token
    /// registered with Murmur, then a fresh read of what the server now thinks.
    /// Doing it piecemeal is how the row ends up stale and saying "未连接"
    /// after the person has already said yes.
    private func connectPush() async {
        connectingPush = true
        defer { connectingPush = false }
        await notifications.refreshAuthorizationStatus()
        if notifications.authorization == .notDetermined {
            await notifications.requestAuthorizationIfNeeded()
        }
        guard notifications.authorization == .allowed else {
            openSystemSettings()
            return
        }
        let token = await notifications.tokenAfterRegistering()
        await model.updatePushRegistration(token: token)
        await model.refreshDevices()
    }

    private func openSystemSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}

/// The photo waiting to be sent, sitting just above the field it will leave
/// from.  Tapping it opens it full screen; the cross takes it back off.
private struct DraftPhotoTile: View {
    let photo: PhotoAttachment?
    let onOpen: (PhotoAttachment) -> Void
    let onRemove: () -> Void

    private static let side: CGFloat = 76

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Group {
                if let photo {
                    Button {
                        onOpen(photo)
                    } label: {
                        Image(uiImage: photo.preview)
                            .resizable()
                            .scaledToFill()
                            .frame(width: Self.side, height: Self.side)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                            .contentShape(RoundedRectangle(cornerRadius: 14))
                    }
                    .buttonStyle(MurmurPressStyle())
                    .accessibilityLabel("待发送的照片，轻点放大")
                    .accessibilityIdentifier("draft-photo")
                } else {
                    ZStack {
                        MurmurTheme.raisedPaper
                        ProgressView().tint(MurmurTheme.olive)
                    }
                    .frame(width: Self.side, height: Self.side)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    .accessibilityLabel("正在准备照片")
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: 14).stroke(MurmurTheme.rule, lineWidth: 1)
            }

            if photo != nil {
                Button(action: onRemove) {
                    Image(systemName: "xmark")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(MurmurTheme.paper)
                        .frame(width: 22, height: 22)
                        .background(MurmurTheme.ink.opacity(0.85), in: Circle())
                        .overlay { Circle().stroke(MurmurTheme.paper, lineWidth: 1.5) }
                        // The disc straddles the corner; the tap target around
                        // it stays a full 44pt without covering the picture.
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(MurmurPressStyle())
                .offset(x: 13, y: -13)
                .accessibilityLabel("移除待发送照片")
                .accessibilityIdentifier("remove-draft-photo")
            }
        }
        // Room for the cross to overhang without being clipped.
        .padding(.top, 11)
        .padding(.trailing, 11)
    }
}

/// The add-photo menu, drawn in the app's own paper instead of system chrome
/// so it belongs to the composer it rises out of.
private struct PhotoSourceMenu: View {
    let onLibrary: () -> Void
    let onCamera: () -> Void
    let onDismiss: () -> Void

    private var hasCamera: Bool {
        UIImagePickerController.isSourceTypeAvailable(.camera)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            row("从照片中选择", icon: "photo.on.rectangle") {
                onDismiss()
                onLibrary()
            }
            if hasCamera {
                Rectangle()
                    .fill(MurmurTheme.rule)
                    .frame(height: 1)
                    .padding(.leading, 48)
                row("拍照", icon: "camera") {
                    onDismiss()
                    onCamera()
                }
            }
        }
        .frame(width: 210)
        .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 16))
        .overlay { RoundedRectangle(cornerRadius: 16).stroke(MurmurTheme.rule, lineWidth: 1) }
        .shadow(color: MurmurTheme.ink.opacity(0.10), radius: 10, y: 4)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("photo-source-menu")
    }

    private func row(_ title: String, icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 20)
                Text(title)
                    .font(MurmurTheme.body(.subheadline))
                Spacer(minLength: 0)
            }
            .foregroundStyle(MurmurTheme.ink)
            .padding(.horizontal, 14)
            .frame(height: 48)
            .contentShape(Rectangle())
        }
        .buttonStyle(MurmurPressStyle())
    }
}

/// Something wrong with what is still in the composer — a photo that could not
/// be read.  One coral line, not a card: the draft is right there under it, so
/// the line only has to name the problem, and a box the height of a paragraph
/// would push the whole conversation up to say one sentence.
private struct DraftFailureLine: View {
    let message: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: "exclamationmark.circle.fill")
                .font(.system(size: 12, weight: .semibold))
                .accessibilityHidden(true)
            Text(message)
                .font(MurmurTheme.body(.footnote))
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(MurmurTheme.coral)
        .frame(maxWidth: MurmurTheme.contentWidth, alignment: .leading)
        .padding(.horizontal, MurmurTheme.pageInset)
        .padding(.top, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("draft-error")
    }
}

/// The failure block on a screen that has nothing else on it — enrolment.  A
/// screen with a conversation or a composer says it smaller and closer to the
/// thing that failed; this is the one place where the failure *is* the content.
private struct MurmurNotice: View {
    let message: String
    var identifier: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Rectangle()
                .fill(MurmurTheme.coral)
                .frame(width: 2)
                .accessibilityHidden(true)
            Text(message)
                .font(MurmurTheme.body(.body))
                .foregroundStyle(MurmurTheme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier(identifier)
        }
        .accessibilityElement(children: .contain)
    }
}

private struct MurmurMark: View {
    let size: CGFloat

    var body: some View {
        Image("MurmurMark")
            .resizable()
            .scaledToFill()
            .frame(width: size, height: size, alignment: .top)
            .clipShape(Circle())
            .overlay {
                Circle().strokeBorder(MurmurTheme.rule, lineWidth: 0.5)
            }
            .accessibilityLabel("Murmur")
    }
}

/// The app's press feedback, shared by every control that is not a floating
/// disc — including the failure mark in the transcript, which is why this is
/// not file-private.
struct MurmurPressStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.72 : 1)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
            .animation(.easeOut(duration: reduceMotion ? 0.1 : 0.14), value: configuration.isPressed)
    }
}

private struct CameraPicker: UIViewControllerRepresentable {
    let onCaptureFile: (URL) -> Void
    let onCaptureImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.cameraCaptureMode = .photo
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
        let parent: CameraPicker
        init(parent: CameraPicker) { self.parent = parent }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.dismiss()
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            if let url = info[.imageURL] as? URL {
                parent.onCaptureFile(url)
            } else if let image = info[.originalImage] as? UIImage {
                parent.onCaptureImage(image)
            }
            parent.dismiss()
        }
    }
}

#if DEBUG
#Preview {
    MurmurChatView(model: MurmurSessionModel(api: PreviewMurmurAPIClient()))
        .environmentObject(MurmurNotificationBridge.shared)
}

private actor PreviewMurmurAPIClient: MurmurAPIClient {
    func storedIdentity() async throws -> MurmurIdentity? { .init(userID: "preview", deviceID: "preview", keyID: "preview") }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity { .init(userID: "preview", deviceID: "preview", keyID: "preview") }
    func createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String, intent: MurmurMomentIntent?) async throws -> MomentReceipt { .init(momentID: "preview", status: "queued") }
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func currentProactive() async throws -> ProactiveMoment? { nil }
    func acknowledge(momentID: String, reply: String?) async throws {}
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws {}
    func devices() async throws -> [MurmurDevice] { [] }
    func removeDevice(deviceID: String) async throws {}
    func preferences() async throws -> MurmurPreferences { MurmurPreferences() }
    func updatePreferences(_ preferences: MurmurPreferences) async throws {}
    func resetLocalIdentity() async throws {}
    func deleteAccount() async throws {}
}
#endif
