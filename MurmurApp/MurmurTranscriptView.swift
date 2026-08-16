import SwiftUI
import UIKit

// MARK: - Bubble shape

/// A rounded bubble with one squared-off corner on the speaker's side, the way
/// a tail reads without drawing an actual tail on every message.
private struct BubbleShape: Shape {
    let isOutgoing: Bool
    var radius: CGFloat = 18
    var tail: CGFloat = 5

    func path(in rect: CGRect) -> Path {
        Path(
            roundedRect: rect,
            cornerRadii: RectangleCornerRadii(
                topLeading: radius,
                bottomLeading: isOutgoing ? radius : tail,
                bottomTrailing: isOutgoing ? tail : radius,
                topTrailing: radius
            )
        )
    }
}

// MARK: - Delivery ticks

private struct DeliveryTicks: View {
    let state: MurmurDeliveryState

    var body: some View {
        switch state {
        case .sending:
            Image(systemName: "clock")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.7))
                .accessibilityLabel("发送中")
        case .failed:
            Image(systemName: "exclamationmark.circle")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(MurmurTheme.coral)
                .accessibilityLabel("发送失败")
        case .sent, .answered:
            // Two overlapping checks, WhatsApp-style: the second slides in and
            // the pair turns colour once Murmur starts composing.
            ZStack(alignment: .leading) {
                Image(systemName: "checkmark")
                    .offset(x: state == .answered ? 0 : 3)
                Image(systemName: "checkmark")
                    .offset(x: 5)
                    .opacity(state == .answered ? 1 : 0)
                    .scaleEffect(state == .answered ? 1 : 0.6, anchor: .leading)
            }
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(state == .answered ? MurmurTheme.outgoingBubble : MurmurTheme.secondaryInk.opacity(0.75))
            .frame(width: 16, alignment: .leading)
            .animation(.spring(response: 0.32, dampingFraction: 0.72), value: state)
            .accessibilityLabel(state == .answered ? "已送达，Murmur 正在回应" : "已送达")
        }
    }
}

// MARK: - Typing indicator

private struct TypingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase = 0

    private let timer = Timer.publish(every: 0.28, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(MurmurTheme.secondaryInk.opacity(0.55))
                    .frame(width: 7, height: 7)
                    .scaleEffect(!reduceMotion && phase == index ? 1.35 : 0.85)
                    .animation(.easeInOut(duration: 0.26), value: phase)
            }
        }
        .padding(.horizontal, 15)
        .padding(.vertical, 13)
        .background(MurmurTheme.raisedPaper, in: BubbleShape(isOutgoing: false))
        .overlay {
            BubbleShape(isOutgoing: false).stroke(MurmurTheme.rule, lineWidth: 1)
        }
        .onReceive(timer) { _ in
            guard !reduceMotion else { return }
            phase = (phase + 1) % 3
        }
        .accessibilityLabel("Murmur 正在输入")
    }
}

// MARK: - One message

private struct MessageRow: View {
    let message: MurmurMessage
    let imageURL: URL?
    let onOpenImage: (MurmurPhotoPreview) -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    private var isOutgoing: Bool { message.author == .you }

    var body: some View {
        HStack {
            if isOutgoing { Spacer(minLength: 56) }
            VStack(alignment: isOutgoing ? .trailing : .leading, spacing: 7) {
                if let imageURL {
                    TranscriptPhoto(url: imageURL) {
                        onOpenImage(.init(id: message.id, url: imageURL))
                    }
                    .accessibilityLabel(isOutgoing ? "你发送的照片，轻点放大" : "Murmur 发来的照片，轻点放大")
                }
                if !message.text.isEmpty {
                    Text(message.text)
                        .font(MurmurTheme.body(.body))
                        .foregroundStyle(isOutgoing ? Color.white : MurmurTheme.ink)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(
                            isOutgoing ? MurmurTheme.outgoingBubble : MurmurTheme.raisedPaper,
                            in: BubbleShape(isOutgoing: isOutgoing)
                        )
                        .overlay {
                            if !isOutgoing {
                                BubbleShape(isOutgoing: false).stroke(MurmurTheme.rule, lineWidth: 1)
                            }
                        }
                }
                HStack(spacing: 5) {
                    Text(message.sentAt, format: .dateTime.hour().minute())
                        .font(MurmurTheme.body(.caption2))
                        .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.85))
                    if isOutgoing { DeliveryTicks(state: message.delivery) }
                }
                .padding(.horizontal, 4)
            }
            if !isOutgoing { Spacer(minLength: 56) }
        }
        // Entrance: rise and fade from the speaker's side.
        .opacity(appeared || reduceMotion ? 1 : 0)
        .offset(y: appeared || reduceMotion ? 0 : 10)
        .onAppear {
            guard !reduceMotion, !appeared else { appeared = true; return }
            withAnimation(.spring(response: 0.38, dampingFraction: 0.82)) { appeared = true }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilityLabel)
        // Combining the row into one element hides the photo's own button, so
        // the way to open it has to be offered back explicitly.
        .accessibilityAction(named: "查看照片") {
            guard let imageURL else { return }
            onOpenImage(.init(id: message.id, url: imageURL))
        }
    }

    private var accessibilityLabel: String {
        let photo = imageURL == nil ? "" : "一张照片。"
        let body = message.text.isEmpty ? photo : "\(photo)\(message.text)"
        return isOutgoing ? "你说：\(body)" : "Murmur 说：\(body)"
    }
}

// MARK: - Day separator

private struct DaySeparator: View {
    let date: Date

    var body: some View {
        Text(date, format: .dateTime.year().month(.abbreviated).day())
            .font(MurmurTheme.body(.caption2, weight: .medium))
            .foregroundStyle(MurmurTheme.secondaryInk)
            .padding(.horizontal, 11)
            .padding(.vertical, 5)
            .background(MurmurTheme.raisedPaper, in: Capsule())
            .overlay { Capsule().stroke(MurmurTheme.rule, lineWidth: 1) }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
    }
}

// MARK: - Transcript

struct MurmurTranscriptView: View {
    @ObservedObject var model: MurmurSessionModel
    /// Changes each time the composer takes focus.
    var focusPulse: Int = 0
    let onOpenImage: (MurmurPhotoPreview) -> Void
    var onDismissKeyboard: () -> Void = {}

    /// Whether the reader is resting on the newest line.  Kept from the
    /// scroll geometry stream; only then may a resize carry the transcript
    /// with it — someone up in the history keeps their place.
    @State private var isAtBottom = true
    /// Fingers own the scroll view while they are on it; the re-pin below
    /// only ever acts when they are not.
    @State private var scrollPhase = ScrollPhase.idle

    private var showsTyping: Bool { model.isAwaitingReply }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if model.messages.isEmpty && !showsTyping {
                        EmptyTranscript()
                            // The transcript hangs from the bottom edge, so the
                            // opening line needs a screen of its own to sit in
                            // the middle of rather than crowding the composer.
                            .containerRelativeFrame(.vertical)
                    }
                    ForEach(Array(model.messages.enumerated()), id: \.element.id) { index, message in
                        if needsSeparator(at: index) {
                            DaySeparator(date: message.sentAt)
                        }
                        MessageRow(
                            message: message,
                            imageURL: message.imageFile.map { model.transcriptStore.imageURL(for: $0) },
                            onOpenImage: onOpenImage
                        )
                        .id(message.id)
                        .accessibilityIdentifier("murmur-message-\(index)")
                    }
                    if showsTyping {
                        HStack {
                            TypingIndicator()
                            Spacer(minLength: 56)
                        }
                        .id(Self.typingAnchor)
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }
                    Color.clear.frame(height: 1).id(Self.bottomAnchor)
                }
                .frame(maxWidth: MurmurTheme.contentWidth)
                .padding(.horizontal, MurmurTheme.pageInset)
                .padding(.top, 16)
                .padding(.bottom, 12)
                .frame(maxWidth: .infinity)
            }
            // A chat grows downwards: pinning the anchor keeps the newest line
            // against the composer when the keyboard changes the room's height,
            // instead of leaving it hidden behind the keyboard.
            .defaultScrollAnchor(.bottom)
            // A helper, no longer the pillar: it keeps the newest line
            // pinned through some resizes, but a keyboard on a phone resizes
            // the view more than once — it arrives, then a Pinyin candidate
            // bar appears above it, then a toolbar — and on this screen the
            // pin demonstrably comes loose mid-flight.  The re-pin below is
            // what carries the keyboard now; this stays because rotation and
            // smaller content changes still land on it for free.
            .defaultScrollAnchor(.bottom, for: .sizeChanges)
            // `.never`, and it has to be.  Both other modes hand the keyboard
            // to the scroll view, and this transcript scrolls itself: focusing
            // the field triggers a scroll to the bottom, the scroll view reads
            // that as the reader pushing the keyboard away, and it dismisses
            // the keyboard that was still on its way up.  The device log caught
            // it — the keyboard was alive for as little as 0.05s, and what was
            // left behind was a layout half-way between two states.
            //
            // Dismissing is not lost: tapping the conversation still does it,
            // through the composer's own focus, which is the one path that
            // cannot race the scrolling.
            .scrollDismissesKeyboard(.never)
            // Tapping the conversation puts the keyboard away, the way every
            // chat does.  This has to travel back to the composer's own
            // `FocusState` rather than resign the first responder directly:
            // dismissing behind SwiftUI's back leaves its keyboard avoidance
            // still applied, and the composer stays hoisted over a blank strip
            // the height of the keyboard that just left.
            .onTapGesture { onDismissKeyboard() }
            .animation(.spring(response: 0.4, dampingFraction: 0.85), value: model.messages.count)
            .animation(.easeInOut(duration: 0.22), value: showsTyping)
            .onChange(of: model.messages.count) { _, _ in scrollToBottom(proxy) }
            .onChange(of: showsTyping) { _, _ in scrollToBottom(proxy) }
            // One signal, one animation.  The re-pin below already carries
            // the conversation along as the keyboard changes the room, in
            // step with the keyboard's own clock; the only thing it cannot
            // do is come back from history, which is what this is for.
            // Chasing the keyboard with extra timed scrolls on top of that
            // is what made the motion stutter.
            .onChange(of: focusPulse) { _, _ in scrollToBottom(proxy, settling: true) }
            // The bottom anchor pins the newest line while the keyboard
            // changes the room's height — when it works.  On a phone the
            // resize reaches the scroll view late, piecemeal, or not at all,
            // and the line is left hanging over a band of bare paper the
            // height of the keyboard that just left.  So the transcript keeps
            // its own books: any geometry change that finds the window off
            // the end it was resting on — while no finger owns the scroll
            // view — walks the window back to the newest line, in the same
            // display pass the resize arrived in.  That is what keeps the
            // motion on the keyboard's own clock.
            .onScrollGeometryChange(for: CGFloat.self) { geometry in
                geometry.contentSize.height
                    + geometry.contentInsets.bottom
                    - geometry.contentOffset.y
                    - geometry.containerSize.height
            } action: { _, distanceFromBottom in
#if DEBUG
                MurmurDiagnostics.record("scroll dist=\(String(format: "%.1f", distanceFromBottom)) phase=\(scrollPhase) atBottom=\(isAtBottom)")
#endif
                repin(distanceFromBottom: distanceFromBottom, proxy: proxy)
            }
            .onScrollPhaseChange { _, phase in
                scrollPhase = phase
            }
            .onAppear {
                proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
#if DEBUG
                MurmurDiagnostics.startRecordingKeyboard()
#endif
            }
            .onChange(of: focusPulse) { _, _ in
#if DEBUG
                MurmurDiagnostics.record("tapped field")
#endif
            }
        }
    }

    private static let bottomAnchor = "murmur-transcript-bottom"
    private static let typingAnchor = "murmur-transcript-typing"

    /// One display pass back to the newest line, taken only when the window
    /// has come off the end it was resting on.  A finger — down, dragging,
    /// or coasting after a flick — owns the scroll view and is never
    /// interrupted; the book is simply kept, so a reader who walked up into
    /// the history is never yanked back down.  Programmatic scrolls are no
    /// obstacle either: every one of them is heading for the newest line
    /// already, so a re-pin only hurries them along.
    private func repin(distanceFromBottom: CGFloat, proxy: ScrollViewProxy) {
        let fingerOwnsIt = scrollPhase == .tracking
            || scrollPhase == .interacting
            || scrollPhase == .decelerating
        guard !fingerOwnsIt else {
            isAtBottom = distanceFromBottom < 40
            return
        }
        // Past the end is always wrong — there is nothing to read there.
        // Short of the end is wrong only for a reader who never left the
        // newest line: a resize walked away from them, so the window walks
        // back.  Both thresholds are a single point: anything wider reads as
        // the transcript chasing the keyboard in visible chunks — a 40pt
        // tolerance had it snapping down every 50ms instead of gliding.
        let pastEnd = distanceFromBottom < -1
        let lostPin = isAtBottom && distanceFromBottom > 1
        guard pastEnd || lostPin else {
            isAtBottom = distanceFromBottom < 40
            return
        }
#if DEBUG
        MurmurDiagnostics.record("repin dist=\(String(format: "%.1f", distanceFromBottom))")
#endif
        proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
    }

    /// Matched to the keyboard's own timing so the two move together rather
    /// than racing.  UIKit raises the keyboard over 0.25s with an ease-out.
    private func scrollToBottom(_ proxy: ScrollViewProxy, settling: Bool = false) {
#if DEBUG
        MurmurDiagnostics.record("scrollToBottom settling=\(settling)")
#endif
        withAnimation(.easeOut(duration: 0.25)) {
            proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
        }
        // Reaching for the field from up in the history is the one case a
        // single pass does not finish: the anchor is far outside what the lazy
        // stack has built, and the first scroll lands short.  One correction
        // once the rows exist is enough, and there is no keyboard animation
        // left for it to fight — that was the old stutter, and it came from
        // the keyboard being dismissed underneath, not from this.
        guard settling else { return }
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(300))
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
            }
        }
    }

    private func needsSeparator(at index: Int) -> Bool {
        guard index > 0 else { return true }
        return !Calendar.current.isDate(
            model.messages[index].sentAt,
            inSameDayAs: model.messages[index - 1].sentAt
        )
    }
}

private struct EmptyTranscript: View {
    var body: some View {
        VStack(spacing: 12) {
            Text("发来眼前的一刻。")
                .font(MurmurTheme.display(.title))
                .foregroundStyle(MurmurTheme.ink)
                .multilineTextAlignment(.center)
            Text("可以是一张照片，也可以只说一句。")
                .font(MurmurTheme.body(.subheadline))
                .foregroundStyle(MurmurTheme.secondaryInk)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}
