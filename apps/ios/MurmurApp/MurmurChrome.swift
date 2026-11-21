import SwiftUI

/// Shared native controls and compact status presentation.

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

// MARK: - Playing indicator

struct MurmurLiveBars: View {
    var body: some View {
        Image(systemName: "waveform").foregroundStyle(MurmurTheme.accentInk)
            .accessibilityLabel("正在播放")
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

/// Compact room status and transport actions, shared with the conversation.
struct ListenTogetherCard: View {
    let room: ListenTogetherRoomSnapshotV1?
    let track: MusicTrackAttachmentV1?
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
        .background { surface }
        .overlay {
            if presentation.isFailure {
                Capsule().stroke(MurmurTheme.coral, lineWidth: 1)
            }
        }
        .animation(
            reduceMotion ? nil : MurmurMotion.content,
            value: presentation
        )
    }

    private var surface: some View {
        RoundedRectangle(cornerRadius: 12).fill(MurmurTheme.raisedPaper)
    }

    @ViewBuilder
    private var disc: some View {
        Button(action: onOpen) {
            ZStack {
                if let artwork = track?.artworkURL, isActive {
                    CachedArtwork(url: artwork) {
                        Circle().fill(MurmurTheme.secondaryInk.opacity(0.14))
                    }
                    .frame(width: 44, height: 44)
                    .clipShape(Circle())
                } else if isActive {
                    Circle().fill(MurmurTheme.secondaryInk.opacity(0.14))
                        .frame(width: 44, height: 44)
                        .overlay {
                            Image(systemName: "music.note")
                                .font(MurmurTheme.body(.subheadline, weight: .medium))
                                .foregroundStyle(MurmurTheme.secondaryInk)
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
        .buttonStyle(.automatic)
        .accessibilityLabel(isActive ? "打开一起听" : "Murmur")
        .accessibilityHint(isActive ? "轻点查看正在一起听的歌" : "")
    }

    private var lines: some View {
        Button(action: onOpen) {
            VStack(alignment: .leading, spacing: 1) {
                Text(track?.title ?? ListenTogetherRoomSnapshotV1.noTrackLine)
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
        .buttonStyle(.automatic)
        .accessibilityLabel("正在一起听 \(trackLine)")
        .accessibilityHint("轻点打开一起听")
    }

    private var trackLine: String {
        guard let track else { return ListenTogetherRoomSnapshotV1.noTrackLine }
        guard let artist = track.artists.first, !artist.isEmpty else { return track.title }
        return "\(track.title) · \(artist)"
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
        .buttonStyle(.automatic)
        .disabled(dimmed || spinning)
        .opacity(dimmed ? 0.4 : 1)
        .accessibilityLabel(label)
    }
}

/// Shared input presentation. Each caller owns its draft, focus and send policy.
struct MurmurComposer<Actions: View>: View {
    @Binding var text: String
    @FocusState.Binding var focused: Bool
    let placeholder: String
    let fieldLabel: String
    let fieldIdentifier: String
    let sendIdentifier: String
    let canSend: Bool
    let onSend: () -> Void
    var sendLabel = "发送"
    var onFocus: () -> Void = {}
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            actions
            TextField(placeholder, text: $text, axis: .vertical)
                .font(MurmurTheme.body())
                .foregroundStyle(MurmurTheme.ink)
                .lineLimit(1...4)
                .focused($focused)
                .submitLabel(.send)
                .onSubmit(submit)
                .onChange(of: text) { _, value in
                    guard value.contains("\n") else { return }
                    text = value.replacingOccurrences(of: "\n", with: "")
                    submit()
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 11)
                .frame(minHeight: 44)
                .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 22))
                .simultaneousGesture(TapGesture().onEnded(onFocus))
                .accessibilityLabel(fieldLabel)
                .accessibilityIdentifier(fieldIdentifier)
            Button(sendLabel, systemImage: "arrow.up", action: submit)
                .labelStyle(.iconOnly)
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.circle)
                .controlSize(.large)
                .frame(minWidth: 44, minHeight: 44)
                .disabled(!canSend)
                .accessibilityIdentifier(sendIdentifier)
        }
        .frame(maxWidth: MurmurTheme.contentWidth)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(MurmurTheme.paper)
        .accessibilitySortPriority(3)
        .onChange(of: focused) { _, value in if value { onFocus() } }
    }

    private func submit() {
        guard canSend else { return }
        onSend()
    }
}
