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
    /// The current bounded reading window, oldest first. The database index
    /// supplies dates outside this window without loading their messages.
    @Published private(set) var rows: [MurmurMessage] = []
    @Published private(set) var isLoaded = false
    @Published private(set) var dayIndex: [MurmurTranscriptDay] = []
    @Published private(set) var storageFailure: String?
    private var hasInitialRows = false
    private let writer = MurmurSerialWriter()
    private var clearing = false
    private var storageGeneration = 0
    private var pendingPhotoRows: [String: (MurmurMessage, URL)] = [:]

    let store: MurmurTranscriptStore

    private let calendar: Calendar

    init(
        store: MurmurTranscriptStore? = nil,
        calendar: Calendar = .murmur,
        initialRows: [MurmurMessage]? = nil
    ) {
        self.store = store ?? MurmurTranscriptStore.archive()
        self.calendar = calendar
        if let initialRows {
            hasInitialRows = true
            rows = initialRows
            isLoaded = true
        }
    }

    func load() async {
        guard !isLoaded else { return }
        rows = await store.load()
        dayIndex = await store.days(archive: true)
        storageFailure = await store.lastError
        isLoaded = true
    }

    /// Which photo file a row names, resolved against the archive's own images.
    nonisolated func imageURL(for name: String) -> URL { store.imageURL(for: name) }

    // ---- Reading -------------------------------------------------------------

    /// The days that have anything on them, as `startOfDay` dates.
    var daysWithRooms: Set<Date> {
        Set(dayIndex.map(\.date)).union(rows.map { calendar.startOfDay(for: filingDate(for: $0)) })
    }

    /// One day's thread, in the order it happened.
    func rows(on day: Date) -> [MurmurMessage] {
        let start = calendar.startOfDay(for: day)
        return rows.filter { calendar.isDate(filingDate(for: $0), inSameDayAs: start) }
    }

    /// How many photos that day carried.  The count the calendar's day row
    /// shows — rows without a picture are the talk about one, not another one.
    func photoCount(on day: Date) -> Int {
        if let indexed = dayIndex.first(where: { calendar.isDate($0.date, inSameDayAs: day) }) {
            return indexed.photos
        }
        return rows(on: day).count { $0.imageFile != nil }
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

    func load(day: Date, earlier: Bool = false) async {
        guard !hasInitialRows else { return }
        let anchor = earlier ? rows(on: day).first : nil
        let page = await store.page(before: anchor, day: day, archive: true, fromStart: false)
        storageFailure = await store.lastError
        if earlier {
            var seen = Set<String>()
            rows = Array((page + rows).filter { seen.insert($0.id).inserted }.prefix(400))
        } else {
            rows = page
        }
    }

    // ---- Writing -------------------------------------------------------------

    func record(_ message: MurmurMessage, photoURL: URL?) async {
        guard !clearing else { return }
        let generation = storageGeneration
        await writer.drain()
        guard generation == storageGeneration else { return }
        var row = message
        if let photoURL {
            row.imageFile = await store.adoptImage(at: photoURL, id: row.id)
            guard generation == storageGeneration else { return }
            if row.imageFile == nil {
                pendingPhotoRows[row.id] = (message, photoURL)
                storageFailure = await store.lastError
            } else {
                pendingPhotoRows.removeValue(forKey: row.id)
            }
        }
        guard generation == storageGeneration else { return }
        // A row with neither words nor a picture is an empty bubble; the copy
        // failing is not a reason to put one in the archive.
        guard !row.text.isEmpty || row.imageFile != nil else { return }
        rows.append(row)
        // `record` is already async, so do not return before this newly added
        // row is durable.  Besides making the contract honest for callers that
        // immediately reload, this keeps an app suspension directly after a
        // send from losing the optimistic row.
        if !(await store.save([row])) { storageFailure = await store.lastError }
        dayIndex = await store.days(archive: true)
        if rows.count > 400 { rows.removeFirst(rows.count - 400) }
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
        writer.enqueue { [self, store] in
            if !(await store.remove(id: messageID)) {
                storageFailure = await store.lastError
            }
            dayIndex = await store.days(archive: true)
        }
    }

    func clear() async {
        clearing = true
        storageGeneration += 1
        defer { clearing = false }
        await writer.drain()
        if await store.clear() {
            rows = []
            dayIndex = []
            storageFailure = nil
            pendingPhotoRows = [:]
        } else {
            storageFailure = await store.lastError
        }
    }

    func retryStorage() async {
        for (id, pending) in pendingPhotoRows {
            var row = pending.0
            guard let image = await store.adoptImage(at: pending.1, id: id) else {
                storageFailure = await store.lastError
                return
            }
            row.imageFile = image
            guard await store.save([row]) else {
                storageFailure = await store.lastError
                return
            }
            rows.removeAll { $0.id == id }
            rows.append(row)
            pendingPhotoRows.removeValue(forKey: id)
        }
        persist()
        await writer.drain()
    }

    private func persist() {
        guard !clearing else { return }
        let snapshot = rows
        writer.enqueue { [self, store] in
            if await store.save(snapshot) {
                if pendingPhotoRows.isEmpty { storageFailure = nil }
            } else {
                storageFailure = await store.lastError
            }
            dayIndex = await store.days(archive: true)
        }
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
