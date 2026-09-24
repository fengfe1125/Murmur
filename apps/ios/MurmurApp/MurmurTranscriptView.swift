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
    @Environment(\.murmurReduceMotion) private var reduceMotion
    let state: MurmurDeliveryState

    var body: some View {
        switch state {
        case .sending:
            Image(systemName: "clock")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.7))
                .accessibilityLabel("发送中")
        case .failed:
            // Nothing here: a failed row says so beside the bubble, where the
            // mark is big enough to press and the reason is written out in
            // full.  See `SendFailureMark`.
            EmptyView()
        case .sent, .answered:
            // Two overlapping checks, WhatsApp-style: the second slides in and
            // the pair turns colour once Murmur starts composing.
            ZStack(alignment: .leading) {
                Image(systemName: "checkmark")
                    .offset(x: reduceMotion || state == .answered ? 0 : 3)
                Image(systemName: "checkmark")
                    .offset(x: 5)
                    .opacity(state == .answered ? 1 : 0)
                    .scaleEffect(reduceMotion || state == .answered ? 1 : 0.6, anchor: .leading)
            }
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(state == .answered ? MurmurTheme.outgoingBubble : MurmurTheme.secondaryInk.opacity(0.75))
            .frame(width: 16, alignment: .leading)
            .animation(.easeOut(duration: reduceMotion ? 0.12 : 0.15), value: state)
            .accessibilityLabel(state == .answered ? "已送达，Murmur 正在回应" : "已送达")
        }
    }
}

// MARK: - Send failure

/// The mark on an outgoing row that never landed: a coral exclamation outside
/// the bubble, on the side the message left from.
///
/// It is a button exactly when pressing it would do something — the app still
/// holds the turn, with its original idempotency key — and plain ink otherwise.
/// The line under the bubble says which of the two it is, so the mark never has
/// to be guessed at.
///
/// Pressing it asks first.  The mark is small and sits right beside the text a
/// finger reaches for, and a send is not a free action: it can put a
/// full-resolution photo back on the wire.  The question itself is put up by
/// the transcript, not from here — see `MurmurTranscriptView`; all this does is
/// hand over where it is on screen.
private struct SendFailureMark: View {
    let failure: MurmurSendFailure
    let onAsk: () -> Void
    /// Grows with the conversation it sits in — a fixed 19pt mark beside a
    /// bubble set at Accessibility XXXL reads as a speck.  Capped, because past
    /// that it starts to outweigh the message it belongs to.
    @ScaledMetric(relativeTo: .body) private var rawGlyph: CGFloat = 19

    private var glyph: CGFloat { min(rawGlyph, 32) }
    /// 44pt of target at every size, and more once the mark itself needs it.
    private var side: CGFloat { max(44, glyph + 22) }

    var body: some View {
        if failure.canResend {
            Button(action: onAsk) { mark }
                .buttonStyle(.automatic)
                .accessibilityLabel("重新发送")
                .accessibilityIdentifier("resend-moment")
        } else {
            // Nothing to press, and the row's own label already says it
            // failed — a second stop that only reads "感叹号" is noise.
            mark.accessibilityHidden(true)
        }
    }

    private var mark: some View {
        Image(systemName: "exclamationmark.circle.fill")
            .font(.system(size: glyph, weight: .regular))
            .foregroundStyle(MurmurTheme.coral)
            // The margin the target leaves around the mark is also what
            // separates it from the bubble, so the row needs no spacing here.
            .frame(width: side, height: side)
            .contentShape(Rectangle())
    }
}

/// The failed message whose resend is awaiting explicit confirmation.
private struct ResendPrompt: Equatable {
    let id: String
    let reason: String
}

// MARK: - Typing indicator

private struct TypingIndicator: View {
    var body: some View {
        ProgressView("Murmur 正在输入")
            .font(.footnote)
            .padding(12)
    }
}

// MARK: - One message

private struct MessageRow: View {
    let message: MurmurMessage
    let imageURL: URL?
    /// Set only on a row the person sent that did not land.  Its presence is
    /// what draws the mark, so `delivery == .failed` never shows ticks.
    let sendFailure: MurmurSendFailure?
    /// Carried in rather than applied outside, because the row is no longer a
    /// single accessibility element: the mark beside it is a button of its own,
    /// and the identifier has to land on the message, not on the container that
    /// holds both.
    let identifier: String
    let onOpenImage: (MurmurPhotoPreview) -> Void
    let onAsk: () -> Void
    /// What the one player is doing, already narrowed to this row's song.
    var musicPlayback: MusicCardPlayback = .stopped
    var onPlayMusic: (MusicTrackAttachmentV1) -> Void = { _ in }
    /// Absent whenever the room experiment is off for this account, which is
    /// how the card knows not to offer a button that cannot work.
    var onListenTogether: ((MusicTrackAttachmentV1) -> Void)?
    var listenTogetherRoom: ListenTogetherRoomSnapshotV1?
    @Environment(\.murmurReduceMotion) private var reduceMotion
    @Environment(\.openURL) private var openURL

    private var isOutgoing: Bool { message.author == .you }

    private func relationship(
        for track: MusicTrackAttachmentV1
    ) -> ListenTogetherTrackRelationship {
        ListenTogetherTrackRelationship.resolve(track: track, room: listenTogetherRoom)
    }

    var body: some View {
        HStack {
            // A failed row keeps a narrower gutter: the mark is a 44pt target,
            // and the usual 56 on top of it would leave the bubble visibly
            // thinner than the ones above it.
            if isOutgoing { Spacer(minLength: sendFailure == nil ? 56 : 20) }
            VStack(alignment: isOutgoing ? .trailing : .leading, spacing: 7) {
                HStack(spacing: 0) {
                    if let sendFailure {
                        SendFailureMark(
                            failure: sendFailure,
                            onAsk: onAsk
                        )
                    }
                    VStack(alignment: isOutgoing ? .trailing : .leading, spacing: 7) {
                        if let imageURL {
                            TranscriptPhoto(url: imageURL) {
                                onOpenImage(.init(id: message.id, url: imageURL))
                            }
                            .accessibilityLabel(isOutgoing ? "你发送的照片，轻点放大" : "Murmur 发来的照片，轻点放大")
                        }
                        if let track = message.musicTrack {
                            MusicCardView(
                                track: track,
                                isOutgoing: isOutgoing,
                                playback: musicPlayback,
                                onPlay: { onPlayMusic(track) },
                                onOpenProvider: { openURL(track.canonicalURL) },
                                onListenTogether: onListenTogether.map { start in
                                    { start(track) }
                                },
                                listenTogetherRelationship: relationship(for: track)
                            )
                        }
                        // A song carries a text fallback so an older client has
                        // something to show. Here the card already says all of
                        // it, and printing both would be the same message twice.
                        if !message.text.isEmpty, message.musicTrack == nil {
                            Text(message.text)
                                .font(MurmurTheme.body(.body))
                                .foregroundStyle(isOutgoing ? MurmurTheme.onAccent : MurmurTheme.ink)
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
                    }
                    // The message is one element; the mark beside it is its own,
                    // so a resend is a button VoiceOver can find rather than an
                    // action hidden inside a combined row.
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(accessibilityLabel)
                    .accessibilityIdentifier(identifier)
                    // Combining swallows the photo's own button, so the way to
                    // open it has to be offered back explicitly.
                    .accessibilityAction(named: "查看照片") {
                        guard let imageURL else { return }
                        onOpenImage(.init(id: message.id, url: imageURL))
                    }
                }
                // The line under the bubble: the time, and then either the
                // transport ticks or — when the send failed — why, in words.
                // The mark alone would only say "something"; this says what,
                // and whether pressing it is worth anything.
                HStack(spacing: 5) {
                    if let sendFailure {
                        // One run of text rather than two views side by side:
                        // at Accessibility XXXL a reason long enough to wrap
                        // would otherwise be pushed to the far side of the row
                        // with the time stranded across a gap from it.
                        (
                            Text(message.sentAt, format: .dateTime.hour().minute())
                                .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.85))
                            + Text("  ")
                            + Text(caption(for: sendFailure))
                                .foregroundStyle(MurmurTheme.coral)
                        )
                        .font(MurmurTheme.body(.caption2))
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(isOutgoing ? .trailing : .leading)
                    } else {
                        Text(message.sentAt, format: .dateTime.hour().minute())
                            .font(MurmurTheme.body(.caption2))
                            .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.85))
                        if isOutgoing {
                            DeliveryTicks(state: message.delivery)
                        }
                    }
                }
                .padding(.horizontal, 4)
                // Left as its own stop, which is what it has always been on
                // this screen.  Hiding it was tried — `accessibilityHidden`,
                // with and without `accessibilityElement(children: .ignore)` —
                // and neither reaches the text inside, so the modifiers only
                // looked like they were doing something.  It reads the time and
                // the reason a second time after the message's own label; a
                // little repetition beats a lie in the source.
            }
            if !isOutgoing { Spacer(minLength: 56) }
        }
        // Entrance: rise and fade, on whatever animation the insertion itself
        // is running under rather than on a spring of the row's own.
        //
        // It was `onAppear` driving that spring, and in a lazy stack that is
        // the wrong signal — `onAppear` fires when the row is built, not when
        // the message arrives.  A screenful of restored history therefore all
        // rose at once at launch, against a scroll that was animating at the
        // same time; rows built mid-scroll started wherever the scroll had got
        // to; and coming back down through the history made old bubbles fade in
        // as though they were new.  A transition runs only when the insertion
        // lands inside an animated transaction: a message arriving does, a row
        // being built under a moving finger does not.
        .transition(.opacity)
        // The mark arriving widens the row, so it fades in rather than
        // snapping — and under Reduce Motion it simply is there.
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: sendFailure)
    }

    /// 「暂时没有连上 Murmur · 轻点重新发送」.  The reason keeps its own wording
    /// and loses only its full stop, so the offer reads as part of the same
    /// line instead of a second sentence.  The verb is the one the mark's own
    /// question and its confirming button use, so the line, the dialog and the
    /// button never disagree about what is about to happen.
    private func caption(for failure: MurmurSendFailure) -> String {
        let reason = failure.message.hasSuffix("。")
            ? String(failure.message.dropLast())
            : failure.message
        return failure.canResend ? "\(reason) · 轻点重新发送" : reason
    }

    private var accessibilityLabel: String {
        let photo = imageURL == nil ? "" : "一张照片。"
        let body = message.text.isEmpty ? photo : "\(photo)\(message.text)"
        let said = isOutgoing ? "你说：\(body)" : "Murmur 说：\(body)"
        guard let sendFailure else { return said }
        return sendFailure.canResend
            ? "\(said) 发送失败。\(sendFailure.message)可以重新发送。"
            : "\(said) 发送失败。\(sendFailure.message)"
    }
}

// MARK: - Day separator

private struct DaySeparator: View {
    let date: Date

    var body: some View {
        Text(date.formatted(.dateTime.year().month().day().locale(Locale(identifier: "zh_Hans_CN"))))
            .font(MurmurTheme.body(.caption2, weight: .medium))
            .foregroundStyle(MurmurTheme.secondaryInk)
            .padding(.horizontal, 11)
            .padding(.vertical, 5)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
    }
}

// MARK: - Transcript

struct MurmurTranscriptView: View {
    @ObservedObject var model: MurmurSessionModel
    /// Changes each time the composer takes focus.
    var focusPulse: Int = 0
    var keyboardIsFocused = false
    let onOpenImage: (MurmurPhotoPreview) -> Void
    var onDismissKeyboard: () -> Void = {}
    /// Passed down rather than observed per row: one player, one value, and a
    /// screenful of rows that only compare it against their own song.
    var nowPlaying: MusicNowPlaying = .none
    var onPlayMusic: (MusicTrackAttachmentV1) -> Void = { _ in }
    var onListenTogether: ((MusicTrackAttachmentV1) -> Void)?
    var listenTogetherRoom: ListenTogetherRoomSnapshotV1?

    /// Whether the reader is resting on the newest line.  Kept from the
    /// scroll geometry stream; only then may a resize carry the transcript
    /// with it — someone up in the history keeps their place.
    @State private var isAtBottom = true
    /// Fingers own the scroll view while they are on it; the re-pin below
    /// only ever acts when they are not.
    @State private var scrollPhase = ScrollPhase.idle
    /// The row currently being asked about.
    @State private var question: ResendPrompt?
    /// True until the scrollback read off disk has been laid out once.
    ///
    /// While it is set the screen does not animate at all: the rows arriving
    /// are a restore, not an arrival, and the whole first second of the app
    /// used to be an insertion spring, a row-by-row entrance and an animated
    /// scroll all reaching for the same pixels at once.
    @State private var isRestoring = true
    @Environment(\.murmurReduceMotion) private var reduceMotion

    private var showsTyping: Bool { model.isAwaitingReply }

    /// The transcript's own coordinate space, which the marks measure
    /// themselves in and the question is positioned against.  It is the
    /// viewport rather than the scrolled content, so a rectangle taken from it
    /// is where the mark is on screen right now.
    fileprivate nonisolated static let anchorSpace = "murmur-transcript-anchor"
    /// Retains the destination while a newly rebuilt chat tab lays out its lazy
    /// rows. A one-shot proxy scroll can arrive before the bottom exists and be
    /// lost; an edge position remains pending until the scroll view can honor it.
    @State private var scrollPosition = ScrollPosition(edge: .bottom)

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        if !model.visibleMessages.isEmpty {
                            Button { Task { await model.loadHistory(earlier: true) } } label: { Text("载入更早的记录").frame(maxWidth: .infinity, minHeight: 44).contentShape(Rectangle()) }
                                .font(.footnote).foregroundStyle(MurmurTheme.accentInk).frame(minHeight:44)
                                .disabled(model.loadingHistory).accessibilityIdentifier("chat-load-earlier")
                        }
                        // Not until the scrollback has been read back: that read
                        // is asynchronous, and shown before it lands the opening
                        // line is a screen-high view that the history then has to
                        // shove out of the way — the first thing the app showed
                        // was the wrong screen collapsing.
                        if model.visibleMessages.isEmpty && !showsTyping && model.transcriptRestored {
                            EmptyTranscript()
                                // The transcript hangs from the bottom edge, so the
                                // opening line needs a screen of its own to sit in
                                // the middle of rather than crowding the composer.
                                .containerRelativeFrame(.vertical)
                        }
                        ForEach(Array(model.visibleMessages.enumerated()), id: \.element.id) { index, message in
                            if needsSeparator(at: index) {
                                DaySeparator(date: message.sentAt)
                            }
                            MessageRow(
                                message: message,
                                imageURL: message.imageFile.map { model.transcriptStore.imageURL(for: $0) },
                                sendFailure: sendFailure(for: message),
                                identifier: "murmur-message-\(index)",
                                onOpenImage: onOpenImage,
                                onAsk: {
                                    question = .init(
                                        id: message.id,
                                        reason: sendFailure(for: message)?.message ?? ""
                                    )
                                },
                                musicPlayback: message.musicTrack.map(nowPlaying.playback) ?? .stopped,
                                onPlayMusic: onPlayMusic,
                                onListenTogether: onListenTogether,
                                listenTogetherRoom: listenTogetherRoom
                            )
                            .id(message.id)
                            .background {
                                GeometryReader { geometry in
                                    Color.clear.preference(key: ChatDateFrames.self, value: [message.id: geometry.frame(in: .named(Self.anchorSpace)).minY])
                                }
                            }
                        }
                        if model.historyPage != nil {
                            Button { Task { await model.loadHistory(earlier: false) } } label: { Text("载入后面的记录").frame(maxWidth: .infinity, minHeight: 44).contentShape(Rectangle()) }
                                .font(.footnote).foregroundStyle(MurmurTheme.accentInk).frame(minHeight:44).disabled(model.loadingHistory)
                        }
                        if showsTyping && model.historyPage == nil {
                            HStack {
                                TypingIndicator()
                                Spacer(minLength: 56)
                            }
                            .id(Self.typingAnchor)
                            .transition(.opacity)
                        }
                        Color.clear.frame(height: 1).id(Self.bottomAnchor)
                    }
                    .frame(maxWidth: MurmurTheme.contentWidth)
                    .padding(.horizontal, MurmurTheme.pageInset)
                    .padding(.top, 8)
                    .padding(.bottom, 12)
                    .frame(maxWidth: .infinity)
                }
                .scrollPosition($scrollPosition)
                .onPreferenceChange(ChatDateFrames.self) { frames in
                    let top = frames.filter { $0.value <= 0 }.max { $0.value < $1.value }
                        ?? frames.min { $0.value < $1.value }
                    if let id = top?.key, let row = model.visibleMessages.first(where: { $0.id == id }),
                       !Calendar.current.isDate(model.readingDate, inSameDayAs: row.sentAt) {
                        model.readingDate = row.sentAt
                    }
                }
                .onChange(of: model.navigationRevision) { _, _ in
                    if let target = model.historyTarget {
                        isAtBottom = false
                        proxy.scrollTo(target, anchor: .top)
                    } else { isAtBottom = true; scrollToBottom(settling: true) }
                }
                .overlay(alignment: .bottomTrailing) {
                    if model.historyPage != nil || !isAtBottom {
                        Button { model.returnToLatest() } label: {
                            Label("回到最新", systemImage: "arrow.down")
                                .font(.footnote.weight(.medium))
                                .padding(.horizontal, 14)
                                .frame(minHeight: 44)
                                .background(MurmurTheme.raisedPaper, in: Capsule())
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(MurmurTheme.accentInk)
                        .padding(.trailing, 16)
                        .padding(.bottom, 8)
                        .transition(.opacity)
                        .accessibilityIdentifier("chat-return-latest")
                    }
                }
                .animation(reduceMotion ? nil : MurmurMotion.content, value: model.historyPage != nil || !isAtBottom)
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
                // Automatic scroll-to-bottom must not be interpreted as an
                // interactive keyboard dismissal. The shared surface releases the
                // composer's FocusState only for a tap or an intentional downward
                // drag, while leaving this ScrollView's own gesture active.
                .murmurKeyboardDismissSurface(
                    isFocused: keyboardIsFocused,
                    dismiss: onDismissKeyboard
                )
                // The one clock the rows move on — each row's entrance transition
                // runs under this.  Nothing at all while the scrollback is still
                // being read back: what is on disk was already there.  Softer than
                // the spring it replaces, because that overshoot sat on top of a
                // row that was fading in at the same time.
                .animation(
                    isRestoring || reduceMotion ? nil : MurmurMotion.content,
                    value: model.visibleMessages.count
                )
                .animation(reduceMotion ? nil : MurmurMotion.content, value: showsTyping)
                .onChange(of: model.visibleMessages.count) { _, _ in
                    // The restore is not a journey: the newest line is simply
                    // where the transcript opens.  Animating the way down there
                    // put a second clock on the pixels the insertion was already
                    // moving, and those two fighting is what jerked.
                    guard !isRestoring else {
                        pinToBottom()
                        return
                    }
                    if isAtBottom && model.historyPage == nil { scrollToBottom() }
                }
                .onChange(of: showsTyping) { _, _ in scrollToBottom() }
                // One signal, one animation.  The re-pin below already carries
                // the conversation along as the keyboard changes the room, in
                // step with the keyboard's own clock; the only thing it cannot
                // do is come back from history, which is what this is for.
                // Chasing the keyboard with extra timed scrolls on top of that
                // is what made the motion stutter.
                .onChange(of: focusPulse) { _, _ in
                    if model.historyPage != nil {
                        model.returnToLatest()
                        return
                    }
                    isAtBottom = true
                    scrollToBottom(settling: true)
                }
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
                    repin(distanceFromBottom: distanceFromBottom)
                }
                .onScrollPhaseChange { _, phase in
                    scrollPhase = phase
                }
                // Going live: one turn after the restored rows commit.  The pass
                // that puts them on screen is the pass that must not animate, and
                // a hop through the main actor lands after it — so the restore is
                // silent and the very next thing to arrive is not.
                .onChange(of: model.transcriptRestored, initial: true) { _, restored in
                    guard restored, isRestoring else { return }
                    // On a tab return restoration already finished before this view
                    // existed. Reassert the retained edge before ending the silent
                    // restore pass so later inset measurements cannot strand it.
                    pinToBottom()
                    Task { @MainActor in isRestoring = false }
                }
                .onAppear {
    #if DEBUG
                    MurmurDiagnostics.startRecordingKeyboard()
    #endif
                }
                .onChange(of: focusPulse) { _, _ in
    #if DEBUG
                    MurmurDiagnostics.record("tapped field")
    #endif
                }
            .coordinateSpace(.named(Self.anchorSpace))
            .confirmationDialog(
                "重新发送这条消息？",
                isPresented: Binding(get: { question != nil }, set: { if !$0 { question = nil } }),
                titleVisibility: .visible,
                presenting: question
            ) { prompt in
                Button("重新发送") { model.resend(prompt.id); question = nil }
                    .accessibilityIdentifier("confirm-resend")
                Button("取消", role: .cancel) { question = nil }
            } message: { prompt in
                Text(prompt.reason)
            }
        }
    }

    private static let bottomAnchor = "murmur-transcript-bottom"
    private static let typingAnchor = "murmur-transcript-typing"
    /// One rule for the mark: a row of the person's own that ended in `.failed`
    /// always carries one, and never carries ticks.  The model's reason is used
    /// when it has one.  A row read back from disk kept the verdict but not the
    /// reason, and says only that much — but it is still pressable, because the
    /// transcript holds everything the send needs: the words, its own copy of
    /// the photo, and the key it went up under.
    private func sendFailure(for message: MurmurMessage) -> MurmurSendFailure? {
        guard message.author == .you, message.delivery == .failed else { return nil }
        if let standing = model.sendFailures[message.id] { return standing }
        return .interrupted(canResend: !message.text.isEmpty || message.imageFile != nil)
    }

    /// One display pass back to the newest line, taken only when the window
    /// has come off the end it was resting on.  A finger — down, dragging,
    /// or coasting after a flick — owns the scroll view and is never
    /// interrupted; the book is simply kept, so a reader who walked up into
    /// the history is never yanked back down.  Programmatic scrolls are no
    /// obstacle either: every one of them is heading for the newest line
    /// already, so a re-pin only hurries them along.
    private func repin(distanceFromBottom: CGFloat) {
        guard model.historyPage == nil else { return }
        let fingerOwnsIt = scrollPhase == .tracking
            || scrollPhase == .interacting
            || scrollPhase == .decelerating
        guard !fingerOwnsIt else {
            isAtBottom = distanceFromBottom < 40
            if !isAtBottom { model.beginHistoryReading() }
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
        pinToBottom()
    }

    private func pinToBottom() {
        // The empty invitation has no last message. Repeatedly pinning its
        // flexible height during keyboard layout can cancel a fresh focus.
        guard !model.visibleMessages.isEmpty else { return }
        scrollPosition.scrollTo(edge: .bottom)
    }

    /// Matched to the keyboard's own timing so the two move together rather
    /// than racing.  UIKit raises the keyboard over 0.25s with an ease-out.
    private func scrollToBottom(settling: Bool = false) {
        guard model.historyPage == nil, isAtBottom, !model.visibleMessages.isEmpty else { return }
#if DEBUG
        MurmurDiagnostics.record("scrollToBottom settling=\(settling)")
#endif
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) {
            pinToBottom()
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
            guard !Task.isCancelled, isAtBottom, model.historyPage == nil else { return }
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
                pinToBottom()
            }
        }
    }

    private func needsSeparator(at index: Int) -> Bool {
        guard index > 0 else { return true }
        return !Calendar.current.isDate(
            model.visibleMessages[index].sentAt,
            inSameDayAs: model.visibleMessages[index - 1].sentAt
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

private struct ChatDateFrames: PreferenceKey {
    static let defaultValue: [String: CGFloat] = [:]
    static func reduce(value: inout [String: CGFloat], nextValue: () -> [String: CGFloat]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}
