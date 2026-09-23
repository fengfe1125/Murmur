import SwiftUI

/// A compact player inset above the system tab bar.
struct MusicMiniPlayer: View {
    @ObservedObject var player: MusicPlaybackController
    let onOpen: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ScaledMetric(relativeTo: .footnote) private var artworkSide: CGFloat = 34

    var body: some View {
        if let track = player.track, player.state.isActive {
            HStack(spacing: 10) {
                Button(action: onOpen) {
                HStack {
                artwork(for: track)
                VStack(alignment: .leading, spacing: 1) {
                    Text(track.title)
                        .font(MurmurTheme.body(.footnote, weight: .semibold))
                        .foregroundStyle(MurmurTheme.ink)
                        .lineLimit(1)
                    Text(track.artists.joined(separator: "、"))
                        .font(MurmurTheme.body(.caption2))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                        .lineLimit(1)
                }
                }
                }.buttonStyle(.plain).accessibilityLabel("打开播放器，\(track.title)")
                Spacer(minLength: 4)
                MusicTransportButton(state: player.state) { player.togglePlayPause() }
                Button {
                    player.stop()
                } label: {
                    Image(systemName: "xmark")
                        .font(MurmurTheme.body(.caption, weight: .semibold))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                        .frame(width: MurmurTheme.floatingDisc, height: MurmurTheme.floatingDisc)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("停止播放")
            }
            .padding(.leading, 10)
            .padding(.trailing, 2)
            .padding(.vertical, 6)
            .background(.regularMaterial)
            .contentShape(RoundedRectangle(cornerRadius: MurmurTheme.corner, style: .continuous))
            .padding(.horizontal, MurmurTheme.pageInset)
            .transition(.opacity)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("正在播放 \(track.title)")
            .accessibilityHint("轻点打开播放器")
        }
    }

    @ViewBuilder
    private func artwork(for track: MusicTrackAttachmentV1) -> some View {
        let shape = RoundedRectangle(cornerRadius: 7, style: .continuous)
        CachedArtwork(url: track.artworkURL) {
            shape.fill(MurmurTheme.secondaryInk.opacity(0.18))
        }
        .frame(width: artworkSide, height: artworkSide)
        .clipShape(shape)
    }
}

/// Play / pause / spinner, in one control, so every surface that offers the
/// transport offers the same one.
struct MusicTransportButton: View {
    let state: MusicPlaybackState
    var diameter: CGFloat = MurmurTheme.disc
    let action: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Button(action: action) {
            Group {
                switch state {
                case .loading, .buffering:
                    ProgressView().controlSize(.small)
                case .playing:
                    Image(systemName: "pause.fill")
                default:
                    Image(systemName: "play.fill")
                }
            }
            .font(diameter > 44 ? .title2 : .body)
        }
        .buttonStyle(.bordered)
        .buttonBorderShape(.circle)
        .controlSize(.large)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.15), value: state)
        .accessibilityLabel(state == .playing ? "暂停" : "播放")
    }
}

/// The full player: cover, name, a scrubber, and the way back out to Audius.
///
/// No queue, no next, no shuffle — there is one song and the plan says so. A
/// control that would do nothing is worse than no control.
struct MusicPlayerSheet: View {
    @ObservedObject var player: MusicPlaybackController
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    /// Where the finger has dragged to, while it is still down. The player's
    /// own clock keeps ticking during a scrub, so binding the slider straight
    /// to it would make the thumb fight the finger.
    @State private var scrubbing: TimeInterval?

    var body: some View {
        NavigationStack {
        ScrollView {
        VStack(spacing: 24) {
            if let track = player.track {
                artwork(for: track)
                VStack(spacing: 4) {
                    Text(track.title)
                        .font(MurmurTheme.display(.title3))
                        .foregroundStyle(MurmurTheme.ink)
                        .multilineTextAlignment(.center)
                    Text(track.artists.joined(separator: "、"))
                        .font(MurmurTheme.body(.subheadline))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                        .multilineTextAlignment(.center)
                }
                .fixedSize(horizontal: false, vertical: true)
                scrubber
                HStack(spacing: 24) {
                    MusicTransportButton(state: player.state, diameter: 56) {
                        player.togglePlayPause()
                    }
                    Button {
                        player.stop()
                        dismiss()
                    } label: {
                        Image(systemName: "stop.fill")
                            .font(MurmurTheme.body(.body, weight: .semibold))
                            .foregroundStyle(MurmurTheme.secondaryInk)
                            .frame(
                                width: MurmurTheme.floatingDisc,
                                height: MurmurTheme.floatingDisc
                            )
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("停止播放")
                }
                Button("在 Audius 打开") { openURL(track.canonicalURL) }
                    .font(MurmurTheme.body(.footnote, weight: .medium))
                    .foregroundStyle(MurmurTheme.accentInk)
                    .frame(minHeight: 44)
            } else {
                Text("现在没有在放的歌")
                    .font(MurmurTheme.body(.body))
                    .foregroundStyle(MurmurTheme.secondaryInk)
            }
        }
        .padding(MurmurTheme.pageInset)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        .background(MurmurTheme.paper)
        }
        .navigationTitle("正在播放")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }

    @ViewBuilder
    private func artwork(for track: MusicTrackAttachmentV1) -> some View {
        let shape = RoundedRectangle(cornerRadius: MurmurTheme.corner, style: .continuous)
        CachedArtwork(url: track.artworkURL) {
            shape.fill(MurmurTheme.secondaryInk.opacity(0.14))
                .overlay {
                    Image(systemName: "music.note")
                        .font(MurmurTheme.display(.title))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                }
        }
        .frame(maxWidth: 220, maxHeight: 220)
        .aspectRatio(1, contentMode: .fit)
        .clipShape(shape)
    }

    @ViewBuilder
    private var scrubber: some View {
        let total = player.duration ?? 0
        VStack(spacing: 4) {
            Slider(
                value: Binding(
                    get: { scrubbing ?? player.elapsed },
                    set: { scrubbing = $0 }
                ),
                in: 0...max(total, 1),
                onEditingChanged: { editing in
                    guard !editing, let target = scrubbing else { return }
                    player.seek(to: target)
                    scrubbing = nil
                }
            )
            .tint(MurmurTheme.accent)
            .disabled(total <= 0)
            HStack {
                Text(Self.clock(scrubbing ?? player.elapsed))
                Spacer()
                Text(Self.clock(total))
            }
            // Monospaced, so a counting label does not twitch.
            .font(MurmurTheme.body(.caption2).monospacedDigit())
            .foregroundStyle(MurmurTheme.secondaryInk)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("播放进度")
    }

    static func clock(_ seconds: TimeInterval) -> String {
        guard seconds.isFinite, seconds >= 0 else { return "0:00" }
        let whole = Int(seconds.rounded(.down))
        return String(format: "%d:%02d", whole / 60, whole % 60)
    }
}
