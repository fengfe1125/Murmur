import Foundation
import UIKit
import SQLite3

enum MurmurMessageAuthor: String, Codable, Sendable {
    case you
    case murmur
}

/// Only meaningful on messages the person sent.  The ticks describe transport,
/// not comprehension: one when the server took the upload, two when Murmur
/// began composing.  Nothing here claims that anybody "read" anything.
enum MurmurDeliveryState: String, Codable, Sendable {
    case sending
    case sent
    case answered
    case failed
}

struct MurmurMessage: Identifiable, Codable, Equatable, Sendable {
    let id: String
    let author: MurmurMessageAuthor
    var text: String
    /// File name inside the transcript's image directory, not a full path: the
    /// container path changes between installs, so storing one would rot.
    var imageFile: String?
    /// A stable metadata snapshot only.  Playback URLs and Audius credentials
    /// are always reacquired by the music module and never enter the transcript.
    var musicTrack: MusicTrackAttachmentV1?
    let sentAt: Date
    /// The calendar day this row belongs to when it resumes an older room.
    /// `sentAt` remains the real send time; older transcripts omit this field
    /// and continue to group by `sentAt`.
    let archiveDay: Date?
    var delivery: MurmurDeliveryState
    var momentID: String?
    /// The key this row's send went up under.  Kept so that a row still marked
    /// failed after a relaunch can be sent again as the *same* moment rather
    /// than a second one — without it a resend across a restart risks saying
    /// the same thing twice to a server that did quietly accept the first go.
    /// Optional because transcripts written before this existed decode without
    /// it; those rows fall back to a fresh key.
    var idempotencyKey: String?

    init(
        id: String = UUID().uuidString,
        author: MurmurMessageAuthor,
        text: String,
        imageFile: String? = nil,
        musicTrack: MusicTrackAttachmentV1? = nil,
        sentAt: Date = Date(),
        archiveDay: Date? = nil,
        delivery: MurmurDeliveryState = .sent,
        momentID: String? = nil,
        idempotencyKey: String? = nil
    ) {
        self.id = id
        self.author = author
        self.text = text
        self.imageFile = imageFile
        self.musicTrack = musicTrack
        self.sentAt = sentAt
        self.archiveDay = archiveDay
        self.delivery = delivery
        self.momentID = momentID
        self.idempotencyKey = idempotencyKey
    }
}

/// Somewhere for 当年今日's room to write its exchange down.
///
/// The room holds its own screen and its own turn, and what is said in there is
/// still said to Murmur — so it is kept, but kept apart: `MurmurArchive` files
/// it by the day it happened on, and the conversation never sees it.  The room
/// writes through this and never touches a store directly; two writers on one
/// JSON file would each overwrite the other's turn.
@MainActor
protocol MurmurRoomRecorder: AnyObject {
    var storageFailure: String? { get }
    func retryStorage() async
    /// Appends one row.  `photoURL` is copied into transcript storage before
    /// the row lands, so a row never names a file that is about to be deleted.
    func record(_ message: MurmurMessage, photoURL: URL?) async
    func setDelivery(_ delivery: MurmurDeliveryState, for messageID: String)
    func setMomentID(_ momentID: String, for messageID: String)
    /// Takes a row back out.  For a line that never left: the room puts those
    /// words back in the field, and the history must not claim they were sent.
    func withdraw(_ messageID: String)
}

extension MurmurRoomRecorder {
    var storageFailure: String? { nil }
    func retryStorage() async {}
}

/// Runs persistence jobs one at a time, in the order they were asked for, so a
/// later snapshot can never land before an earlier one.
@MainActor
final class MurmurSerialWriter {
    private var tail: Task<Void, Never>?

    func enqueue(_ job: @escaping @MainActor () async -> Void) {
        let previous = tail
        tail = Task { @MainActor in
            await previous?.value
            await job()
        }
    }

    /// Waits for every job enqueued so far.
    func drain() async {
        await tail?.value
    }
}

/// One local calendar day in a transcript's date index.
struct MurmurTranscriptDay: Identifiable, Sendable {
    var id: String
    var date: Date
    var count: Int
    var photos: Int
}

/// The on-device chat history.
///
/// Murmur's server keeps private memory, never a transcript, so the history a
/// person scrolls through exists only here.  Deleting the app deletes it, and
/// `clear()` is what the settings screen calls.
///
/// Each history is a SQLite database in its original directory, beside its
/// photos.  A page is a read window: saving it never deletes rows outside the
/// window, so history stays until the person clears it.
actor MurmurTranscriptStore {
    static let pageSize = 100

    private let directory: URL
    private let fileURL: URL
    private let databaseURL: URL
    private let imageDirectory: URL
    private var migrated = false
    /// Photos copied in but not yet named by any saved row, so a prune that
    /// runs in between leaves them alone.
    private var pendingAdoptions: Set<String> = []
    private(set) var lastError: String?

    init(directory: URL? = nil) {
        let base = directory ?? FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Murmur", isDirectory: true)
        self.directory = base
        fileURL = base.appendingPathComponent("transcript.json")
        databaseURL = base.appendingPathComponent("transcript.sqlite")
        imageDirectory = base.appendingPathComponent("images", isDirectory: true)
    }

    /// The store 当年今日's rooms write into: a directory of its own, beside
    /// the conversation and never mixed into it.
    static func archive(directory: URL? = nil) -> MurmurTranscriptStore {
        let base = directory ?? FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Murmur/archive", isDirectory: true)
        return MurmurTranscriptStore(directory: base)
    }

    // ---- SQLite -------------------------------------------------------------

    private struct StorageFailure: Error {}

    private static let schema = """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS messages(
            id TEXT PRIMARY KEY,
            sent_at REAL NOT NULL,
            filing_at REAL NOT NULL,
            image_file TEXT,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS messages_sent ON messages(sent_at, id);
        CREATE INDEX IF NOT EXISTS messages_filing ON messages(filing_at, sent_at, id);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """

    private func execute(_ db: OpaquePointer, _ sql: String) throws {
        guard sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK else { throw StorageFailure() }
    }

    private func statement(_ db: OpaquePointer, _ sql: String) throws -> OpaquePointer {
        var result: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &result, nil) == SQLITE_OK, let result else {
            throw StorageFailure()
        }
        return result
    }

    private func bind(_ statement: OpaquePointer, _ index: Int32, _ value: String) {
        _ = value.withCString {
            sqlite3_bind_text(statement, index, $0, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
        }
    }

    /// Opens the database for one piece of work.  The first open imports the
    /// legacy JSON history before any work runs.
    private func withDatabase<T>(_ work: (OpaquePointer) throws -> T) throws -> T {
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.complete]
        )
        var connection: OpaquePointer?
        guard sqlite3_open(databaseURL.path, &connection) == SQLITE_OK, let db = connection else {
            if let connection { sqlite3_close(connection) }
            throw StorageFailure()
        }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 5000)
        try execute(db, Self.schema)
        if !migrated {
            try importLegacyJSON(db)
            migrated = true
        }
        for path in [databaseURL.path, databaseURL.path + "-wal", databaseURL.path + "-shm"]
        where FileManager.default.fileExists(atPath: path) {
            try FileManager.default.setAttributes(
                [.protectionKey: FileProtectionType.complete],
                ofItemAtPath: path
            )
        }
        return try work(db)
    }

    /// Moves `transcript.json` into the database, once.  Every row is read back
    /// and compared before the marker is written; a failure rolls the import
    /// back and leaves the JSON file where it was.
    private func importLegacyJSON(_ db: OpaquePointer) throws {
        let marker = try statement(db, "SELECT value FROM metadata WHERE key='json_migrated'")
        let done = sqlite3_step(marker) == SQLITE_ROW
        sqlite3_finalize(marker)
        guard !done else { return }
        var legacy: [MurmurMessage] = []
        if FileManager.default.fileExists(atPath: fileURL.path) {
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            legacy = try decoder.decode([MurmurMessage].self, from: Data(contentsOf: fileURL))
            guard Set(legacy.map(\.id)).count == legacy.count else { throw StorageFailure() }
        }
        try execute(db, "BEGIN IMMEDIATE")
        do {
            try upsert(legacy, db: db)
            for row in legacy {
                guard try storedRow(id: row.id, db: db) == row else { throw StorageFailure() }
            }
            try execute(db, "INSERT INTO metadata VALUES('json_migrated','1'); COMMIT")
        } catch {
            try? execute(db, "ROLLBACK")
            throw error
        }
    }

    private func storedRow(id: String, db: OpaquePointer) throws -> MurmurMessage? {
        let query = try statement(db, "SELECT payload FROM messages WHERE id=?")
        defer { sqlite3_finalize(query) }
        bind(query, 1, id)
        guard sqlite3_step(query) == SQLITE_ROW, let raw = sqlite3_column_text(query, 0) else { return nil }
        return try JSONDecoder().decode(MurmurMessage.self, from: Data(String(cString: raw).utf8))
    }

    private func upsert(_ rows: [MurmurMessage], db: OpaquePointer) throws {
        let insert = try statement(db, """
            INSERT INTO messages(id, sent_at, filing_at, image_file, payload) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                sent_at = excluded.sent_at,
                filing_at = excluded.filing_at,
                image_file = excluded.image_file,
                payload = excluded.payload
            """)
        defer { sqlite3_finalize(insert) }
        let encoder = JSONEncoder()
        for row in rows {
            let payload = String(decoding: try encoder.encode(row), as: UTF8.self)
            bind(insert, 1, row.id)
            sqlite3_bind_double(insert, 2, row.sentAt.timeIntervalSince1970)
            sqlite3_bind_double(insert, 3, (row.archiveDay ?? row.sentAt).timeIntervalSince1970)
            if let image = row.imageFile {
                bind(insert, 4, image)
            } else {
                sqlite3_bind_null(insert, 4)
            }
            bind(insert, 5, payload)
            guard sqlite3_step(insert) == SQLITE_DONE else { throw StorageFailure() }
            sqlite3_reset(insert)
            sqlite3_clear_bindings(insert)
        }
    }

    // ---- Reading ------------------------------------------------------------

    /// The newest page.
    func load() -> [MurmurMessage] { page() }

    /// One read window, oldest first.  Keyset pagination breaks ties between
    /// rows sharing a timestamp with their durable insertion order.
    func page(
        before: MurmurMessage? = nil,
        after: MurmurMessage? = nil,
        day: Date? = nil,
        archive: Bool = false,
        fromStart: Bool = true,
        limit: Int = MurmurTranscriptStore.pageSize
    ) -> [MurmurMessage] {
        do {
            let result = try withDatabase { db in
                let dayColumn = archive ? "filing_at" : "sent_at"
                var clauses: [String] = []
                if before != nil {
                    clauses.append("(sent_at < ? OR (sent_at = ? AND rowid < (SELECT rowid FROM messages WHERE id=?)))")
                }
                if after != nil {
                    clauses.append("(sent_at > ? OR (sent_at = ? AND rowid > (SELECT rowid FROM messages WHERE id=?)))")
                }
                if day != nil {
                    clauses.append("\(dayColumn) >= ? AND \(dayColumn) < ?")
                }
                let forward = after != nil || (day != nil && fromStart)
                let order = forward ? "ASC" : "DESC"
                let filter = clauses.isEmpty ? "" : " WHERE " + clauses.joined(separator: " AND ")
                let query = try statement(
                    db,
                    "SELECT payload FROM messages\(filter) ORDER BY sent_at \(order), rowid \(order) LIMIT ?"
                )
                defer { sqlite3_finalize(query) }
                var index: Int32 = 1
                for anchor in [before, after].compactMap({ $0 }) {
                    sqlite3_bind_double(query, index, anchor.sentAt.timeIntervalSince1970)
                    sqlite3_bind_double(query, index + 1, anchor.sentAt.timeIntervalSince1970)
                    bind(query, index + 2, anchor.id)
                    index += 3
                }
                if let day {
                    let calendar = archive ? Calendar.murmur : Calendar.current
                    let start = calendar.startOfDay(for: day)
                    let end = calendar.date(byAdding: .day, value: 1, to: start) ?? start.addingTimeInterval(86_400)
                    sqlite3_bind_double(query, index, start.timeIntervalSince1970)
                    sqlite3_bind_double(query, index + 1, end.timeIntervalSince1970)
                    index += 2
                }
                sqlite3_bind_int(query, index, Int32(min(500, max(1, limit))))
                let decoder = JSONDecoder()
                var rows: [MurmurMessage] = []
                var status = sqlite3_step(query)
                while status == SQLITE_ROW {
                    guard let raw = sqlite3_column_text(query, 0) else { throw StorageFailure() }
                    var row = try decoder.decode(MurmurMessage.self, from: Data(String(cString: raw).utf8))
                    // A send interrupted by a crash or a force quit never
                    // reached the server; it must not spin forever.
                    if row.delivery == .sending { row.delivery = .failed }
                    rows.append(row)
                    status = sqlite3_step(query)
                }
                guard status == SQLITE_DONE else { throw StorageFailure() }
                return forward ? rows : rows.reversed()
            }
            lastError = nil
            return result
        } catch {
            lastError = "本机记录读取失败，原记录未删除。请重试。"
            return []
        }
    }

    /// The local days that have rows, newest first.  Reads the index columns
    /// only, never message payloads.
    func days(archive: Bool = false) -> [MurmurTranscriptDay] {
        do {
            return try withDatabase { db in
                let column = archive ? "filing_at" : "sent_at"
                let query = try statement(db, """
                    SELECT strftime('%Y-%m-%d', \(column), 'unixepoch', 'localtime'),
                           MIN(\(column)), COUNT(*), SUM(image_file IS NOT NULL)
                    FROM messages GROUP BY 1 ORDER BY 1 DESC
                    """)
                defer { sqlite3_finalize(query) }
                let calendar = archive ? Calendar.murmur : Calendar.current
                var result: [MurmurTranscriptDay] = []
                while sqlite3_step(query) == SQLITE_ROW {
                    let first = Date(timeIntervalSince1970: sqlite3_column_double(query, 1))
                    result.append(.init(
                        id: String(cString: sqlite3_column_text(query, 0)),
                        date: calendar.startOfDay(for: first),
                        count: Int(sqlite3_column_int(query, 2)),
                        photos: Int(sqlite3_column_int(query, 3))
                    ))
                }
                return result
            }
        } catch {
            lastError = "日期索引读取失败，请重试。"
            return []
        }
    }

    // ---- Writing ------------------------------------------------------------

    /// Inserts or updates these rows.  Rows it does not name are left alone.
    @discardableResult
    func save(_ rows: [MurmurMessage]) -> Bool {
        do {
            try withDatabase { db in
                try execute(db, "BEGIN IMMEDIATE")
                do {
                    try upsert(rows, db: db)
                    try execute(db, "COMMIT")
                } catch {
                    try? execute(db, "ROLLBACK")
                    throw error
                }
            }
            // Anything these rows name is durable now and no longer pending.
            pendingAdoptions.subtract(rows.compactMap(\.imageFile))
            lastError = nil
            return true
        } catch {
            lastError = "本机记录未保存，请重试。"
            return false
        }
    }

    /// Deletes one row, and any photo that only it referred to.
    @discardableResult
    func remove(id: String) -> Bool {
        do {
            try withDatabase { db in
                let delete = try statement(db, "DELETE FROM messages WHERE id=?")
                defer { sqlite3_finalize(delete) }
                bind(delete, 1, id)
                guard sqlite3_step(delete) == SQLITE_DONE else { throw StorageFailure() }
            }
            try pruneUnreferencedImages()
            lastError = nil
            return true
        } catch {
            lastError = "删除未完成，请重试。"
            return false
        }
    }

    @discardableResult
    func clear() -> Bool {
        do {
            try withDatabase { db in try execute(db, "DELETE FROM messages") }
            // The migration marker keeps an old JSON copy from bringing cleared
            // rows back; the copy itself goes only on this explicit deletion.
            if FileManager.default.fileExists(atPath: fileURL.path) {
                try FileManager.default.removeItem(at: fileURL)
            }
            pendingAdoptions = []
            try pruneUnreferencedImages()
            lastError = nil
            return true
        } catch {
            lastError = "清空未完成，请重试。"
            return false
        }
    }

    // ---- Photos -------------------------------------------------------------

    /// Copies a picked photo out of the temporary directory, which the photo
    /// loader clears, and into storage the transcript controls.
    func adoptImage(at url: URL, id: String) -> String? {
        guard !id.contains("/"), !id.contains("..") else {
            lastError = "图片标识无效。"
            return nil
        }
        do {
            try FileManager.default.createDirectory(
                at: imageDirectory,
                withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.complete]
            )
            let name = "\(id).\(url.pathExtension.isEmpty ? "jpg" : url.pathExtension)"
            let data = try Data(contentsOf: url)
            try data.write(
                to: imageDirectory.appendingPathComponent(name),
                options: [.atomic, .completeFileProtection]
            )
            pendingAdoptions.insert(name)
            lastError = nil
            return name
        } catch {
            lastError = "图片未能保存到本机，请重试。"
            return nil
        }
    }

    nonisolated func imageURL(for name: String) -> URL {
        imageDirectory.appendingPathComponent(name)
    }

    private func pruneUnreferencedImages() throws {
        let referenced = try withDatabase { db -> Set<String> in
            let query = try statement(db, "SELECT DISTINCT image_file FROM messages WHERE image_file IS NOT NULL")
            defer { sqlite3_finalize(query) }
            var names = Set<String>()
            while sqlite3_step(query) == SQLITE_ROW {
                names.insert(String(cString: sqlite3_column_text(query, 0)))
            }
            return names
        }
        guard FileManager.default.fileExists(atPath: imageDirectory.path) else { return }
        let files = try FileManager.default.contentsOfDirectory(at: imageDirectory, includingPropertiesForKeys: nil)
        for url in files
        where !referenced.contains(url.lastPathComponent) && !pendingAdoptions.contains(url.lastPathComponent) {
            try FileManager.default.removeItem(at: url)
        }
    }
}
