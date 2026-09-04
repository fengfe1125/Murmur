import SwiftUI

/// The chrome that floats over a screen, and the pieces more than one screen
/// needs.
///
/// These all began inside `MurmurChatView.swift` as `private` types, back when
/// the conversation was the only screen that floated anything over itself.
/// 一起听 now has its own tab and needs the same mark, the same discs and the
/// same bars, so they live here rather than being copied — a second copy of a
/// 44pt tap target is a second thing to get wrong.

// MARK: - The mark

struct MurmurMark: View {
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

// MARK: - Floating discs

/// A floating 44pt control: the whole disc is both the drawn shape and the
/// tap target, which is the part a navigation bar would not give up.
struct MurmurDisc<Content: View>: View {
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
struct MurmurFloatingDisc<Content: View>: View {
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
    func murmurDiscButtonStyle() -> some View {
        if #available(iOS 26.0, *) {
            self.buttonStyle(MurmurQuietStyle())
        } else {
            self.buttonStyle(MurmurPressStyle())
        }
    }
}

/// A button with no opinions: the interactive glass supplies all of the
/// press feedback, and a second one from the style would double it.
struct MurmurQuietStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View { configuration.label }
}

// MARK: - Playing indicator

struct MurmurLiveBars: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase = false

    var body: some View {
        HStack(alignment: .center, spacing: 3) {
            ForEach([10.0, 18.0, 13.0], id: \.self) { height in
                Capsule()
                    .fill(MurmurTheme.accent)
                    .frame(width: 3, height: reduceMotion ? height : (phase ? height : height * 0.55))
            }
        }
        .task {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 0.55).repeatForever(autoreverses: true)) {
                phase = true
            }
        }
    }
}

// MARK: - Room polling

/// 房间轮询的节奏。
///
/// 每一次轮询在服务端都会变成三个网易云请求，而那是非官方接口、用的是一个
/// 可丢弃的小号。所以「跟得紧」和「别把账号打进风控」是一对取舍，不能只顾
/// 前者——这两个数字是取舍的结果，不是随手填的。
enum NeteaseRoomPolling {
    /// 正在一起听：三秒一次，切歌和暂停能在一次呼吸之内跟上。
    static let active: Duration = .seconds(3)
    /// 手上没有房间：只是看看别处有没有建起来（聊天那条路会自己建房），
    /// 服务端这一路不碰网易云，很便宜。
    static let idle: Duration = .seconds(20)
}

struct NeteaseRoomPollingKey: Equatable {
    let roomHandle: String?
    /// 必须进 key。它本来只在循环条件里，于是房间一变成 failed/ended，循环
    /// 退出而 key 没变——任务永不重启，界面就永远停在那一刻。
    let isActive: Bool
    let mayPoll: Bool
}

// MARK: - The listen-together card

/// 聊天页左上角那张卡片。
///
/// 房间没开的时候它就是那颗 44pt 的 logo 圆盘；房间一活，它从原地向右长出一张
/// 玻璃卡：封面、歌名、状态、暂停、下一首。点卡片本体去「一起听」那一整屏，
/// 上一首、在网易云打开和结束都在那里。
///
/// **一个容器，不是两个分支。** `MurmurTabBar` 那段注释记着同一个教训：两个用
/// `glassEffectID` 配对的形状，逐帧录下来是交叉淡入而不是流动；一个从不被插入
/// 也从不被移除的视图没有淡入可用，只能移动。所以这里始终是同一个胶囊，宽度从
/// 44 长到内容需要的宽度——44 宽的胶囊就是一个圆。
struct ListenTogetherCard: View {
    let room: ListenTogetherRoomSnapshotV1?
    let presentation: ListenTogetherPresentationState
    let onPrimary: () -> Void
    let onNext: () -> Void
    let onOpen: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// 跟着字号长。288×56 是默认字号下的意图，不是一个常量——两行字加两个 44pt
    /// 的目标在 XXXL 下装不进 288。
    @ScaledMetric(relativeTo: .subheadline) private var linesWidth: CGFloat = 116

    private var isActive: Bool { presentation != .inactive }

    var body: some View {
        HStack(spacing: 8) {
            disc
            if isActive {
                lines
                MurmurCardKey(
                    symbol: presentation.primarySymbol,
                    spinning: presentation.isSyncing,
                    label: presentation.primaryAccessibilityLabel,
                    dimmed: presentation == .offline,
                    action: onPrimary
                )
                if presentation.allowsTrackSkipping {
                    MurmurCardKey(
                        symbol: "forward.fill",
                        spinning: false,
                        label: "下一首",
                        dimmed: presentation == .offline || presentation.isSyncing,
                        action: onNext
                    )
                }
            }
        }
        .padding(isActive ? EdgeInsets(top: 6, leading: 6, bottom: 6, trailing: 4)
                          : EdgeInsets())
        .background { glass }
        .overlay {
            if presentation.isFailure {
                Capsule().stroke(MurmurTheme.coral, lineWidth: 1)
            }
        }
        .animation(
            reduceMotion ? nil : .spring(response: 0.32, dampingFraction: 0.86),
            value: presentation
        )
    }

    @ViewBuilder
    private var glass: some View {
        if #available(iOS 26.0, *) {
            Capsule().fill(.clear).glassEffect(.regular.interactive(), in: Capsule())
        } else {
            Capsule()
                .fill(MurmurTheme.raisedPaper)
                .overlay { Capsule().strokeBorder(MurmurTheme.rule, lineWidth: 1) }
                .shadow(color: MurmurTheme.ink.opacity(0.08), radius: 6, y: 2)
        }
    }

    /// 圆盘里的东西换了，圆盘本身没换位置也没消失——那是「一次形变，不是一次
    /// 替换」里形变的那一半。
    @ViewBuilder
    private var disc: some View {
        Button(action: onOpen) {
            ZStack {
                if let artwork = room?.currentTrack?.artworkURL, isActive {
                    CachedArtwork(url: artwork) {
                        Circle().fill(MurmurTheme.accent.opacity(0.18))
                    }
                    .frame(width: 44, height: 44)
                    .clipShape(Circle())
                } else if isActive {
                    Circle().fill(MurmurTheme.accent.opacity(0.18))
                        .frame(width: 44, height: 44)
                        .overlay {
                            Image(systemName: "music.note")
                                .font(MurmurTheme.body(.subheadline, weight: .medium))
                                .foregroundStyle(MurmurTheme.accentInk)
                        }
                } else {
                    MurmurMark(size: 38).frame(width: 44, height: 44)
                }
                if isActive {
                    // Murmur 还在这儿：这一整个功能说的就是「它陪你听同一首」。
                    MurmurMark(size: 16)
                        .overlay { Circle().strokeBorder(MurmurTheme.paper, lineWidth: 1.5) }
                        .offset(x: 15, y: 15)
                }
            }
            .frame(width: 44, height: 44)
            .contentShape(Circle())
        }
        .murmurDiscButtonStyle()
        .accessibilityLabel(isActive ? "打开一起听" : "Murmur")
        .accessibilityHint(isActive ? "轻点查看正在一起听的歌" : "")
    }

    private var lines: some View {
        Button(action: onOpen) {
            VStack(alignment: .leading, spacing: 1) {
                Text(room?.currentTrack?.title ?? ListenTogetherRoomSnapshotV1.noTrackLine)
                    .font(MurmurTheme.body(.subheadline, weight: .medium))
                    .foregroundStyle(MurmurTheme.ink)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Text(presentation.statusLine)
                    .font(MurmurTheme.body(.caption2))
                    .foregroundStyle(presentation.statusColor)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .frame(maxWidth: linesWidth, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(MurmurPressStyle())
        .accessibilityLabel("正在一起听 \(room?.trackLine ?? ListenTogetherRoomSnapshotV1.noTrackLine)")
        .accessibilityHint("轻点打开一起听")
    }
}

/// 卡片上的一颗键：画出来 36pt，手指拿到的是 44pt。
///
/// murmur-ui 里最常漏的一条就是这个——`MurmurTheme.disc` 是画的直径，
/// `floatingDisc` 才是目标。
private struct MurmurCardKey: View {
    let symbol: String
    let spinning: Bool
    let label: String
    let dimmed: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Group {
                if spinning {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: symbol)
                        .font(MurmurTheme.body(.footnote, weight: .semibold))
                }
            }
            .foregroundStyle(MurmurTheme.ink)
            .frame(width: MurmurTheme.disc, height: MurmurTheme.disc)
            .background(MurmurTheme.raisedPaper.opacity(0.7), in: Circle())
            // 46，不是 44。一个正好 44 的 frame 在 XCUITest 里量出来是 43.9，
            // 于是「目标必须够 44」这条就差一点点不成立；这个功能里其他浮动
            // 控件本来也都是 46（一起听那屏的上一首/下一首就是），对齐它。
            .frame(width: 46, height: 46)
            .contentShape(Circle())
        }
        .buttonStyle(MurmurPressStyle())
        .disabled(dimmed || spinning)
        .opacity(dimmed ? 0.4 : 1)
        .accessibilityLabel(label)
    }
}
