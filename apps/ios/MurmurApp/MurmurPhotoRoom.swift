import SwiftUI
import UIKit

// MARK: - Shared room stream

/// One SSE bubble after its per-moment event identity has been scoped for a
/// room that can span several moments.  Both the live photo room and an
/// archived-day continuation consume the wire through this path, so pacing,
/// duplicate suppression, terminal handling and SwiftUI identity cannot drift.
struct MurmurRoomBubble: Equatable, Sendable {
    let id: String
    let text: String
}

@MainActor
struct MurmurRoomEventConsumer {
    let api: any MurmurAPIClient
    let pacing: MurmurBubblePacing

    func consume(
        momentID: String,
        onBubble: (MurmurRoomBubble) async -> Void,
        onAngles: ([String]) -> Void = { _ in }
    ) async throws {
        var terminal = false
        var lastBubbleAt = Date()
        var seenEventIDs = Set<String>()
        for try await event in await api.events(momentID: momentID, lastEventID: nil) {
            try Task.checkCancellation()
            let eventID = Self.eventID(of: event)
            if let eventID, !seenEventIDs.insert(eventID).inserted { continue }
            switch event {
            case .accepted:
                lastBubbleAt = Date()
            case let .bubble(id, text, _):
                guard !text.isEmpty else { continue }
                try await pace(for: text, since: lastBubbleAt)
                await onBubble(.init(
                    id: Self.bubbleID(momentID: momentID, eventID: id),
                    text: text
                ))
                lastBubbleAt = Date()
            case let .angles(_, texts):
                onAngles(Array(texts.prefix(3)))
            case .quiet:
                break
            case .done:
                terminal = true
            case let .failure(_, failure):
                throw failure
            }
            if terminal { break }
        }
        guard terminal else {
            throw MurmurFailure(code: "stream_ended", message: "回应中断了。", retryable: true)
        }
    }

    static func bubbleID(momentID: String, eventID: String?) -> String {
        "\(momentID)-\(eventID ?? UUID().uuidString)"
    }

    private func pace(for text: String, since: Date) async throws {
        let target = pacing.delay(for: text)
        guard target > 0 else { return }
        let remaining = target - Date().timeIntervalSince(since)
        guard remaining > 0 else { return }
        try await Task.sleep(for: .seconds(remaining))
    }

    private static func eventID(of event: MurmurStreamEvent) -> String? {
        switch event {
        case let .accepted(id), let .bubble(id, _, _), let .angles(id, _),
             let .quiet(id), let .done(id, _, _), let .failure(id, _):
            id
        }
    }
}

// MARK: - The room's own bubble

/// The transcript's bubble shape, again.  The original is `private` to
/// `MurmurTranscriptView` and should stay that way: this room is not the
/// conversation and must not start borrowing its internals piecemeal.  One
/// small shape copied is cheaper than a shared surface that invites the rest.
private struct RoomBubbleShape: Shape {
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

// MARK: - Model

/// One photo, and the exchange about it.
///
/// The room is not the conversation and does not share its model: no queue, no
/// resend offers, and `lines` ends with the screen.  What it does keep is the
/// exchange — the photo and everything said about it are written into 当年今日's
/// own archive through `MurmurRoomRecorder` as they happen, filed under the day
/// they happened on.  The conversation never sees any of it; the calendar is
/// where you find it again.
@MainActor
final class PhotoRoomModel: ObservableObject {
    enum Phase: Equatable {
        /// The photo is on the wire and the server has not answered yet.
        case reading
        /// The reading never landed.  The room is not open: the only thing
        /// on offer is sending the same photo again.  Typing into a room
        /// whose photo the server never saw would get an answer about
        /// nothing.
        case unopened
        /// Murmur has spoken; the field is live.
        case listening
        /// Something the person said is on the wire.
        case sending
    }

    struct Line: Identifiable, Equatable {
        enum Author { case mine, murmur }
        let id: String
        let author: Author
        let text: String
    }

    /// The photo this room is about.  Held in memory for the life of the
    /// screen; the file it was uploaded from is deleted long before that.
    let image: UIImage

    @Published private(set) var lines: [Line] = []
    /// The three openers under Murmur's guess.  One tap lifts an opener into
    /// the field, where the person decides whether it leaves — they are doors
    /// into the next line, not messages of their own.  Cleared the moment the
    /// person says anything, because by then they no longer need a way in.
    @Published private(set) var openers: [String] = []
    @Published private(set) var phase: Phase = .reading
    @Published private(set) var failure: MurmurFailure?
    @Published var draft = ""

    var canSend: Bool {
        phase == .listening && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Whether 再试一次 is on offer, which it is for exactly one failure: the
    /// one that kept the room from opening.  A line that did not land is put
    /// back in the field instead, where the send button is the retry.
    var canRetryOpening: Bool { phase == .unopened }

    /// True while Murmur owes an answer, which is what the typing dots read.
    var isAwaitingReply: Bool { phase == .reading || phase == .sending }

    private let api: any MurmurAPIClient
    private let photoLoader: PhotoLoader
    private let uploadTimeoutSeconds: TimeInterval
    private let requestTimeoutSeconds: TimeInterval
    private let bubblePacing: MurmurBubblePacing
    /// What the photo says about itself: when it was taken, and where.  Nil for
    /// a room opened on something other than an old photo off the shelf.
    private let provenance: PhotoProvenance?
    /// Turns the coordinate into a name, on this device.  Asked exactly once,
    /// at the moment the photo is sent.
    private let placeLookup: any MurmurPlaceLookup
    /// Where the room's rows go.  Weak because the session owns both the room
    /// and the archive, not the other way round.
    private weak var transcript: (any MurmurRoomRecorder)?
    /// The scrollback row the photo went into, written once however many times
    /// 再试一次 is pressed — the retry re-sends the same moment, not a second one.
    private var photoRowID: String?
    /// Rows the room is holding open, keyed by the line on screen, so a line
    /// pulled back out of the room comes back out of the history with it.
    private var rowForLine: [String: String] = [:]
    /// Rows whose receipt has not landed yet — as far as this device knows,
    /// those words never left.  A row leaves this the moment the server takes
    /// it, which is what keeps `close()` from calling a sent line failed.
    private var unsentRows: Set<String> = []
    /// The upload's temporary original.  It exists from the moment the photo is
    /// encoded until the reading is over or the room closes, and every one of
    /// those paths deletes it — see `finishOpening` and `close`.
    private var attachment: PhotoAttachment?
    /// The opening upload's key, kept so that 再试一次 is a retry of the same
    /// moment rather than a second one.
    private let openingKey = UUID().uuidString
    private var turn: Task<Void, Never>?
    private var closed = false

    init(
        image: UIImage,
        api: any MurmurAPIClient,
        photoLoader: PhotoLoader = PhotoLoader(),
        uploadTimeoutSeconds: TimeInterval = 300,
        requestTimeoutSeconds: TimeInterval = 45,
        bubblePacing: MurmurBubblePacing = .human,
        provenance: PhotoProvenance? = nil,
        placeLookup: any MurmurPlaceLookup = SystemPlaceLookup(),
        transcript: (any MurmurRoomRecorder)? = nil
    ) {
        self.image = image
        self.api = api
        self.photoLoader = photoLoader
        self.uploadTimeoutSeconds = uploadTimeoutSeconds
        self.requestTimeoutSeconds = requestTimeoutSeconds
        self.bubblePacing = bubblePacing
        self.provenance = provenance
        self.placeLookup = placeLookup
        self.transcript = transcript
    }

    // ---- Opening ------------------------------------------------------------

    /// Sends the photo up and waits for the server to read it.  Safe to call
    /// again: 再试一次 lands here with the same idempotency key.
    func open() {
        guard !closed else { return }
        turn?.cancel()
        phase = .reading
        failure = nil
        turn = Task { [weak self] in
            guard let self else { return }
            do {
                let photo = try await self.prepareAttachment()
                try Task.checkCancellation()
                let receipt = try await withTimeout(seconds: self.uploadTimeoutSeconds) { [api = self.api] in
                    try await api.createMoment(
                        note: nil,
                        photo: photo,
                        idempotencyKey: self.openingKey,
                        intent: .photoReading
                    )
                }
                // The photo goes into the history here, while its original is
                // still on disk to be copied from and before the first bubble
                // can land — the picture has to sit above the reading of it.
                await self.recordPhoto(momentID: receipt.momentID, from: photo)
                try await self.consume(momentID: receipt.momentID)
                self.finishOpening()
            } catch is CancellationError {
                return
            } catch {
                self.fail(with: error)
            }
        }
    }

    /// Encodes the photo once.  A retry after a network failure reuses the same
    /// file rather than writing a second copy of the same pixels.
    private func prepareAttachment() async throws -> PhotoAttachment {
        if let attachment { return attachment }
        // The place name is asked for here and nowhere earlier: a coordinate
        // leaves this phone only for a photo the person actually chose to send,
        // never for one they merely scrolled past.  It resolves alongside the
        // encode rather than in front of it — the upload is the thing that
        // matters, and a slow geocode must not stand in its way.
        let facts = provenance
        let lookup = placeLookup
        async let resolvedPlace = Self.place(for: facts, using: lookup)
        var loaded = try await photoLoader.load(libraryImage: image)
        if var facts {
            // A name that did not arrive in time is simply absent.  The photo
            // still goes, still with its own date — the server reads the place
            // out of the picture the way it always did.
            facts.place = await resolvedPlace
            loaded.provenance = facts
        }
        guard !closed else {
            await photoLoader.discard(loaded)
            throw CancellationError()
        }
        attachment = loaded
        return loaded
    }

    /// nonisolated and static so the wait happens off the main actor and needs
    /// nothing from `self` while it does.
    private nonisolated static func place(
        for provenance: PhotoProvenance?, using lookup: any MurmurPlaceLookup
    ) async -> String? {
        guard let latitude = provenance?.latitude,
              let longitude = provenance?.longitude else { return nil }
        return await lookup.name(latitude: latitude, longitude: longitude)
    }

    /// The reading is over, whichever way it went: the full-resolution original
    /// has nothing left to do and goes now, not when the screen closes.
    private func finishOpening() {
        phase = .listening
        if let photoRowID { transcript?.setDelivery(.answered, for: photoRowID) }
        discardAttachment()
    }

    // ---- Saying something ---------------------------------------------------

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, phase == .listening, !closed else { return }
        draft = ""
        // The way in has been used; the doors close behind it.
        openers = []
        let line = Line(id: UUID().uuidString, author: .mine, text: text)
        lines.append(line)
        phase = .sending
        failure = nil
        turn?.cancel()
        let key = UUID().uuidString
        turn = Task { [weak self] in
            guard let self else { return }
            // On screen the instant it is said, in the history the same way the
            // composer does it — one tick when the server takes it.
            let row = MurmurMessage(author: .you, text: text, delivery: .sending)
            self.rowForLine[line.id] = row.id
            self.unsentRows.insert(row.id)
            await self.transcript?.record(row, photoURL: nil)
            do {
                let receipt = try await withTimeout(seconds: self.requestTimeoutSeconds) { [api = self.api] in
                    // No photo: the room's photo is already in Murmur's memory
                    // from the opening moment, so every line after it is an
                    // ordinary moment that lands in the same thread.
                    try await api.createMoment(
                        note: text, photo: nil, idempotencyKey: key, intent: nil
                    )
                }
                self.unsentRows.remove(row.id)
                self.transcript?.setMomentID(receipt.momentID, for: row.id)
                self.transcript?.setDelivery(.sent, for: row.id)
                try await self.consume(momentID: receipt.momentID)
                self.transcript?.setDelivery(.answered, for: row.id)
                self.rowForLine.removeValue(forKey: line.id)
                self.phase = .listening
            } catch is CancellationError {
                return
            } catch {
                self.failLine(line, with: error)
            }
        }
    }

    func pick(opener: String) {
        draft = opener
    }

    // ---- The history --------------------------------------------------------

    /// Puts the room's photo into 当年今日's archive.  Called once
    /// the server has the moment and while the original is still on disk;
    /// 再试一次 lands here again and must not write a second copy of the picture.
    private func recordPhoto(momentID: String, from photo: PhotoAttachment) async {
        guard !closed, photoRowID == nil, let transcript else { return }
        // One tick now — the server has the photo.  The second one waits for
        // the reading, the same as any other turn in the conversation.
        let row = MurmurMessage(
            author: .you, text: "", delivery: .sent, momentID: momentID
        )
        photoRowID = row.id
        await transcript.record(row, photoURL: photo.originalURL)
    }

    private func recordBubble(_ line: Line, momentID: String) async {
        await transcript?.record(
            MurmurMessage(
                id: line.id,
                author: .murmur,
                text: line.text,
                momentID: momentID
            ),
            photoURL: nil
        )
    }

    // ---- Leaving ------------------------------------------------------------

    /// Closing the room is a terminal path like any other, and it owns the same
    /// cleanup: the turn in flight is cancelled and the original goes with it.
    func close() {
        closed = true
        turn?.cancel()
        turn = nil
        // Leaving mid-send: the receipt never landed, so as far as this device
        // knows the line never left.  Same call the conversation makes when it
        // cancels its queue — a row stuck on a spinner forever is the worse lie.
        for rowID in unsentRows { transcript?.setDelivery(.failed, for: rowID) }
        unsentRows = []
        rowForLine = [:]
        discardAttachment()
    }

    private func discardAttachment() {
        let old = attachment
        attachment = nil
        guard old != nil else { return }
        Task { [photoLoader] in await photoLoader.discard(old) }
    }

    // ---- The stream ---------------------------------------------------------

    private func consume(momentID: String) async throws {
        try await MurmurRoomEventConsumer(api: api, pacing: bubblePacing).consume(
            momentID: momentID,
            onBubble: { bubble in
                let line = Line(
                    id: bubble.id,
                    author: .murmur,
                    text: bubble.text
                )
                lines.append(line)
                await recordBubble(line, momentID: momentID)
            },
            onAngles: { texts in
                openers = texts
            }
        )
    }

    /// The reading did not land.  The room stays shut, with one offer on it.
    private func fail(with error: Error) {
        guard !closed else { return }
        failure = MurmurFailure.from(error)
        phase = .unopened
        // A photo that could not be read still has a file behind it, and a
        // retryable failure is an offer to use it again.  A permanent one is
        // not, so it goes now rather than waiting for the screen to close.
        if failure?.retryable == false { discardAttachment() }
    }

    /// A line the person wrote did not land.  Nothing was said, so nothing
    /// stays on screen claiming it was: the row comes back out and the words
    /// go back into the field, where the send button is the retry.  Making
    /// them retype a sentence they already wrote is the worse failure.
    private func failLine(_ line: Line, with error: Error) {
        guard !closed else { return }
        if let rowID = rowForLine.removeValue(forKey: line.id) {
            let wasUnsent = unsentRows.remove(rowID) != nil
            if wasUnsent {
                lines.removeAll { $0.id == line.id }
                // Only a line with no receipt comes back to the field.  Once
                // the server accepted it, a later stream failure must not
                // rewrite history and claim those words never left.
                transcript?.withdraw(rowID)
                if draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    draft = line.text
                }
            }
        }
        failure = MurmurFailure.from(error)
        phase = .listening
    }
}

// MARK: - View

/// 当年今日's second half: the photo arrives out of its own particles, settles
/// at the top of the screen, and stays there while the person tells Murmur what
/// it is.  The guess and the three openers are the way in; everything after
/// them is an ordinary exchange about one picture.
struct PhotoRoomView: View {
    @ObservedObject var model: PhotoRoomModel
    let onClose: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var composerFocused: Bool
    @State private var settled = false

    /// Matches the browser's dissolve, so the two halves read as one motion.
    private static let cell: CGFloat = 8
    private static let drift: CGFloat = 90
    private static let arrival: TimeInterval = 0.7

    var body: some View {
        VStack(spacing: 0) {
            photo
                .padding(.top, 64)
                .padding(.bottom, 16)
            exchange
            composer
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(MurmurTheme.paper.ignoresSafeArea())
        .overlay(alignment: .topLeading) {
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(MurmurTheme.ink)
                    .frame(width: 34, height: 34)
                    .background(MurmurTheme.raisedPaper, in: Circle())
                    .overlay { Circle().stroke(MurmurTheme.rule, lineWidth: 1) }
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(MurmurPressStyle())
            .padding(.leading, 12)
            .padding(.top, 10)
            .accessibilityLabel("离开这张照片")
            .accessibilityIdentifier("close-photo-room")
        }
        .task {
            model.open()
            // A trigger, not a timeline: flipping this once starts the arrival
            // track and the shader is never touched again.  See OnThisDayView.
            settled = true
        }
        .onDisappear { model.close() }
    }

    // ---- The photo, pinned at the top ---------------------------------------

    private var photo: some View {
        Group {
            if !reduceMotion, MurmurShaderSupport.particleDissolve {
                KeyframeAnimator(initialValue: 1.0, trigger: settled) { progress in
                    photoCard.layerEffect(
                        ShaderLibrary.default.onThisDayDissolve(
                            .float(Self.cell),
                            .float(progress),
                            .float(Self.drift)
                        ),
                        maxSampleOffset: CGSize(
                            width: Self.cell + Self.drift,
                            height: Self.cell + Self.drift
                        )
                    )
                } keyframes: { _ in
                    KeyframeTrack(\.self) {
                        // 1 → 0: the particles come back together.  The browser
                        // let them go upward, and this is where they land.
                        LinearKeyframe(0.0, duration: Self.arrival)
                    }
                }
            } else {
                photoCard
            }
        }
        .accessibilityLabel("你带进来的那张照片")
        .accessibilityIdentifier("photo-room-photo")
    }

    /// A fixed band rather than the photo's own aspect ratio: the room's job is
    /// the conversation under it, and a tall portrait shot left to itself takes
    /// the screen.  `Color.clear` carries the frame so `scaledToFill` fills it
    /// instead of proposing its own size to the stack.
    private var photoCard: some View {
        Color.clear
            .frame(height: 220)
            .overlay {
                Image(uiImage: model.image)
                    .resizable()
                    .scaledToFill()
            }
            // `.clipped` as well as `.clipShape`: the shape masks the drawing
            // but leaves the overflowing image's own bounds behind it, and
            // those bounds are what hit-testing and VoiceOver see — the photo
            // reached up over the close button and down over the first line.
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: MurmurTheme.corner))
            .overlay {
                RoundedRectangle(cornerRadius: MurmurTheme.corner)
                    .stroke(MurmurTheme.rule, lineWidth: 1)
            }
            .frame(maxWidth: MurmurTheme.contentWidth)
            .padding(.horizontal, MurmurTheme.pageInset)
            .frame(maxWidth: .infinity)
    }

    // ---- What has been said about it ----------------------------------------

    private var exchange: some View {
        ScrollViewReader { proxy in
            ScrollView {
                // Not lazy.  A room holds a handful of lines about one photo,
                // so laziness buys nothing — and it costs: rows removed while
                // parked outside the viewport keep their transition parked
                // with them, and stay in the tree after they are gone.
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(model.lines) { line in
                        bubble(line)
                            .id(line.id)
                    }
                    if model.isAwaitingReply {
                        HStack {
                            RoomTypingIndicator()
                            Spacer(minLength: 56)
                        }
                        .id(Self.typingAnchor)
                        .transition(.opacity)
                    }
                    if !model.openers.isEmpty {
                        openersRow
                            .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }
                    if let failure = model.failure {
                        failureRow(failure)
                    }
                    Color.clear.frame(height: 1).id(Self.bottomAnchor)
                }
                .frame(maxWidth: MurmurTheme.contentWidth, alignment: .leading)
                .padding(.horizontal, MurmurTheme.pageInset)
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity)
            }
            .defaultScrollAnchor(.bottom)
            .scrollDismissesKeyboard(.interactively)
            .animation(.easeInOut(duration: 0.22), value: model.lines)
            .animation(.easeInOut(duration: 0.22), value: model.openers)
            .animation(.easeInOut(duration: 0.22), value: model.isAwaitingReply)
            .onChange(of: model.lines.count) { _, _ in scrollToBottom(proxy) }
            .onChange(of: model.openers) { _, _ in scrollToBottom(proxy) }
            .onChange(of: model.isAwaitingReply) { _, _ in scrollToBottom(proxy) }
        }
    }

    private func bubble(_ line: PhotoRoomModel.Line) -> some View {
        let isOutgoing = line.author == .mine
        return HStack {
            if isOutgoing { Spacer(minLength: 56) }
            Text(line.text)
                .font(MurmurTheme.body(.body))
                .foregroundStyle(isOutgoing ? MurmurTheme.onAccent : MurmurTheme.ink)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(
                    isOutgoing ? MurmurTheme.outgoingBubble : MurmurTheme.raisedPaper,
                    in: RoomBubbleShape(isOutgoing: isOutgoing)
                )
                .overlay {
                    if !isOutgoing {
                        RoomBubbleShape(isOutgoing: false)
                            .stroke(MurmurTheme.rule, lineWidth: 1)
                    }
                }
                .accessibilityLabel(isOutgoing ? "你说：\(line.text)" : "Murmur 说：\(line.text)")
            if !isOutgoing { Spacer(minLength: 56) }
        }
        .frame(maxWidth: .infinity)
    }

    private var openersRow: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(model.openers.enumerated()), id: \.offset) { index, opener in
                Button {
                    model.pick(opener: opener)
                    composerFocused = true
                } label: {
                    HStack(spacing: 10) {
                        Text(opener)
                            .font(MurmurTheme.body(.subheadline))
                            .foregroundStyle(MurmurTheme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                        Image(systemName: "arrow.up.left")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(MurmurTheme.secondaryInk)
                    }
                    .padding(.horizontal, 14)
                    .frame(minHeight: 44)
                    .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 12))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12).stroke(MurmurTheme.rule, lineWidth: 1)
                    }
                }
                .buttonStyle(MurmurPressStyle())
                .accessibilityLabel("从这里说起：\(opener)")
                .accessibilityIdentifier("opener-\(index)")
            }
        }
        // No identifier on this stack: SwiftUI pushes a container's identifier
        // down onto every descendant, and it lands *over* theirs — all three
        // openers came out as "openers-row" and none of them as its own.
        .frame(maxWidth: 320, alignment: .leading)
    }

    private func failureRow(_ failure: MurmurFailure) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(failure.message)
                .font(MurmurTheme.body(.footnote))
                .foregroundStyle(MurmurTheme.coral)
                .fixedSize(horizontal: false, vertical: true)
            if model.canRetryOpening, failure.retryable {
                Button {
                    model.open()
                } label: {
                    Text("再试一次")
                        .font(MurmurTheme.body(.subheadline, weight: .semibold))
                        .foregroundStyle(MurmurTheme.ink)
                        .padding(.horizontal, 16)
                        .frame(minHeight: 44)
                        .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 12))
                        .overlay {
                            RoundedRectangle(cornerRadius: 12).stroke(MurmurTheme.rule, lineWidth: 1)
                        }
                }
                .buttonStyle(MurmurPressStyle())
                .accessibilityIdentifier("retry-photo-room")
            }
        }
        // Same reason as the openers: the retry button keeps its own name.
        .frame(maxWidth: 320, alignment: .leading)
    }

    // ---- Saying it ----------------------------------------------------------

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 6) {
            TextField("跟它说说这张照片", text: $model.draft, axis: .vertical)
                .font(MurmurTheme.body(.body))
                .foregroundStyle(MurmurTheme.ink)
                .lineLimit(1...4)
                .focused($composerFocused)
                .submitLabel(.send)
                .onSubmit { model.send() }
                .onChange(of: model.draft, initial: false) { _, newValue in
                    guard newValue.contains("\n") else { return }
                    model.draft = newValue.replacingOccurrences(of: "\n", with: "")
                    model.send()
                }
                .padding(.leading, 12)
                .padding(.vertical, 12)
                .accessibilityLabel("你对这张照片想说的")
                .accessibilityIdentifier("photo-room-composer")

            Button { model.send() } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(model.canSend ? MurmurTheme.paper : MurmurTheme.secondaryInk)
                    .frame(width: MurmurTheme.disc, height: MurmurTheme.disc)
                    .background(model.canSend ? MurmurTheme.ink : MurmurTheme.rule, in: Circle())
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(MurmurPressStyle())
            .disabled(!model.canSend)
            .accessibilityLabel("说给 Murmur")
            .accessibilityIdentifier("photo-room-send")
        }
        .padding(.horizontal, 5)
        .padding(.vertical, 4)
        .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 26))
        .overlay { RoundedRectangle(cornerRadius: 26).stroke(MurmurTheme.rule, lineWidth: 1) }
        .frame(maxWidth: MurmurTheme.contentWidth)
        .padding(.horizontal, MurmurTheme.pageInset)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity)
    }

    private static let bottomAnchor = "photo-room-bottom"
    private static let typingAnchor = "photo-room-typing"

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.22)) {
            proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
        }
    }
}

/// The room's own dots.  Same reason as `RoomBubbleShape`.
private struct RoomTypingIndicator: View {
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
        .background(MurmurTheme.raisedPaper, in: RoomBubbleShape(isOutgoing: false))
        .overlay {
            RoomBubbleShape(isOutgoing: false).stroke(MurmurTheme.rule, lineWidth: 1)
        }
        .onReceive(timer) { _ in
            guard !reduceMotion else { return }
            phase = (phase + 1) % 3
        }
        .accessibilityLabel("Murmur 正在输入")
        .accessibilityIdentifier("photo-room-typing")
    }
}
