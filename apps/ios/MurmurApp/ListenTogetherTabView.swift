import SwiftUI

/// 「一起听」那一整屏。
///
/// 以前这些控制挤在聊天页顶部一条 90pt 的玻璃条里，上一首、下一首和结束还得
/// 再点开一个弹层才够得着。现在它有一整屏：封面看得清，三颗键排得开，收尾的
/// 动作不必藏在省略号后面。聊天页只留左上角那张小卡片。
///
/// 这里**不轮询**。房间轮询归 `MurmurShell` 管（见那边的注释）——一次只有一个
/// tab 在树上，谁看着谁轮询的话，另一边看到的就是冻住的房间。
struct ListenTogetherTabView: View {
    @ObservedObject var model: MurmurSessionModel
    @ObservedObject var music: MusicModule
    @ObservedObject private var netease: NeteaseMusicModel
    @Environment(\.openURL) private var openURL
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var showPicker = false

    init(model: MurmurSessionModel, music: MusicModule) {
        self.model = model
        self.music = music
        self._netease = ObservedObject(wrappedValue: music.netease)
    }

    private var presentation: ListenTogetherPresentationState {
        netease.presentationState(connection: model.connection)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: MurmurTheme.contentWidth)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(MurmurTheme.paper.ignoresSafeArea())
        .sheet(isPresented: $showPicker) {
            NeteaseSearchSheet(
                api: netease.api,
                purpose: .listenTogether,
                onPick: start
            )
        }
        .accessibilityIdentifier("listen-together-tab")
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("一起听")
                .font(MurmurTheme.display(.title2))
                .foregroundStyle(MurmurTheme.ink)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            if music.isListenTogetherAvailable, presentation != .inactive {
                Text(presentation.statusTag)
                    .font(MurmurTheme.body(.caption2, weight: .semibold))
                    .foregroundStyle(presentation.statusColor)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(presentation.statusColor.opacity(0.13), in: Capsule())
            }
        }
        .padding(.horizontal, MurmurTheme.pageInset)
        .padding(.top, 8)
        .padding(.bottom, 16)
    }

    // MARK: - Which face

    @ViewBuilder
    private var content: some View {
        if !music.hasAppliedAvailability {
            // 服务端还没答话。此刻说「未开放」有一半机会是错的，等一下再说。
            Color.clear
        } else if !music.isListenTogetherAvailable {
            unavailable
        } else {
            switch presentation {
            case .inactive: empty
            case .creating, .creationFailed: roomMutation
            default: player
            }
        }
    }

    private var unavailable: some View {
        centred {
            Image(systemName: "headphones")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.5))
                .accessibilityHidden(true)
            Text("一起听还没有对你开放")
                .font(MurmurTheme.display(.title3))
                .foregroundStyle(MurmurTheme.ink)
                .multilineTextAlignment(.center)
            Text("这个功能还在很小的范围里试。开放了它会自己出现，不用你做什么。")
                .font(MurmurTheme.body(.subheadline))
                .foregroundStyle(MurmurTheme.secondaryInk)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityIdentifier("listen-together-unavailable")
    }

    private var empty: some View {
        centred {
            Image(systemName: "music.note")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.5))
                .accessibilityHidden(true)
            Text("现在没有在一起听")
                .font(MurmurTheme.display(.title3))
                .foregroundStyle(MurmurTheme.ink)
                .multilineTextAlignment(.center)
            Text("选一首网易云的歌，Murmur 会跟你听同一首。")
                .font(MurmurTheme.body(.subheadline))
                .foregroundStyle(MurmurTheme.secondaryInk)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if music.isNeteaseSearchAvailable {
                Button("选一首歌") { showPicker = true }
                    .font(MurmurTheme.body(.subheadline, weight: .semibold))
                    .foregroundStyle(MurmurTheme.onAccent)
                    .padding(.horizontal, 24)
                    .frame(minHeight: MurmurTheme.floatingDisc)
                    .background(MurmurTheme.accent, in: Capsule())
                    .buttonStyle(MurmurPressStyle())
                    // 离线时搜索一定失败。给一个必然打不开的门不如先关上它。
                    .disabled(model.connection.isOffline)
                    .opacity(model.connection.isOffline ? 0.45 : 1)
                    .padding(.top, 8)
            } else {
                Text("选歌暂不可用")
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .padding(.top, 8)
            }
            if model.connection.isOffline, music.isNeteaseSearchAvailable {
                Text("Murmur 连接异常，先连上再选歌。")
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.coral)
            }
        }
        .accessibilityIdentifier("listen-together-empty")
    }

    private var roomMutation: some View {
        centred {
            cover
            Text(netease.displayTrack?.title ?? ListenTogetherRoomSnapshotV1.noTrackLine)
                .font(MurmurTheme.display(.title3))
                .foregroundStyle(MurmurTheme.ink)
                .multilineTextAlignment(.center)
            if let meta = metaLine {
                Text(meta)
                    .font(MurmurTheme.body(.subheadline))
                    .foregroundStyle(MurmurTheme.secondaryInk)
            }
            switch presentation {
            case .creating:
                ProgressView("正在创建一起听…")
                    .font(MurmurTheme.body(.footnote, weight: .medium))
                    .tint(MurmurTheme.accentInk)
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .padding(.top, 8)
            case .creationFailed(_, let message):
                Text(message)
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.coral)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 8)
                Button {
                    Task {
                        if let invite = await netease.retryLastAction() { openURL(invite) }
                    }
                } label: {
                    Text("重新建房")
                        .font(MurmurTheme.body(.subheadline, weight: .semibold))
                        .foregroundStyle(MurmurTheme.onAccent)
                        .padding(.horizontal, 24)
                        .frame(minHeight: MurmurTheme.floatingDisc)
                        .background(MurmurTheme.accent, in: Capsule())
                        .contentShape(Capsule())
                }
                .buttonStyle(MurmurPressStyle())
            default:
                EmptyView()
            }
        }
        .accessibilityIdentifier("listen-together-room-mutation")
    }

    // MARK: - The player

    private var player: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 8)
            cover
            VStack(spacing: 4) {
                // 只放歌名。`trackLine` 是给聊天页那张一行卡片用的，它自带
                // 歌手；在这里用会和下面那行的歌手撞一次。
                Text(netease.displayTrack?.title ?? ListenTogetherRoomSnapshotV1.noTrackLine)
                    .font(MurmurTheme.display(.title3))
                    .foregroundStyle(MurmurTheme.ink)
                    .multilineTextAlignment(.center)
                if let meta = metaLine {
                    Text(meta)
                        .font(MurmurTheme.body(.subheadline))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 24)

            HStack(spacing: 8) {
                if presentation == .playing {
                    MurmurLiveBars()
                        .frame(width: 15, height: 22)
                        .accessibilityHidden(true)
                }
                Text(presentation.statusLine)
                    .font(MurmurTheme.body(.footnote, weight: .medium))
                    .foregroundStyle(presentation.statusColor)
                    .multilineTextAlignment(.center)
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 16)

            if let notice = netease.room?.nonblockingNotice {
                Text(notice)
                    .font(MurmurTheme.body(.footnote, weight: .medium))
                    .foregroundStyle(MurmurTheme.coral)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
                    .accessibilityIdentifier("listen-together-rights-notice")
            }

            transport.padding(.top, 24)
            secondaryActions.padding(.top, 32)
            Spacer(minLength: 8)
        }
        .padding(.horizontal, MurmurTheme.pageInset)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: presentation)
    }

    @ViewBuilder
    private var cover: some View {
        let shape = RoundedRectangle(cornerRadius: MurmurTheme.corner, style: .continuous)
        CachedArtwork(url: netease.displayTrack?.artworkURL) {
            // 中性灰，不是 accent。这块有 240pt，铺成青色远超 murmur-ui 的
            // accent 预算，而且和已上线的 `MusicCardView` 占位不是一个样子。
            shape.fill(MurmurTheme.secondaryInk.opacity(0.14))
                .overlay {
                    Image(systemName: "music.note")
                        .font(.system(size: 42, weight: .light))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                }
        }
        .frame(maxWidth: 240, maxHeight: 240)
        .aspectRatio(1, contentMode: .fit)
        .clipShape(shape)
        // 邀请过期的时候封面退到后面去：这张图还在，但它已经不是「正在放」了。
        .opacity(presentation.isRoomFailure ? 0.45 : 1)
        .accessibilityHidden(true)
    }

    private var transport: some View {
        HStack(spacing: 28) {
            if showsSideKeys {
                quietKey("backward.fill", label: "上一首") {
                    Task { await netease.command(.previous) }
                }
            }
            primaryKey
            if showsSideKeys {
                quietKey("forward.fill", label: "下一首") {
                    Task { await netease.command(.next) }
                }
            }
        }
    }

    /// 还没有人加入、或者邀请已经过期的时候，切歌切给谁听？一个按下去什么都
    /// 不会发生的键比没有这个键更糟。
    private var showsSideKeys: Bool {
        switch presentation {
        case .playing, .paused, .syncing, .commandFailed, .offline: true
        case .creating, .creationFailed, .waiting, .roomFailed, .inactive: false
        }
    }

    private var sideKeysEnabled: Bool {
        presentation != .offline && !presentation.isSyncing
    }

    private func quietKey(
        _ symbol: String, label: String, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(MurmurTheme.body(.subheadline, weight: .semibold))
                .foregroundStyle(MurmurTheme.ink)
                .frame(width: 46, height: 46)
                .background(MurmurTheme.raisedPaper.opacity(0.74), in: Circle())
                .overlay { Circle().stroke(MurmurTheme.rule, lineWidth: 1) }
                .contentShape(Circle())
        }
        .buttonStyle(MurmurPressStyle())
        .disabled(!sideKeysEnabled)
        .opacity(sideKeysEnabled ? 1 : 0.4)
        .accessibilityLabel(label)
    }

    private var primaryKey: some View {
        Button(action: performPrimaryAction) {
            Group {
                if presentation.isSyncing {
                    ProgressView().controlSize(.regular).tint(MurmurTheme.onAccent)
                } else {
                    Image(systemName: presentation.primarySymbol)
                        .font(.system(size: 24, weight: .semibold))
                }
            }
            // 全屏唯一一处填充 accent 的地方。
            .foregroundStyle(MurmurTheme.onAccent)
            .frame(width: 64, height: 64)
            .background(MurmurTheme.accent, in: Circle())
            .contentShape(Circle())
        }
        .buttonStyle(MurmurPressStyle())
        .disabled(presentation.isSyncing || presentation == .offline)
        .opacity(presentation == .offline ? 0.45 : 1)
        .accessibilityLabel(presentation.primaryAccessibilityLabel)
    }

    private var secondaryActions: some View {
        VStack(spacing: 16) {
            HStack(spacing: 12) {
                if music.isNeteaseSearchAvailable {
                    quietPill("换一首") { showPicker = true }
                        .disabled(model.connection.isOffline)
                }
                if netease.room?.inviteURL != nil {
                    quietPill("在网易云打开") { openInvite() }
                }
            }
            Button("结束一起听") { Task { await netease.closeRoom() } }
                .font(MurmurTheme.body(.subheadline, weight: .semibold))
                .foregroundStyle(MurmurTheme.coral)
                .frame(minHeight: MurmurTheme.floatingDisc)
                .buttonStyle(MurmurPressStyle())
                .disabled(presentation == .offline)
            if let failure = netease.failureMessage {
                Text(failure)
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.coral)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func quietPill(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .font(MurmurTheme.body(.subheadline, weight: .medium))
            .foregroundStyle(MurmurTheme.ink)
            .padding(.horizontal, 20)
            .frame(minHeight: MurmurTheme.floatingDisc)
            .background(MurmurTheme.raisedPaper, in: Capsule())
            .overlay { Capsule().stroke(MurmurTheme.rule, lineWidth: 1) }
            .buttonStyle(MurmurPressStyle())
    }

    // MARK: - Bits

    private var metaLine: String? {
        guard let track = netease.displayTrack else { return nil }
        let artists = track.artists.joined(separator: "、")
        guard let seconds = track.durationSeconds else {
            return artists.isEmpty ? nil : artists
        }
        let clock = MusicPlayerSheet.clock(TimeInterval(seconds))
        return artists.isEmpty ? clock : "\(artists) · \(clock)"
    }

    @ViewBuilder
    private func centred<Content: View>(
        @ViewBuilder _ content: () -> Content
    ) -> some View {
        VStack(spacing: 12) {
            Spacer(minLength: 0)
            content()
            Spacer(minLength: 0)
        }
        .frame(maxWidth: 320)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, MurmurTheme.pageInset)
    }

    // MARK: - Actions

    private func performPrimaryAction() {
        switch presentation {
        case .waiting:
            openInvite()
        case .playing:
            Task { await netease.command(.pause) }
        case .paused:
            Task { await netease.command(.resume) }
        case .commandFailed:
            Task { _ = await netease.retryLastAction() }
        case .creationFailed:
            Task {
                if let invite = await netease.retryLastAction() { openURL(invite) }
            }
        case .roomFailed:
            guard let track = netease.room?.currentTrack else { return }
            start(track)
        case .inactive, .creating, .syncing, .offline:
            break
        }
    }

    private func start(_ track: MusicTrackAttachmentV1) {
        if let current = netease.room, current.isActive,
           current.currentTrack?.trackID == track.trackID {
            if !current.userJoined, let invite = current.inviteURL {
                openURL(invite)
            }
            return
        }
        guard let action = netease.prepareRoomAction(for: track) else { return }
        Task {
            // nil 表示「你已经在房间里了」，不必再被甩去网易云一次。
            if let invite = await netease.performPreparedRoomAction(action) {
                openURL(invite)
            }
        }
    }

    private func openInvite() {
        guard let invite = netease.room?.inviteURL else { return }
        openURL(invite)
    }
}

// MARK: - Picking a song for the room

/// 网易云搜索。
///
/// 和 `MusicPickerModel` 分开而不是给它加第四个来源：那边整套是围着 Audius
/// 的偏移分页、账号和空态文案长的，而网易云这边一次最多五条、没有账号、没有
/// 下一页。硬塞进去会让两边都别扭，也会动到九个已有的测试。
@MainActor
final class NeteaseSearchModel: ObservableObject {
    enum Phase: Equatable {
        case idle
        case searching
        case results([MusicTrackAttachmentV1])
        case failed(String)
    }

    @Published var query = ""
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var emptyReason: MusicSearchEmptyReason?

    private let api: any MurmurAPIClient
    private let purpose: MusicSearchPurpose
    private var inFlight: Task<Void, Never>?

    init(
        api: any MurmurAPIClient,
        purpose: MusicSearchPurpose = .listenTogether
    ) {
        self.api = api
        self.purpose = purpose
    }

    /// 只在提交时搜，不做逐键防抖。网易云那条路是非官方接口、跑在一个可丢弃
    /// 的小号上；每敲一个字发一次请求是最便宜的把账号打进风控的方式。
    func search() {
        let clean = query.trimmingCharacters(in: .whitespacesAndNewlines)
        inFlight?.cancel()
        guard !clean.isEmpty else {
            phase = .idle
            emptyReason = nil
            return
        }
        phase = .searching
        emptyReason = nil
        inFlight = Task {
            do {
                let result = try await api.searchMusic(
                    query: clean, limit: 5, purpose: purpose
                )
                guard !Task.isCancelled else { return }
                emptyReason = result.emptyReason
                phase = .results(result.tracks)
            } catch {
                guard !Task.isCancelled else { return }
                phase = .failed(MurmurFailure.from(error).message)
            }
        }
    }
}

struct NeteaseSearchSheet: View {
    let api: any MurmurAPIClient
    let purpose: MusicSearchPurpose
    /// 同一个搜索面板，两个去处：一起听那屏拿它开房间，composer 拿它发一张卡。
    /// 按钮上的词得说清楚按下去会发生什么。
    let actionLabel: String
    let onPick: (MusicTrackAttachmentV1) -> Void

    @StateObject private var model: NeteaseSearchModel
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focused: Bool

    init(
        api: any MurmurAPIClient,
        purpose: MusicSearchPurpose = .listenTogether,
        actionLabel: String = "一起听",
        onPick: @escaping (MusicTrackAttachmentV1) -> Void
    ) {
        self.api = api
        self.purpose = purpose
        self.actionLabel = actionLabel
        self.onPick = onPick
        _model = StateObject(wrappedValue: NeteaseSearchModel(api: api, purpose: purpose))
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                field
                Divider().padding(.horizontal, MurmurTheme.pageInset)
                results
            }
            .background(MurmurTheme.paper.ignoresSafeArea())
            .navigationTitle("选一首歌")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
        }
        .presentationDetents([.large])
        .onAppear { focused = true }
    }

    private var field: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(MurmurTheme.secondaryInk)
                .accessibilityHidden(true)
            TextField("搜网易云的歌", text: $model.query)
                .font(MurmurTheme.body(.body))
                .focused($focused)
                .submitLabel(.search)
                .onSubmit { model.search() }
                .accessibilityIdentifier("netease-search-field")
        }
        .padding(.horizontal, 14)
        .frame(minHeight: MurmurTheme.floatingDisc)
        .background(MurmurTheme.raisedPaper, in: Capsule())
        .overlay { Capsule().stroke(MurmurTheme.rule, lineWidth: 1) }
        .padding(MurmurTheme.pageInset)
    }

    @ViewBuilder
    private var results: some View {
        switch model.phase {
        case .idle:
            message("搜歌名或歌手，按回车。")
        case .searching:
            VStack {
                Spacer()
                ProgressView()
                Spacer()
            }
        case .results(let tracks) where tracks.isEmpty:
            if model.emptyReason == .noCommonPlayableTrack {
                message("找到了，但没有双方都能播放的版本。")
            } else {
                message("没找到这首。换个说法试试。")
            }
        case .results(let tracks):
            List(tracks) { track in
                PickerTrackRow(
                    track: track,
                    actionLabel: actionLabel,
                    actionName: actionLabel == "一起听" ? "和 Murmur 一起听" : "发送给 Murmur"
                ) {
                    onPick(track)
                    dismiss()
                }
                .listRowBackground(MurmurTheme.paper)
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
        case .failed(let reason):
            VStack(spacing: 12) {
                Spacer()
                Text(reason)
                    .font(MurmurTheme.body(.subheadline))
                    .foregroundStyle(MurmurTheme.coral)
                    .multilineTextAlignment(.center)
                Button("再试一次") { model.search() }
                    .font(MurmurTheme.body(.subheadline, weight: .semibold))
                    .foregroundStyle(MurmurTheme.accentInk)
                    .frame(minHeight: MurmurTheme.floatingDisc)
                Spacer()
            }
            .padding(MurmurTheme.pageInset)
        }
    }

    private func message(_ text: String) -> some View {
        VStack {
            Spacer()
            Text(text)
                .font(MurmurTheme.body(.subheadline))
                .foregroundStyle(MurmurTheme.secondaryInk)
                .multilineTextAlignment(.center)
            Spacer()
        }
        .padding(MurmurTheme.pageInset)
    }
}
