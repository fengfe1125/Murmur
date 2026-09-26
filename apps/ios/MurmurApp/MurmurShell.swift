import SwiftUI

/// The four primary destinations.
///
/// 当年今日 used to be a disc floating over the conversation and settings used
/// to be another one.  Both were doors on top of the chat, which made the chat
/// the whole app and everything else a detour off it.  They are siblings now.
enum MurmurTab: String, CaseIterable, Identifiable {
    case chat
    case listenTogether
    case onThisDay
    case me

    var id: String { rawValue }

    var title: String {
        switch self {
        case .chat: "聊天"
        case .listenTogether: "一起听"
        case .onThisDay: "当年今日"
        case .me: "我的"
        }
    }

    var symbol: String {
        switch self {
        case .chat: "bubble.left.fill"
        case .listenTogether: "headphones"
        case .onThisDay: "calendar"
        case .me: "person.fill"
        }
    }
}

private struct MurmurTabSelectionKey: EnvironmentKey {
    static let defaultValue: @MainActor @Sendable (MurmurTab) -> Void = { _ in }
}

extension EnvironmentValues {
    var murmurSelectTab: @MainActor @Sendable (MurmurTab) -> Void {
        get { self[MurmurTabSelectionKey.self] }
        set { self[MurmurTabSelectionKey.self] = newValue }
    }
}

/// Native navigation; session work remains above individual tab lifecycles.
struct MurmurShell: View {
    @ObservedObject var model: MurmurSessionModel
    @ObservedObject var music: MusicModule
    /// Observed here, not only inside the chat, because the shell owns the room
    /// poll now: the task's restart key reads the room, and `music` does not
    /// republish when its `netease` changes.
    @ObservedObject private var netease: NeteaseMusicModel
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: MurmurTab = .chat
    @State private var showPlayer = false
    /// Window-local keyboard geometry. A second scene owns a second state, so
    /// neither can move the other's composer with a late transition.
    @StateObject private var keyboard = MurmurKeyboardState()
    /// Shared photo-library state survives tab switches.
    @StateObject private var onThisDay = OnThisDayModel()

    init(model: MurmurSessionModel, music: MusicModule) {
        self.model = model
        self.music = music
        self._netease = ObservedObject(wrappedValue: music.netease)
    }

    var body: some View {
        Group {
            if model.connection == .checking {
                ConnectionLoadingView()
            } else if model.identity == nil {
                EnrollmentView(model: model)
            } else if model.requiresDeviceReconnect {
                DeviceReconnectView(model: model)
            } else {
                tabs
            }
        }
        .background(MurmurTheme.paper.ignoresSafeArea())
        .overlay {
            MurmurKeyboardLayoutGuideProbe { overlap in
                keyboard.updateFromLayoutGuide(overlap: overlap)
            }
            .frame(width: 0, height: 0)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
        .environmentObject(keyboard)
        .tint(MurmurTheme.accentInk)
    }

    @ViewBuilder
    private var miniPlayer: some View {
        if keyboard.overlap <= 0.5 {
            MusicMiniPlayer(player: music.player) { showPlayer = true }
        }
    }

    private var tabs: some View {
        // Identifiers go on each Tab.  Set on a `.tabItem` label, they reached
        // the tab bar button on some launches and not on others.
        TabView(selection: $tab) {
            Tab("聊天", systemImage: MurmurTab.chat.symbol, value: MurmurTab.chat) {
                NavigationStack {
                    MurmurChatView(model: model, music: music)
                }
                .safeAreaInset(edge: .bottom, spacing: 0) { miniPlayer }
            }
            .accessibilityIdentifier("tab-chat")

            Tab("一起听", systemImage: MurmurTab.listenTogether.symbol, value: MurmurTab.listenTogether) {
                NavigationStack {
                    ListenTogetherTabView(model: model, music: music)
                }
                .safeAreaInset(edge: .bottom, spacing: 0) { miniPlayer }
            }
            .accessibilityIdentifier("tab-listenTogether")

            Tab("当年今日", systemImage: MurmurTab.onThisDay.symbol, value: MurmurTab.onThisDay) {
                OnThisDayTabView(model: model, onThisDay: onThisDay)
                    .safeAreaInset(edge: .bottom, spacing: 0) { miniPlayer }
            }
            .accessibilityIdentifier("tab-onThisDay")

            Tab("我的", systemImage: MurmurTab.me.symbol, value: MurmurTab.me) {
                MurmurSettingsView(model: model, music: music)
                    .safeAreaInset(edge: .bottom, spacing: 0) { miniPlayer }
            }
            .accessibilityIdentifier("tab-me")
        }
        .environment(\.murmurSelectTab) { tab = $0 }
        .sheet(isPresented: $showPlayer) {
            MusicPlayerSheet(player: music.player)
        }
        // Exactly one room poll, independent of which tab is visible.
        .task(id: NeteaseRoomPollingKey(
            roomHandle: netease.room?.roomHandle,
            isActive: netease.room?.isActive == true,
            mayPoll: music.isListenTogetherAvailable && scenePhase == .active
        )) {
            guard music.isListenTogetherAvailable, scenePhase == .active else { return }
            // 没有房间也要问：房间可能是聊天那条路在服务端建起来的，手机这边
            // 只有问了才知道。原来这里直接退出，于是「和 Murmur 一起听」之后
            // 顶部什么都不出现。
            let interval = netease.room?.isActive == true
                ? NeteaseRoomPolling.active
                : NeteaseRoomPolling.idle
            // 先取一次再进循环。原来是先睡后取，第一份状态要等满一个间隔。
            await netease.refreshRoom()
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: interval)
                } catch {
                    return
                }
                await netease.refreshRoom()
            }
        }
    }
}
