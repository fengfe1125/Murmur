import Foundation
import SwiftUI

/// What was said in 当年今日's rooms, kept by the day it was said on.
///
/// Deliberately not the conversation.  A room is about one old photo, and the
/// thing worth coming back to is "which day did we talk about this" — so the
/// archive is indexed by day and the chat stays a chat.  Same file format and
/// the same complete file protection as the transcript; a directory of its own.
@MainActor
final class MurmurArchive: ObservableObject, MurmurRoomRecorder {
    /// Every row, oldest first.  Days are a view over this rather than a second
    /// structure to keep in step.
    @Published private(set) var rows: [MurmurMessage] = []
    @Published private(set) var isLoaded = false

    private let store: MurmurTranscriptStore
    private let calendar: Calendar

    init(
        store: MurmurTranscriptStore? = nil,
        calendar: Calendar = .murmur,
        initialRows: [MurmurMessage]? = nil
    ) {
        self.store = store ?? MurmurTranscriptStore.archive()
        self.calendar = calendar
        if let initialRows {
            rows = initialRows
            isLoaded = true
        }
    }

    func load() async {
        guard !isLoaded else { return }
        rows = await store.load()
        isLoaded = true
    }

    /// Which photo file a row names, resolved against the archive's own images.
    nonisolated func imageURL(for name: String) -> URL { store.imageURL(for: name) }

    // ---- Reading -------------------------------------------------------------

    /// The days that have anything on them, as `startOfDay` dates.
    var daysWithRooms: Set<Date> {
        Set(rows.map { calendar.startOfDay(for: filingDate(for: $0)) })
    }

    /// One day's thread, in the order it happened.
    func rows(on day: Date) -> [MurmurMessage] {
        let start = calendar.startOfDay(for: day)
        return rows.filter { calendar.isDate(filingDate(for: $0), inSameDayAs: start) }
    }

    /// How many photos that day carried.  The count the calendar's day row
    /// shows — rows without a picture are the talk about one, not another one.
    func photoCount(on day: Date) -> Int {
        rows(on: day).count { $0.imageFile != nil }
    }

    /// The most recent days that have anything on them, newest first.
    func recentDays(limit: Int = 60) -> [Date] {
        daysWithRooms.sorted(by: >).prefix(limit).map { $0 }
    }

    /// The durable server moments that make up the most recent photo room on
    /// one archive day.  Rows are ordered by their real send time, duplicate
    /// moment IDs collapse in first-seen order, and the newest bounded window
    /// is sent so a long room does not crowd out its latest turns.
    func contextMomentIDs(on day: Date, limit: Int = 8) -> [String] {
        guard limit > 0 else { return [] }
        let ordered = rows(on: day).enumerated().sorted { left, right in
            if left.element.sentAt == right.element.sentAt {
                return left.offset < right.offset
            }
            return left.element.sentAt < right.element.sentAt
        }.map(\.element)
        let roomStart = ordered.lastIndex(where: { $0.imageFile != nil }) ?? ordered.startIndex
        var seen = Set<String>()
        let moments = ordered[roomStart...].compactMap { row -> String? in
            guard let momentID = row.momentID, !momentID.isEmpty,
                  seen.insert(momentID).inserted else { return nil }
            return momentID
        }
        return Array(moments.suffix(limit))
    }

    // ---- Writing -------------------------------------------------------------

    func record(_ message: MurmurMessage, photoURL: URL?) async {
        var row = message
        if let photoURL {
            row.imageFile = await store.adoptImage(at: photoURL, id: row.id)
        }
        // A row with neither words nor a picture is an empty bubble; the copy
        // failing is not a reason to put one in the archive.
        guard !row.text.isEmpty || row.imageFile != nil else { return }
        rows.append(row)
        // `record` is already async, so do not return before this newly added
        // row is durable.  Besides making the contract honest for callers that
        // immediately reload, this keeps an app suspension directly after a
        // send from losing the optimistic row.
        let snapshot = rows
        await store.save(snapshot)
    }

    func setDelivery(_ delivery: MurmurDeliveryState, for messageID: String) {
        guard let index = rows.firstIndex(where: { $0.id == messageID }) else { return }
        guard rows[index].delivery != delivery else { return }
        rows[index].delivery = delivery
        persist()
    }

    func setMomentID(_ momentID: String, for messageID: String) {
        guard let index = rows.firstIndex(where: { $0.id == messageID }) else { return }
        guard rows[index].momentID != momentID else { return }
        rows[index].momentID = momentID
        persist()
    }

    func withdraw(_ messageID: String) {
        guard rows.contains(where: { $0.id == messageID }) else { return }
        rows.removeAll { $0.id == messageID }
        persist()
    }

    func clear() async {
        rows = []
        await store.clear()
    }

    private func persist() {
        let snapshot = rows
        Task { [store] in await store.save(snapshot) }
    }

    private func filingDate(for row: MurmurMessage) -> Date {
        row.archiveDay ?? row.sentAt
    }
}

/// The composer attached to one archived day.  It deliberately owns no second
/// transcript: every optimistic row and every streamed bubble is written to
/// `MurmurArchive`, tagged with the selected logical day and its real send time.
@MainActor
final class ArchiveDayModel: ObservableObject {
    enum Phase: Equatable {
        case listening
        case sending
    }

    let day: Date
    let archive: MurmurArchive
    @Published var draft = ""
    @Published private(set) var phase: Phase = .listening
    @Published private(set) var failure: MurmurFailure?

    var canSend: Bool {
        phase == .listening
            && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    var isAwaitingReply: Bool { phase == .sending }

    private let api: any MurmurAPIClient
    private let requestTimeoutSeconds: TimeInterval
    private let bubblePacing: MurmurBubblePacing
    private var turn: Task<Void, Never>?
    private var closed = false
    /// The row this turn put in the archive before any receipt came back.
    /// Cleared the moment the server takes it; until then it is the row that
    /// leaving the day has to account for.
    private var unsentRowID: String?

    init(
        day: Date,
        archive: MurmurArchive,
        api: any MurmurAPIClient,
        requestTimeoutSeconds: TimeInterval = 45,
        bubblePacing: MurmurBubblePacing = .human,
        calendar: Calendar = .murmur
    ) {
        self.day = calendar.startOfDay(for: day)
        self.archive = archive
        self.api = api
        self.requestTimeoutSeconds = requestTimeoutSeconds
        self.bubblePacing = bubblePacing
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, phase == .listening, !closed else { return }
        let contextMomentIDs = archive.contextMomentIDs(on: day)
        let key = UUID().uuidString
        let row = MurmurMessage(
            author: .you,
            text: text,
            sentAt: Date(),
            archiveDay: day,
            delivery: .sending,
            idempotencyKey: key
        )
        draft = ""
        failure = nil
        phase = .sending
        turn?.cancel()
        turn = Task { [weak self] in
            guard let self else { return }
            self.unsentRowID = row.id
            await self.archive.record(row, photoURL: nil)
            var receiptMomentID: String?
            do {
                let receipt = try await withTimeout(
                    seconds: self.requestTimeoutSeconds
                ) { [api = self.api] in
                    try await api.createMoment(
                        note: text,
                        photo: nil,
                        idempotencyKey: key,
                        intent: nil,
                        contextMomentIDs: contextMomentIDs
                    )
                }
                receiptMomentID = receipt.momentID
                self.unsentRowID = nil
                self.archive.setMomentID(receipt.momentID, for: row.id)
                self.archive.setDelivery(.sent, for: row.id)
                try await MurmurRoomEventConsumer(
                    api: self.api,
                    pacing: self.bubblePacing
                ).consume(momentID: receipt.momentID) { bubble in
                    await self.archive.record(
                        MurmurMessage(
                            id: bubble.id,
                            author: .murmur,
                            text: bubble.text,
                            sentAt: Date(),
                            archiveDay: self.day,
                            momentID: receipt.momentID
                        ),
                        photoURL: nil
                    )
                }
                self.archive.setDelivery(.answered, for: row.id)
                self.phase = .listening
            } catch is CancellationError {
                self.finishFailedTurn(
                    row: row,
                    text: text,
                    receiptMomentID: receiptMomentID,
                    failure: nil
                )
            } catch {
                self.finishFailedTurn(
                    row: row,
                    text: text,
                    receiptMomentID: receiptMomentID,
                    failure: MurmurFailure.from(error)
                )
            }
        }
    }

    func close() {
        closed = true
        turn?.cancel()
        turn = nil
        // Leaving mid-send is not the same as a line that came back: the
        // receipt never landed, so as far as this device knows it never left,
        // but the words are on their way out of the composer with the screen.
        // The same call the photo room makes — the day keeps the row and says
        // it failed, rather than quietly deleting what was typed.
        if let unsentRowID { archive.setDelivery(.failed, for: unsentRowID) }
        unsentRowID = nil
    }

    private func finishFailedTurn(
        row: MurmurMessage,
        text: String,
        receiptMomentID: String?,
        failure: MurmurFailure?
    ) {
        // `close` has already filed this row and there is no composer left to
        // put the words back into.
        guard !closed else { return }
        unsentRowID = nil
        if receiptMomentID == nil {
            archive.withdraw(row.id)
            if draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                draft = text
            }
        }
        self.failure = failure
        phase = .listening
    }
}
