import SwiftUI

/// The three places the app has.
///
/// 当年今日 used to be a disc floating over the conversation and settings used
/// to be another one.  Both were doors on top of the chat, which made the chat
/// the whole app and everything else a detour off it.  They are siblings now.
enum MurmurTab: String, CaseIterable, Identifiable {
    case chat
    case onThisDay
    case me

    var id: String { rawValue }

    var title: String {
        switch self {
        case .chat: "聊天"
        case .onThisDay: "当年今日"
        case .me: "我的"
        }
    }

    var symbol: String {
        switch self {
        case .chat: "bubble.left.fill"
        case .onThisDay: "calendar"
        case .me: "person.fill"
        }
    }
}

private struct MurmurTabBarClearanceKey: EnvironmentKey {
    static let defaultValue: CGFloat = 0
}

extension EnvironmentValues {
    var murmurTabBarClearance: CGFloat {
        get { self[MurmurTabBarClearanceKey.self] }
        set { self[MurmurTabBarClearanceKey.self] = newValue }
    }
}

/// The shell: the gates that must own the whole screen, and otherwise the three
/// tabs with one glass bar under them.
///
/// Named apart from `MurmurRootView` in `MurmurApp.swift`, which is the session
/// lifecycle's host rather than anything the reader sees.
struct MurmurShell: View {
    @ObservedObject var model: MurmurSessionModel
    @ObservedObject var music: MusicModule
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @State private var tab: MurmurTab = .chat
    @State private var showPlayer = false
    /// Window-local keyboard geometry. A second scene owns a second state, so
    /// neither can move the other's composer with a late transition.
    @StateObject private var keyboard = MurmurKeyboardState()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Measured rather than assumed: the bar grows with Dynamic Type, and the
    /// content has to be inset by whatever it actually became.
    @State private var barHeight: CGFloat = 72
    /// The album shelf, held above the tab that shows it: 当年今日 is torn down
    /// when you leave it, and rebuilding this would re-read the library every
    /// time you came back.
    @StateObject private var onThisDay = OnThisDayModel()

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

    private var barIsFolded: Bool { keyboard.overlap > 0.5 }

    private var tabs: some View {
        // One tab on screen at a time, and only that one in the tree.
        //
        // Stacking all three and hiding two was the first attempt, and it does
        // not hold: `accessibilityHidden` left every control of every off-screen
        // tab reachable, so a VoiceOver reader on 聊天 could swipe into 设置's
        // switches — see `testTheTabsNotOnScreenAreOutOfReach`.  Rendering one
        // is correct by construction.  What state matters survives elsewhere:
        // the draft and the scrollback live in the session, and the album shelf
        // is held here rather than inside the tab so coming back is free.
        //
        // Deliberately not a `TabView` either: its page hosting reframes the
        // child, and the chat's composer — which lives in a bottom
        // `safeAreaInset` — simply stopped being laid out inside one.
        Group {
            switch tab {
            case .chat:
                MurmurChatView(model: model, music: music)
                    .environmentObject(notifications)
            case .onThisDay:
                OnThisDayTabView(model: model, onThisDay: onThisDay)
            case .me:
                MurmurSettingsView(model: model, music: music)
                    .environmentObject(notifications)
            }
        }
        .environment(\.murmurTabBarClearance, barHeight)
        // The tap that moves the pill runs inside `withAnimation`, and a
        // conditional swap caught by one of those gets SwiftUI's default
        // opacity transition for free — the whole screen would cross-fade
        // under a bar that is only meant to flow.  The screen cuts; the bar
        // flows.
        .transaction { $0.animation = nil }
        // The bar floats over the tabs and the content is inset by exactly its
        // height.  A `safeAreaInset` on the TabView itself does not reach the
        // pages inside it — the chat's own composer inset simply stopped being
        // laid out — so the two halves are stated separately here.
        .safeAreaPadding(.bottom, barHeight)
        // SwiftUI's own keyboard avoidance would be a second, differently
        // timed motion on top of the composer's own lift.  Stated here rather
        // than inside the workbench: inside a TabView page that modifier let
        // the page grow past its own bounds and took the composer with it.
        .ignoresSafeArea(.keyboard, edges: .bottom)
        .overlay(alignment: .bottom) {
            VStack(spacing: 8) {
                // Rides just above the bar and folds away with it: the keyboard
                // takes the whole floor, and a strip left behind over a raised
                // composer would be a second thing floating in the same place.
                MusicMiniPlayer(player: music.player) { showPlayer = true }
                MurmurTabBar(selection: $tab)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: {
                        barHeight = $0
                    }
            }
            .offset(y: barIsFolded ? barHeight + 40 : 0)
            .opacity(barIsFolded ? 0 : 1)
            .allowsHitTesting(!barIsFolded)
            .animation(
                reduceMotion ? .easeInOut(duration: 0.15) : .easeInOut(duration: 0.22),
                value: barIsFolded
            )
            .animation(
                reduceMotion ? nil : .spring(response: 0.34, dampingFraction: 0.86),
                value: music.nowPlaying
            )
        }
        .sheet(isPresented: $showPlayer) {
            MusicPlayerSheet(player: music.player)
        }
    }
}

/// One pane of glass with three stops on it, and one pill that travels between
/// them rather than blinking out on one and in on the next.
///
/// The pill's place is arithmetic: three stops divide the row equally, so the
/// selected one begins at `stopWidth * index` and is `stopWidth` across.
/// Animating that offset is the travel.
///
/// It was `matchedGeometryEffect` before — each stop publishing its frame to
/// one follower pill — and both faults came from there.  A follower has no
/// frame of its own, so on the tick where the outgoing source has gone and the
/// incoming one has not been measured yet it takes the size it is offered,
/// which is the whole bar; the next frame contracts it onto the new stop.
/// Tapping the middle therefore read as both ends flowing inward rather than
/// as the pill crossing over from where it stood.  The rest of the travel then
/// cost a layout round trip per frame, which is the stutter.  Arithmetic has
/// neither problem: the pill has a definite frame on every frame, the first
/// one included.
///
/// An earlier attempt gave each stop its own conditional pill matched by
/// `glassEffectID`; recorded frame by frame that is a cross-fade — the old
/// pill fading out where it stood while the new one faded in where it stood.
/// That lesson still holds and is why there is exactly one pill here: a view
/// that is never inserted or removed has no fade available to it and has to
/// move.
///
/// On iOS 26 that one travelling shape is real glass, so it refracts and
/// wobbles as it goes; before that it is a plain tinted capsule that slides.
struct MurmurTabBar: View {
    @Binding var selection: MurmurTab
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// One stop's width, measured from the row rather than assumed: the bar is
    /// as wide as the window less its margins, and it grows with Dynamic Type.
    @State private var stopWidth: CGFloat = 0

    var body: some View {
        stops
            .background(alignment: .leading) { travellingPill }
            .padding(.horizontal, 8)
            .padding(.vertical, 8)
            .murmurGlass(radius: MurmurTheme.tabBarCorner)
            .padding(.horizontal, 16)
            .padding(.bottom, 6)
    }

    private var selectedIndex: Int {
        MurmurTab.allCases.firstIndex(of: selection) ?? 0
    }

    /// The one pill: never inserted, never removed, only moved.
    private var travellingPill: some View {
        pillShape
            .frame(width: stopWidth)
            .offset(x: stopWidth * CGFloat(selectedIndex))
            // Nothing to draw before the row has been measured.  A full-width
            // capsule for a single frame at launch is the very artefact this
            // is here to remove.
            .opacity(stopWidth > 0 ? 1 : 0)
    }

    @ViewBuilder
    private var pillShape: some View {
        if #available(iOS 26.0, *) {
            Capsule()
                .fill(.clear)
                .glassEffect(.regular.tint(MurmurTheme.accent), in: Capsule())
        } else {
            Capsule().fill(MurmurTheme.accent)
        }
    }

    private var stops: some View {
        HStack(spacing: 0) {
            ForEach(MurmurTab.allCases) { tab in
                MurmurTabButton(tab: tab, isSelected: selection == tab) {
                    guard selection != tab else { return }
                    // A spring rather than a curve, and an underdamped one: the
                    // pill arrives, overshoots a hair and settles, which is what
                    // reads as liquid rather than as a slide.  Reduce Motion
                    // takes the travel away and leaves the colours to change.
                    withAnimation(
                        reduceMotion ? nil : .spring(response: 0.42, dampingFraction: 0.72)
                    ) {
                        selection = tab
                    }
                }
            }
        }
        // The width is measured out of any animation on purpose.  It changes on
        // rotation and on Dynamic Type, never on a tap, and one caught by the
        // tap's spring would stretch the pill on its way across.
        .onGeometryChange(for: CGFloat.self) {
            $0.size.width / CGFloat(MurmurTab.allCases.count)
        } action: { width in
            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) { stopWidth = width }
        }
        // Deliberately no identifier on the bar itself: one here overrides
        // every child's, and all three stops came back as "murmur-tab-bar".
    }
}

private struct MurmurTabButton: View {
    let tab: MurmurTab
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Image(systemName: tab.symbol)
                    .font(.system(size: 17, weight: isSelected ? .semibold : .regular))
                    .frame(height: 22)
                Text(tab.title)
                    .font(MurmurTheme.body(.caption2, weight: isSelected ? .semibold : .regular))
            }
            // The glyph and the label stay put and only change colour: the
            // motion belongs to the pill, and two things moving at once reads
            // as jitter rather than flow.  The weight change is the second
            // signal the colour cannot carry on its own.
            .foregroundStyle(isSelected ? MurmurTheme.onAccent : MurmurTheme.secondaryInk)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(MurmurPressStyle())
        .accessibilityLabel(tab.title)
        .accessibilityAddTraits(isSelected ? [.isSelected, .isButton] : .isButton)
        .accessibilityIdentifier("tab-\(tab.rawValue)")
    }
}
