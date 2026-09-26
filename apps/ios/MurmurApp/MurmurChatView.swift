import PhotosUI
import SwiftUI
import UIKit

/// System semantic surfaces with Murmur's readable cyan accent.
@MainActor
enum MurmurTheme {
    static let paper = Color(uiColor: .systemBackground)
    static let raisedPaper = Color(uiColor: .secondarySystemBackground)
    static let ink = Color.primary
    static let secondaryInk = Color.secondary
    static let rule = Color(uiColor: .separator)
    static let accent = Color(red: 0, green: 0.718, blue: 0.780)
    static let accentInk = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0, green: 0.718, blue: 0.780, alpha: 1)
            : UIColor(red: 0, green: 0.439, blue: 0.478, alpha: 1)
    })
    static let onAccent = Color(red: 0.043, green: 0.129, blue: 0.141)
    static let coral = Color(uiColor: .systemRed)
    static let outgoingBubble = accent
    static let disc: CGFloat = 36
    static let floatingDisc: CGFloat = 44
    static let pageInset: CGFloat = 20
    static let contentWidth: CGFloat = 1_080
    static let corner: CGFloat = 18
    static func display(_ style: Font.TextStyle) -> Font {
        .system(style, weight: .semibold)
    }
    static func body(_ style: Font.TextStyle = .body, weight: Font.Weight = .regular) -> Font {
        .system(style, weight: weight)
    }
}

struct MurmurChatView: View {
    @ObservedObject var model: MurmurSessionModel
    @ObservedObject var music: MusicModule
    @ObservedObject private var netease: NeteaseMusicModel
    @Environment(\.openURL) private var openURL
    @Environment(\.murmurSelectTab) private var selectTab
    @State private var showCamera = false

    init(model: MurmurSessionModel, music: MusicModule) {
        self.model = model
        self.music = music
        self._netease = ObservedObject(wrappedValue: music.netease)
    }

    var body: some View {
        MomentWorkbench(model: model, showCamera: $showCamera, music: music)
            .safeAreaInset(edge: .top, spacing: 0) {
                MurmurTopChrome(
                    connection: model.connection,
                    room: netease.room,
                    track: netease.displayTrack,
                    presentation: netease.presentationState(connection: model.connection),
                    onPrimary: performPrimaryRoomAction,
                    onNext: { Task { await netease.command(.next) } },
                    onOpenTab: { selectTab(.listenTogether) }
                )
            }
            .modifier(MurmurChatNavigation(session: model, reviews: model.reviews))
            .background(MurmurTheme.paper.ignoresSafeArea())
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
        .sheet(isPresented: $showCamera) {
            CameraPicker(
                onCaptureFile: { model.preparePhoto(at: $0) },
                onCaptureImage: { model.prepareCapturedPhoto($0) }
            )
            .ignoresSafeArea()
        }
    }

    private func performPrimaryRoomAction() {
        switch netease.presentationState(connection: model.connection) {
        case .waiting:
            openRoomInNetease()
        case .playing:
            Task { await netease.command(.pause) }
        case .paused:
            Task { await netease.command(.resume) }
        case .commandFailed:
            Task { _ = await netease.retryLastAction() }
        case .creationFailed:
            Task {
                if let inviteURL = await netease.retryLastAction() {
                    openURL(inviteURL)
                }
            }
        case .roomFailed:
            guard let track = netease.room?.currentTrack else { return }
            Task {
                if let inviteURL = await netease.createRoom(for: track) {
                    openURL(inviteURL)
                }
            }
        case .inactive, .creating, .syncing, .offline:
            break
        }
    }

    private func openRoomInNetease() {
        guard let inviteURL = netease.room?.inviteURL else { return }
        openURL(inviteURL)
    }
}

/// Optional connection and active-room status; no space when connected and idle.
private struct MurmurTopChrome: View {
    let connection: MurmurConnectionState
    let room: ListenTogetherRoomSnapshotV1?
    let track: MusicTrackAttachmentV1?
    let presentation: ListenTogetherPresentationState
    let onPrimary: () -> Void
    let onNext: () -> Void
    let onOpenTab: () -> Void

    var body: some View {
        if presentation != .inactive || connection != .connected {
            HStack(alignment: .top, spacing: 8) {
                ListenTogetherCard(
                    room: room,
                    track: track,
                    presentation: presentation,
                    onPrimary: onPrimary,
                    onNext: onNext,
                    onOpen: onOpenTab
                )
                Spacer(minLength: 0)
                // An active room already presents its own connection state.
                if connection != .connected, presentation == .inactive {
                    Text(connection.label)
                        .font(MurmurTheme.body(.caption2, weight: .medium))
                        .foregroundStyle(connectionCaptionColor)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(MurmurTheme.raisedPaper, in: Capsule())
                }
            }
            .frame(maxWidth: MurmurTheme.contentWidth)
            .padding(.horizontal, MurmurTheme.pageInset)
            .padding(.top, 6)
            .padding(.bottom, 8)
            .frame(maxWidth: .infinity)
        }
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

struct DeviceReconnectView: View {
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

struct ConnectionLoadingView: View {
    var body: some View {
        VStack(spacing: 18) {
            MurmurMark(size: 58)
            ProgressView()
                .tint(MurmurTheme.accentInk)
            Text("正在确认这台设备")
                .font(MurmurTheme.body(.subheadline))
                .foregroundStyle(MurmurTheme.secondaryInk)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
    }
}

struct EnrollmentView: View {
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
                    Text(failure.message)
                        .foregroundStyle(MurmurTheme.coral)
                        .accessibilityIdentifier("enrollment-error")
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
                .buttonStyle(.automatic)
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

enum NeteaseIncomingCardRefresh {
    static func shouldRefresh(phase: MurmurPhase, message: MurmurMessage?) -> Bool {
        phase == .responding
            && message?.author == .murmur
            && message?.musicTrack?.isNetease == true
    }
}

private struct MomentWorkbench: View {
    @ObservedObject var model: MurmurSessionModel
    @Binding var showCamera: Bool
    @ObservedObject var music: MusicModule
    @ObservedObject private var netease: NeteaseMusicModel
    @State private var showMusicPicker = false
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
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.murmurReduceMotion) private var reduceMotion
    @Environment(\.openURL) private var openURL
    @ScaledMetric(relativeTo: .body) private var neteaseShareHeightLimit: CGFloat = 360

    init(model: MurmurSessionModel, showCamera: Binding<Bool>, music: MusicModule) {
        self.model = model
        self._showCamera = showCamera
        self.music = music
        self.netease = music.netease
    }

    var body: some View {
        MurmurTranscriptView(
            model: model,
            focusPulse: focusPulse,
            keyboardIsFocused: composerFocused,
            onOpenImage: { openPhoto = $0 },
            onDismissKeyboard: {
#if DEBUG
                MurmurDiagnostics.record("tapped conversation")
#endif
                composerFocused = false
            },
            nowPlaying: music.nowPlaying,
            onPlayMusic: { music.player.tap($0) },
            onListenTogether: music.isListenTogetherAvailable ? { track in
                Task {
                    if let inviteURL = await netease.createRoom(for: track) {
                        openURL(inviteURL)
                    }
                }
            } : nil,
            listenTogetherRoom: netease.room
        )
            // Answering a review's question starts in the composer.
            .onChange(of: model.questionContext) { _, context in
                if context != nil { composerFocused = true }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                VStack(spacing: 0) {
                    if model.draftPhoto != nil || model.isPreparingPhoto {
                        DraftPhotoTile(
                            photo: model.draftPhoto,
                            onOpen: { photo in
                                openPhoto = .init(id: photo.id.uuidString, url: photo.originalURL)
                            },
                            onRemove: { model.removeDraftPhoto() }
                        )
                        .frame(maxWidth: MurmurTheme.contentWidth, alignment: .leading)
                        .padding(.horizontal, MurmurTheme.pageInset)
                        .padding(.top, 8)
                    }
                    if let context = model.questionContext {
                        QuestionContextLine(context: context) { model.questionContext = nil }
                    }
                    if let draftFailure = model.draftFailure {
                        DraftFailureLine(message: draftFailure)
                            .transition(.opacity)
                    }
                    MomentComposer(
                        model: model,
                        showCamera: $showCamera,
                        onFocus: { focusPulse += 1 },
                        onPickFromLibrary: { showLibrary = true },
                        // Two gates, and both have to be open: this build has
                        // an Audius registration, and Murmur's server says this
                        // account may use music at all.
                        onPickMusic: (music.isAvailable || music.isNeteaseSearchAvailable)
                            ? { showMusicPicker = true } : nil,
                        onSubmitText: music.isNeteaseCatalogAvailable ? { text in
                            guard model.draftPhoto == nil,
                                  netease.recognizePastedText(text)
                            else { return false }
                            model.draftText = ""
                            return true
                        } : nil,
                        focused: $composerFocused
                    )
                }
                .background(MurmurTheme.paper)
                .animation(
                    reduceMotion ? nil : .easeInOut(duration: 0.18),
                    value: model.draftFailure
                )
            }
        .photosPicker(isPresented: $showLibrary, selection: $selectedItem, matching: .images)
        .sheet(isPresented: $showMusicPicker) {
            // 两个曲库，两个选歌器。`music.isAvailable` 只在 Audius 开着时为
            // 真，而生产上的灰度形态是「只有网易云」——以前那种账号点开 ＋ 里的
            // 「音乐」什么都没有，因为这个按钮本身就是关的。
            if music.isAvailable {
                MusicPickerView(
                    client: music.library,
                    account: music.account,
                    onSend: { model.submitMusic($0) }
                )
            } else if music.isNeteaseSearchAvailable {
                NeteaseSearchSheet(
                    api: netease.api,
                    purpose: .share,
                    actionLabel: "发送"
                ) {
                    model.submitMusic($0)
                }
            }
        }
        .sheet(
            isPresented: Binding(
                get: { netease.preview != nil },
                set: { if !$0, netease.preview != nil { netease.discardPreview() } }
            )
        ) {
            if let preview = netease.preview {
                NeteaseSharedMusicPreviewView(
                    preview: preview,
                    onConfirm: {
                        guard let track = netease.confirmPreview() else { return }
                        model.submitMusic(track)
                    },
                    onCancel: { netease.discardPreview() }
                )
                .padding(.top, 20)
                // A root ScrollView reports its viewport as the fitted ideal
                // height on compact iPhone. Scale the compact detent with type
                // and let that ScrollView handle accessibility-size overflow.
                .presentationDetents([.height(neteaseShareHeightLimit)])
                .presentationContentInteraction(.scrolls)
                .presentationDragIndicator(.visible)
                .presentationBackground(MurmurTheme.paper)
            }
        }
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
        .onChange(of: model.messages.count, initial: false) { _, _ in
            guard music.isListenTogetherAvailable,
                  NeteaseIncomingCardRefresh.shouldRefresh(
                    phase: model.phase,
                    message: model.messages.last
                  )
            else { return }
            // The worker creates or changes the room before emitting its card.
            // Fetch at that boundary instead of waiting for the idle poll.
            Task { await netease.refreshRoom() }
        }
        .task {
            await model.loadTranscript()
            await model.checkProactive()
            if music.isNeteaseCatalogAvailable {
                netease.loadPendingShareDraft()
            }
#if DEBUG
            stubPhotoIfAsked()
#endif
        }
        .onChange(of: scenePhase, initial: false) { _, phase in
            guard phase == .active else { return }
            Task {
                await model.checkProactive()
                if music.isNeteaseCatalogAvailable {
                    netease.loadPendingShareDraft()
                }
            }
        }
        .onChange(of: music.isNeteaseCatalogAvailable, initial: true) { _, enabled in
            guard enabled else { return }
            netease.loadPendingShareDraft()
#if DEBUG
            netease.seedUITestSharePreviewIfRequested()
#endif
        }
        .overlay(alignment: .top) {
            if netease.isResolvingShare {
                Label("正在识别网易云歌曲…", systemImage: "music.note")
                    .font(MurmurTheme.body(.footnote, weight: .medium))
                    .foregroundStyle(MurmurTheme.ink)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(MurmurTheme.raisedPaper, in: Capsule())
                    .overlay { Capsule().stroke(MurmurTheme.rule, lineWidth: 1) }
                    .padding(.top, 8)
            }
        }
        .alert(
            "网易云音乐",
            isPresented: Binding(
                // Room mutations render their own recoverable state in the
                // corner card / 一起听 tab.  Presenting this generic alert
                // would clear the retained track and retry key on dismissal.
                get: {
                    netease.failureMessage != nil && netease.roomMutation == nil
                },
                set: { if !$0 { netease.clearFailure() } }
            )
        ) {
            Button("知道了") { netease.clearFailure() }
        } message: {
            Text(netease.failureMessage ?? "")
        }
        .toolbar(composerFocused ? .hidden : .visible, for: .tabBar)
        .onDisappear {
            composerFocused = false
        }
#if DEBUG
        .onChange(of: composerFocused, initial: true) { _, focused in
            MurmurDiagnostics.recordKeyboardFocus(source: "chat", focused: focused)
        }
#endif
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
    let onFocus: () -> Void
    let onPickFromLibrary: () -> Void
    var onPickMusic: (() -> Void)?
    /// Returns true when the text was consumed by an attachment flow (for
    /// example a pasted NetEase link that must be previewed before sending).
    var onSubmitText: ((String) -> Bool)?
    /// Owned by the workbench, so tapping the conversation can drop focus in the
    /// same turn the tap is seen rather than a pass later.
    @FocusState.Binding var focused: Bool
    /// A box rather than plain `@State`: the two paths that see a reach for the
    /// field fire in the same update pass, and a `@State` write is not visible
    /// to the second one, so both signalled and two scrolls fought each other.
    @State private var focusClock = FocusClock()

    var body: some View {
        MurmurComposer(
            text: $model.draftText,
            focused: $focused,
            placeholder: "发一张图，或说点什么",
            fieldLabel: "这一刻的文字",
            fieldIdentifier: "moment-composer",
            sendIdentifier: "send-moment",
            canSend: model.canSubmit,
            onSend: submit,
            sendLabel: "发送这一刻",
            onFocus: signalFocus
        ) {
            Menu {
                Button("从照片中选择", systemImage: "photo.on.rectangle", action: onPickFromLibrary)
                if UIImagePickerController.isSourceTypeAvailable(.camera) {
                    Button("拍照", systemImage: "camera") { showCamera = true }
                }
                if let onPickMusic {
                    Button("音乐", systemImage: "music.note", action: onPickMusic)
                }
            } label: {
                Image(systemName: "plus").frame(width: 44, height: 44)
            }
            .disabled(model.isPreparingPhoto)
            .accessibilityLabel("添加照片")
        }
#if DEBUG
        .onGeometryChange(for: CGFloat.self) { $0.frame(in: .global).minY } action: {
            MurmurDiagnostics.composerTop = $0
        }
#endif
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

    /// The keyboard stays up.  Sending a line is not the end of the thought —
    /// the next one is usually already half-written — and dropping focus made
    /// every sentence cost a fresh tap on the field and a fresh rise of the
    /// keyboard.  Nothing here needs focus gone: the send button rides above
    /// the keyboard on the inset, the transcript scrolls itself to the newest
    /// line, and putting the keyboard away is still one tap on the
    /// conversation.
    private func submit() {
        guard model.canSubmit else { return }
        if onSubmitText?(model.draftText) == true { return }
        model.submit()
    }
}

/// Mutable across a single update pass, which `@State` is not.
@MainActor
private final class FocusClock {
    var last = Date.distantPast
}

struct MurmurSettingsView: View {
    @ObservedObject var model: MurmurSessionModel
    @ObservedObject var music: MusicModule
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDelete = false
    @State private var confirmReconnect = false
    @State private var deviceToRemove: MurmurDevice?
    @State private var connectingPush = false
    @State private var confirmClearTranscript = false
    @State private var confirmClearArchive = false

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

                // Only when the server has actually turned music on for this
                // account: an account that has never seen the feature should
                // not be offered a music setting it cannot use.
                if music.isAvailable {
                    AudiusSection(music: music)
                }

                MurmurReviewSettingsEntry(reviews: model.reviews)

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
                                    .foregroundStyle(MurmurTheme.accentInk)
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack(spacing: 6) {
                                        Text(device.deviceName ?? "Apple 设备")
                                        if device.id == model.identity?.deviceID {
                                            Text("当前")
                                                .font(.caption2.weight(.semibold))
                                                .foregroundStyle(MurmurTheme.accentInk)
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
                    // Also offered when the history could not be read: clearing
                    // is the way out of a store that keeps failing.
                    Button("清空聊天记录", role: .destructive) { confirmClearTranscript = true }
                        .disabled(
                            model.messages.isEmpty
                                && model.transcriptDays.isEmpty
                                && model.storageFailure == nil
                        )
                        .accessibilityIdentifier("clear-transcript")
                    Text("聊天记录连同其中的照片只存在这台设备上，删除 App 就一并消失。服务端保存的是私有记忆，不是对话本身。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                // Two histories, two switches.  当年今日's archive is not part
                // of the conversation and must not be swept away with it.
                Section("当年今日") {
                    LabeledContent("留下的日子", value: "\(model.archive.daysWithRooms.count) 天")
                    Button("清空当年今日的记录", role: .destructive) { confirmClearArchive = true }
                        .disabled(
                            model.archive.rows.isEmpty
                                && model.archive.dayIndex.isEmpty
                                && model.archive.storageFailure == nil
                        )
                        .accessibilityIdentifier("clear-archive")
                    Text("照片房间记录独立保存在这台设备上。清空不会删除服务端记忆。")
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
            .task {
                await model.loadPreferences()
                await model.refreshDevices()
            }
            .navigationTitle("我的")
            .navigationBarTitleDisplayMode(.inline)
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
            .alert("清空当年今日的记录？", isPresented: $confirmClearArchive) {
                Button("取消", role: .cancel) {}
                Button("确认清空", role: .destructive) {
                    Task { await model.archive.clear() }
                }
            } message: {
                Text("日历会空掉，那些天聊过的照片也会从这台设备上删除。服务端的记忆不受影响。")
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
                    .buttonStyle(.automatic)
                    .accessibilityLabel("待发送的照片，轻点放大")
                    .accessibilityIdentifier("draft-photo")
                } else {
                    ZStack {
                        MurmurTheme.raisedPaper
                        ProgressView().tint(MurmurTheme.accentInk)
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
                .buttonStyle(.automatic)
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

/// The Audius account, in 我的.
///
/// Connecting is what unlocks 收藏 and 歌单 in the picker; public search works
/// without it. Disconnecting clears the credential on this device whether or
/// not Audius can be reached to revoke it.
private struct AudiusSection: View {
    @ObservedObject var music: MusicModule
    @State private var working = false

    var body: some View {
        Section("音乐") {
            switch music.account.state {
            case let .signedIn(user):
                LabeledContent("Audius", value: user.handle.isEmpty ? user.name : "@\(user.handle)")
                Button("断开 Audius", role: .destructive) {
                    working = true
                    Task {
                        await music.account.logout()
                        working = false
                    }
                }
                .disabled(working)
            case .authorizing:
                HStack {
                    ProgressView()
                    Text("正在连接 Audius")
                }
                .foregroundStyle(.secondary)
            case .needsReconnect:
                Text("Audius 授权已失效，需要重新连接。")
                    .font(.footnote)
                    .foregroundStyle(MurmurTheme.coral)
                connectButton(title: "重新连接 Audius")
            case let .failed(message):
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(MurmurTheme.coral)
                connectButton(title: "再试一次")
            case .signedOut:
                connectButton(title: "连接 Audius")
            case .unavailable:
                Text("这个版本还没有配置 Audius。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Text("连接后可以在聊天里发送你的 Audius 收藏和歌单。搜索公开曲库不需要连接。Murmur 不会收到你的 Audius 授权或完整收听记录。")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private func connectButton(title: String) -> some View {
        Button(title) {
            working = true
            Task {
                await music.account.login()
                working = false
            }
        }
        .disabled(working)
    }
}

/// Which review question the next line answers, with a way to drop it.
private struct QuestionContextLine: View {
    let context: MurmurQuestionContext
    let onCancel: () -> Void

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text("回答 \(context.day) 的问题")
                    .font(.caption)
                    .foregroundStyle(MurmurTheme.secondaryInk)
                Text(context.question)
                    .font(.footnote)
                    .lineLimit(3)
            }
            Spacer()
            Button("取消", systemImage: "xmark", action: onCancel)
                .labelStyle(.iconOnly)
                .frame(minWidth: 44, minHeight: 44)
        }
        .padding(12)
        .background(MurmurTheme.raisedPaper)
        .padding(.horizontal, MurmurTheme.pageInset)
    }
}

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

/// Brief content changes; system navigation and keyboard own their motion.
enum MurmurMotion {
    static let content = Animation.easeOut(duration: 0.15)
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
    let api = PreviewMurmurAPIClient()
    MurmurChatView(
        model: MurmurSessionModel(api: api),
        music: MusicModule(api: api, configuration: nil)
    )
        .environmentObject(MurmurNotificationBridge.shared)
        .environmentObject(MurmurKeyboardState())
}

private actor PreviewMurmurAPIClient: MurmurAPIClient {
    func storedIdentity() async throws -> MurmurIdentity? { .init(userID: "preview", deviceID: "preview", keyID: "preview") }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity { .init(userID: "preview", deviceID: "preview", keyID: "preview") }
    func createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String, intent: MurmurMomentIntent?, contextMomentIDs: [String]) async throws -> MomentReceipt { .init(momentID: "preview", status: "queued") }
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

// The system preference remains authoritative; the extra branch only lets
// isolated UI tests exercise the same paths without changing simulator settings.
extension EnvironmentValues {
    var murmurReduceMotion: Bool {
#if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--murmur-ui-testing"), arguments.contains("--murmur-ui-test-reduce-motion") {
            return true
        }
#endif
        return accessibilityReduceMotion
    }
}
