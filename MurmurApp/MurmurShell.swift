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

/// The shell: the gates that must own the whole screen, and otherwise the three
/// tabs with one glass bar under them.
///
/// Named apart from `MurmurRootView` in `MurmurApp.swift`, which is the session
/// lifecycle's host rather than anything the reader sees.
struct MurmurShell: View {
    @ObservedObject var model: MurmurSessionModel
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @State private var tab: MurmurTab = .chat
    /// The keyboard folds the bar away.  Without that the composer would come
    /// to rest a bar's height above the keyboard: the composer lifts by exactly
    /// what the keyboard takes, measured from wherever it was resting, and the
    /// bar is part of where it was resting.  See `MurmurKeyboardInset`.
    @ObservedObject private var keyboard = MurmurKeyboardInset.shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Measured rather than assumed: the bar grows with Dynamic Type, and the
    /// content has to be inset by whatever it actually became.
    @State private var barHeight: CGFloat = 72

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
        .tint(MurmurTheme.accentInk)
    }

    private var barIsFolded: Bool { keyboard.overlap > 0 }

    /// One tab, kept in the tree whether or not it is the one on screen.
    /// Hidden tabs are taken out of the accessibility tree as well as off the
    /// glass: a VoiceOver reader swiping through the chat should never land in
    /// 设置 because it happens to be stacked behind.
    @ViewBuilder
    private func tabContent<Content: View>(
        _ which: MurmurTab, @ViewBuilder content: () -> Content
    ) -> some View {
        let isOn = tab == which
        content()
            .opacity(isOn ? 1 : 0)
            .allowsHitTesting(isOn)
            // Both, and in this order.  `accessibilityHidden` alone left every
            // control of every off-screen tab in the tree — a VoiceOver reader
            // on 聊天 could swipe straight into 设置's switches, and XCUITest
            // could see them too.
            .accessibilityElement(children: isOn ? .contain : .ignore)
            .accessibilityHidden(!isOn)
            .zIndex(isOn ? 1 : 0)
    }

    private var tabs: some View {
        // All three stay alive and stacked rather than being swapped in and
        // out, so each keeps its scroll position and its half-written line.
        // Deliberately not a `TabView`: its page hosting reframes the child,
        // and the chat's composer — which lives in a bottom `safeAreaInset` —
        // simply stopped being laid out inside one.
        ZStack {
            tabContent(.chat) {
                MurmurChatView(model: model).environmentObject(notifications)
            }
            tabContent(.onThisDay) {
                OnThisDayTabView(model: model)
            }
            tabContent(.me) {
                MurmurSettingsView(model: model).environmentObject(notifications)
            }
        }
        // The bar floats over the tabs and the content is inset by exactly its
        // height.  A `safeAreaInset` on the TabView itself does not reach the
        // pages inside it — the chat's own composer inset simply stopped being
        // laid out — so the two halves are stated separately here.
        .safeAreaPadding(.bottom, barIsFolded ? 0 : barHeight)
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

/// One pane of glass with three stops on it.
struct MurmurTabBar: View {
    @Binding var selection: MurmurTab

    var body: some View {
        HStack(spacing: 0) {
            ForEach(MurmurTab.allCases) { tab in
                MurmurTabButton(tab: tab, isSelected: selection == tab) {
                    guard selection != tab else { return }
                    selection = tab
                }
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 8)
        .murmurGlass(radius: MurmurTheme.tabBarCorner)
        .padding(.horizontal, 16)
        .padding(.bottom, 6)
        // Deliberately no identifier on the bar itself: one here overrides
        // every child's, and all three stops came back as "murmur-tab-bar".
    }
}

private struct MurmurTabButton: View {
    let tab: MurmurTab
    let isSelected: Bool
    let action: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Image(systemName: tab.symbol)
                    .font(.system(size: 17, weight: isSelected ? .semibold : .regular))
                    .frame(height: 22)
                Text(tab.title)
                    .font(MurmurTheme.body(.caption2, weight: isSelected ? .semibold : .regular))
            }
            // Selected is a filled pill, which is a shape change as well as a
            // colour one — the label goes bolder in the same breath, so the
            // state survives both colour blindness and a greyscale screenshot.
            .foregroundStyle(isSelected ? MurmurTheme.onAccent : MurmurTheme.secondaryInk)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background {
                if isSelected {
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .fill(MurmurTheme.accent)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(MurmurPressStyle())
        .animation(reduceMotion ? nil : .spring(response: 0.28, dampingFraction: 0.84), value: isSelected)
        .accessibilityLabel(tab.title)
        .accessibilityAddTraits(isSelected ? [.isSelected, .isButton] : .isButton)
        .accessibilityIdentifier("tab-\(tab.rawValue)")
    }
}
