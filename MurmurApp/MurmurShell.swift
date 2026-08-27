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
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @State private var tab: MurmurTab = .chat
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
                MurmurChatView(model: model).environmentObject(notifications)
            case .onThisDay:
                OnThisDayTabView(model: model, onThisDay: onThisDay)
            case .me:
                MurmurSettingsView(model: model).environmentObject(notifications)
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
            MurmurTabBar(selection: $tab)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { barHeight = $0 }
                .offset(y: barIsFolded ? barHeight + 40 : 0)
                .opacity(barIsFolded ? 0 : 1)
                .allowsHitTesting(!barIsFolded)
                .animation(
                    reduceMotion ? .easeInOut(duration: 0.15) : .easeInOut(duration: 0.22),
                    value: barIsFolded
                )
        }
    }
}

/// One pane of glass with three stops on it, and one pill that travels between
/// them rather than blinking out on one and in on the next.
///
/// The pill is a single view that is never inserted or removed — only its frame
/// changes, published by whichever stop is selected through
/// `matchedGeometryEffect`.  That is the whole trick, and it was arrived at the
/// hard way: giving each stop its own conditional pill and matching them by
/// `glassEffectID` looks right in principle and, recorded frame by frame, is a
/// cross-fade — the old pill fading out where it stood while the new one faded
/// in where it stood.  A view that is never removed has no fade available to it
/// and has to move.
///
/// On iOS 26 that one travelling shape is real glass, so it refracts and
/// wobbles as it goes; before that it is a plain tinted capsule that slides.
struct MurmurTabBar: View {
    @Binding var selection: MurmurTab
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var pill

    private static let pillID = "murmur-tab-pill"

    var body: some View {
        stops
            .background(alignment: .leading) { travellingPill }
            .padding(.horizontal, 8)
            .padding(.vertical, 8)
            .murmurGlass(radius: MurmurTheme.tabBarCorner)
            .padding(.horizontal, 16)
            .padding(.bottom, 6)
    }

    /// The one pill.  `isSource: false` makes it a follower: it takes the frame
    /// the selected stop publishes, and animating that frame is the travel.
    @ViewBuilder
    private var travellingPill: some View {
        if #available(iOS 26.0, *) {
            Capsule()
                .fill(.clear)
                .glassEffect(.regular.tint(MurmurTheme.accent), in: Capsule())
                .matchedGeometryEffect(id: Self.pillID, in: pill, isSource: false)
        } else {
            Capsule()
                .fill(MurmurTheme.accent)
                .matchedGeometryEffect(id: Self.pillID, in: pill, isSource: false)
        }
    }

    private var stops: some View {
        HStack(spacing: 0) {
            ForEach(MurmurTab.allCases) { tab in
                MurmurTabButton(
                    tab: tab,
                    isSelected: selection == tab,
                    pill: pill,
                    pillID: Self.pillID
                ) {
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
        // Deliberately no identifier on the bar itself: one here overrides
        // every child's, and all three stops came back as "murmur-tab-bar".
    }
}

private struct MurmurTabButton: View {
    let tab: MurmurTab
    let isSelected: Bool
    let pill: Namespace.ID
    let pillID: String
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
            // The selected stop publishes the frame; it draws nothing itself.
            .background {
                if isSelected {
                    Color.clear.matchedGeometryEffect(id: pillID, in: pill, isSource: true)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(MurmurPressStyle())
        .accessibilityLabel(tab.title)
        .accessibilityAddTraits(isSelected ? [.isSelected, .isButton] : .isButton)
        .accessibilityIdentifier("tab-\(tab.rawValue)")
    }
}
