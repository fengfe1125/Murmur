import SwiftUI

/// What one card should show about playback.
///
/// Not the player's whole state machine: a card only needs to know whether the
/// button under its finger is a play button, a pause button, a spinner, or a
/// sentence explaining that this song is gone.
enum MusicCardPlayback: Equatable, Sendable {
    case stopped
    case loading
    case playing
    case paused
    case unavailable

    init(playerState: MusicPlaybackState, isCurrent: Bool) {
        guard isCurrent else { self = .stopped; return }
        self = switch playerState {
        case .loading, .buffering: .loading
        case .playing: .playing
        case .paused: .paused
        case .unavailable: .unavailable
        case .idle, .ended, .failed: .stopped
        }
    }
}

/// One song, sitting in the scrollback like any other message.
///
/// It draws only what the sender's snapshot recorded. Nothing here reaches the
/// network to decide what to show: a card must read the same on a plane as it
/// did the day it arrived, and what is actually playable is re-checked at the
/// moment somebody presses play, not now.
struct MusicCardView: View {
    let track: MusicTrackAttachmentV1
    let isOutgoing: Bool
    let playback: MusicCardPlayback
    let onPlay: () -> Void
    let onOpenProvider: () -> Void
    var onListenTogether: (() -> Void)?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ScaledMetric(relativeTo: .body) private var artworkSide: CGFloat = 56

    private var foreground: Color { isOutgoing ? MurmurTheme.onAccent : MurmurTheme.ink }
    private var secondary: Color {
        isOutgoing ? MurmurTheme.onAccent.opacity(0.72) : MurmurTheme.secondaryInk
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 12) {
                artwork
                VStack(alignment: .leading, spacing: 2) {
                    Text(track.title)
                        .font(MurmurTheme.body(.subheadline, weight: .semibold))
                        .foregroundStyle(foreground)
                        .lineLimit(2)
                    Text(track.artists.joined(separator: "、"))
                        .font(MurmurTheme.body(.footnote))
                        .foregroundStyle(secondary)
                        .lineLimit(1)
                }
                .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if track.isAudius, playback != .unavailable { playButton }
            }
            if track.isAudius, playback == .unavailable {
                // The card stays; only the sound is gone. Saying so beats a
                // play button that does nothing.
                Text("这首歌现在不可播放")
                    .font(MurmurTheme.body(.caption))
                    .foregroundStyle(isOutgoing ? MurmurTheme.onAccent.opacity(0.8) : MurmurTheme.coral)
            }
            HStack(spacing: 14) {
                Button(action: onOpenProvider) {
                    Text(track.isNetease ? "在网易云打开" : "在 Audius 打开")
                        .font(MurmurTheme.body(.caption2, weight: .medium))
                        .foregroundStyle(isOutgoing ? MurmurTheme.onAccent : MurmurTheme.accentInk)
                }
                .buttonStyle(.plain)
                .frame(minHeight: 44, alignment: .leading)

                if track.isNetease, let onListenTogether {
                    Button(action: onListenTogether) {
                        Text("和 Murmur 一起听")
                            .font(MurmurTheme.body(.caption2, weight: .semibold))
                            .foregroundStyle(isOutgoing ? MurmurTheme.onAccent : MurmurTheme.accentInk)
                    }
                    .buttonStyle(.plain)
                    .frame(minHeight: 44, alignment: .leading)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: 300, alignment: .leading)
        .background(
            isOutgoing ? MurmurTheme.outgoingBubble : MurmurTheme.raisedPaper,
            in: RoundedRectangle(cornerRadius: MurmurTheme.corner, style: .continuous)
        )
        .overlay {
            if !isOutgoing {
                RoundedRectangle(cornerRadius: MurmurTheme.corner, style: .continuous)
                    .stroke(MurmurTheme.rule, lineWidth: 1)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityAddTraits(track.isAudius ? .isButton : [])
        .accessibilityAction {
            if track.isAudius, playback != .unavailable { onPlay() }
        }
        .accessibilityAction(
            named: track.isNetease ? "在网易云打开" : "在 Audius 打开",
            onOpenProvider
        )
        .accessibilityAction(named: "和 Murmur 一起听") {
            if track.isNetease { onListenTogether?() }
        }
    }

    @ViewBuilder
    private var artwork: some View {
        let shape = RoundedRectangle(cornerRadius: 10, style: .continuous)
        AsyncImage(url: track.artworkURL) { image in
            image.resizable().aspectRatio(contentMode: .fill)
        } placeholder: {
            // A song with no cover still has a shape, so the row does not
            // reflow when the image arrives.
            shape.fill(secondary.opacity(0.18))
                .overlay {
                    Image(systemName: "music.note")
                        .font(MurmurTheme.body(.footnote))
                        .foregroundStyle(secondary)
                }
        }
        .frame(width: artworkSide, height: artworkSide)
        .clipShape(shape)
    }

    private var playButton: some View {
        Button(action: onPlay) {
            Group {
                switch playback {
                case .loading:
                    ProgressView().controlSize(.small)
                case .playing:
                    Image(systemName: "pause.fill")
                case .stopped, .paused, .unavailable:
                    Image(systemName: "play.fill")
                }
            }
            .font(MurmurTheme.body(.subheadline, weight: .semibold))
            .foregroundStyle(isOutgoing ? MurmurTheme.onAccent : MurmurTheme.accentInk)
            // A 36pt disc inside a 44pt target: the circle is what is drawn,
            // the padding is what the finger gets.
            .frame(width: MurmurTheme.disc, height: MurmurTheme.disc)
            .background(
                (isOutgoing ? MurmurTheme.onAccent.opacity(0.14)
                            : MurmurTheme.accent.opacity(0.16)),
                in: Circle()
            )
            .padding(4)
        }
        .buttonStyle(.plain)
        .contentShape(Circle())
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.15), value: playback)
    }

    private var accessibilityLabel: String {
        let who = isOutgoing ? "你分享的歌曲" : "Murmur 分享的歌曲"
        let names = track.artists.joined(separator: "、")
        let status: String
        if track.isAudius {
            status = switch playback {
            case .playing: "，正在播放"
            case .paused: "，已暂停"
            case .loading: "，正在载入"
            case .unavailable: "，现在不可播放"
            case .stopped: ""
            }
        } else {
            status = "，在网易云中播放"
        }
        return "\(who)，\(track.title)，\(names)\(status)"
    }
}
