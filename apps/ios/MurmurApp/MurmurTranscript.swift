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

/// The on-device chat history.
///
/// Murmur's server keeps private memory, never a transcript, so the history a
/// person scrolls through exists only here.  Deleting the app deletes it, and
/// `clear()` is what the settings screen calls.
struct MurmurTranscriptDay: Identifiable, Sendable {
    var id: String
    var date: Date
    var count: Int
    var photos: Int
}

/// Separate SQLite databases preserve the existing channel/image directories.
/// A page is a read window; saving it never deletes rows outside that window.
actor MurmurTranscriptStore {
    static let pageSize = 100
    // Compatibility constants for old callers; these are no longer retention limits.
    static let historyLimit = 600
    static let archiveLimit = 6_000
    private let directory: URL
    private let fileURL: URL
    private let databaseURL: URL
    private let imageDirectory: URL
    private var migrated = false
    private var pendingAdoptions: Set<String> = []
    private(set) var lastError: String?

    init(directory: URL? = nil, limit: Int = MurmurTranscriptStore.pageSize) {
        let base = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Murmur", isDirectory: true)
        self.directory = base
        fileURL = base.appendingPathComponent("transcript.json")
        databaseURL = base.appendingPathComponent("transcript.sqlite")
        imageDirectory = base.appendingPathComponent("images", isDirectory: true)
    }

    static func archive(directory: URL? = nil) -> MurmurTranscriptStore {
        let base = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Murmur/archive", isDirectory: true)
        return MurmurTranscriptStore(directory: base)
    }

    private struct StorageFailure: Error {}
    private func execute(_ db: OpaquePointer, _ sql: String) throws {
        guard sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK else { throw StorageFailure() }
    }
    private func statement(_ db: OpaquePointer, _ sql: String) throws -> OpaquePointer {
        var result: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &result, nil) == SQLITE_OK, let result else { throw StorageFailure() }
        return result
    }
    private func bind(_ stmt: OpaquePointer, _ index: Int32, _ value: String) {
        _ = value.withCString { sqlite3_bind_text(stmt, index, $0, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self)) }
    }
    private func withDatabase<T>(_ work: (OpaquePointer) throws -> T) throws -> T {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.complete])
        var connection: OpaquePointer?
        guard sqlite3_open(databaseURL.path, &connection) == SQLITE_OK, let db = connection else {
            if let connection { sqlite3_close(connection) }
            throw StorageFailure()
        }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 5000)
        try execute(db, "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, sent_at REAL NOT NULL, filing_at REAL NOT NULL, image_file TEXT, payload TEXT NOT NULL); CREATE INDEX IF NOT EXISTS messages_sent ON messages(sent_at,id); CREATE INDEX IF NOT EXISTS messages_filing ON messages(filing_at,sent_at,id); CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);")
        if !migrated {
            let q = try statement(db, "SELECT value FROM metadata WHERE key='json_migrated'")
            let done = sqlite3_step(q) == SQLITE_ROW
            sqlite3_finalize(q)
            if !done {
                var legacy: [MurmurMessage] = []
                if FileManager.default.fileExists(atPath: fileURL.path) {
                    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
                    legacy = try decoder.decode([MurmurMessage].self, from: Data(contentsOf: fileURL))
                    guard Set(legacy.map(\.id)).count == legacy.count else { throw StorageFailure() }
                }
                try execute(db, "BEGIN IMMEDIATE")
                do {
                    try upsert(legacy, db: db)
                    // Compare the encoded payloads before switching away from JSON.
                    for row in legacy {
                        let check = try statement(db, "SELECT payload FROM messages WHERE id=?")
                        bind(check, 1, row.id)
                        defer { sqlite3_finalize(check) }
                        guard sqlite3_step(check) == SQLITE_ROW,
                              let raw = sqlite3_column_text(check, 0),
                              let data = String(cString: raw).data(using: .utf8),
                              try JSONDecoder().decode(MurmurMessage.self, from: data) == row else { throw StorageFailure() }
                    }
                    try execute(db, "INSERT INTO metadata VALUES('json_migrated','1'); COMMIT")
                } catch { try? execute(db, "ROLLBACK"); throw error }
            }
            migrated = true
        }
        for path in [databaseURL, URL(fileURLWithPath: databaseURL.path + "-wal"), URL(fileURLWithPath: databaseURL.path + "-shm")] where FileManager.default.fileExists(atPath: path.path) {
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: path.path)
        }
        return try work(db)
    }
    private func upsert(_ rows: [MurmurMessage], db: OpaquePointer) throws {
        let stmt = try statement(db, "INSERT INTO messages(id,sent_at,filing_at,image_file,payload) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET sent_at=excluded.sent_at,filing_at=excluded.filing_at,image_file=excluded.image_file,payload=excluded.payload")
        defer { sqlite3_finalize(stmt) }
        for row in rows {
            let raw = String(decoding: try JSONEncoder().encode(row), as: UTF8.self)
            bind(stmt, 1, row.id)
            sqlite3_bind_double(stmt, 2, row.sentAt.timeIntervalSince1970)
            sqlite3_bind_double(stmt, 3, (row.archiveDay ?? row.sentAt).timeIntervalSince1970)
            if let image = row.imageFile { bind(stmt, 4, image) } else { sqlite3_bind_null(stmt, 4) }
            bind(stmt, 5, raw)
            guard sqlite3_step(stmt) == SQLITE_DONE else { throw StorageFailure() }
            sqlite3_reset(stmt); sqlite3_clear_bindings(stmt)
        }
    }

    func load() -> [MurmurMessage] { page() }

    /// Keyset pagination disambiguates rows sharing a timestamp using their durable insertion order.
    func page(before: MurmurMessage? = nil, after: MurmurMessage? = nil, day: Date? = nil, archive: Bool = false, fromStart: Bool = true, limit: Int = MurmurTranscriptStore.pageSize) -> [MurmurMessage] {
        do {
            let result = try withDatabase { db in
                var clauses: [String] = []
                if before != nil { clauses.append("(sent_at < ? OR (sent_at = ? AND rowid < (SELECT rowid FROM messages WHERE id=?)))") }
                if after != nil { clauses.append("(sent_at > ? OR (sent_at = ? AND rowid > (SELECT rowid FROM messages WHERE id=?)))") }
                if day != nil { clauses.append("\(archive ? "filing_at" : "sent_at") >= ? AND \(archive ? "filing_at" : "sent_at") < ?") }
                let forward = after != nil || (day != nil && fromStart)
                let order = forward ? "ASC" : "DESC"
                let sql = "SELECT payload FROM messages" + (clauses.isEmpty ? "" : " WHERE " + clauses.joined(separator: " AND ")) + " ORDER BY sent_at \(order),rowid \(order) LIMIT ?"
                let stmt = try statement(db, sql); defer { sqlite3_finalize(stmt) }
                var index: Int32 = 1
                for anchor in [before, after].compactMap({ $0 }) {
                    sqlite3_bind_double(stmt,index,anchor.sentAt.timeIntervalSince1970); index += 1
                    sqlite3_bind_double(stmt,index,anchor.sentAt.timeIntervalSince1970); index += 1
                    bind(stmt,index,anchor.id); index += 1
                }
                if let day {
                    let cal = archive ? Calendar.murmur : Calendar.current
                    let start = cal.startOfDay(for: day)
                    let end = cal.date(byAdding: .day, value: 1, to: start)!
                    sqlite3_bind_double(stmt,index,start.timeIntervalSince1970); index += 1
                    sqlite3_bind_double(stmt,index,end.timeIntervalSince1970); index += 1
                }
                sqlite3_bind_int(stmt,index,Int32(min(500,max(1,limit))))
                var rows: [MurmurMessage] = []
                var status = sqlite3_step(stmt)
                while status == SQLITE_ROW {
                    guard let raw = sqlite3_column_text(stmt,0) else { throw StorageFailure() }
                    var row = try JSONDecoder().decode(MurmurMessage.self, from: Data(String(cString: raw).utf8))
                    if row.delivery == .sending { row.delivery = .failed }
                    rows.append(row); status = sqlite3_step(stmt)
                }
                guard status == SQLITE_DONE else { throw StorageFailure() }
                return forward ? rows : rows.reversed()
            }
            lastError = nil; return result
        } catch { lastError = "本机记录读取失败，原记录未删除。请重试。"; return [] }
    }

    @discardableResult func save(_ rows: [MurmurMessage]) -> Bool {
        do {
            try withDatabase { db in
                try execute(db, "BEGIN IMMEDIATE")
                do { try upsert(rows, db: db); try execute(db,"COMMIT") }
                catch { try? execute(db,"ROLLBACK"); throw error }
            }
            pendingAdoptions.subtract(rows.compactMap(\.imageFile))
            lastError = nil; return true
        } catch { lastError = "本机记录未保存，请重试。"; return false }
    }

    func days(archive: Bool = false) -> [MurmurTranscriptDay] {
        do {
            return try withDatabase { db in
                // Only the date index and counts are read, never message payloads.
                let column = archive ? "filing_at" : "sent_at"
                let stmt = try statement(db,"SELECT strftime('%Y-%m-%d',\(column),'unixepoch','localtime'),MIN(\(column)),COUNT(*),SUM(image_file IS NOT NULL) FROM messages GROUP BY 1 ORDER BY 1 DESC")
                defer { sqlite3_finalize(stmt) }
                var result: [MurmurTranscriptDay] = []
                while sqlite3_step(stmt) == SQLITE_ROW {
                    let date = Date(timeIntervalSince1970: sqlite3_column_double(stmt,1))
                    result.append(.init(id:String(cString:sqlite3_column_text(stmt,0)),date:(archive ? Calendar.murmur : Calendar.current).startOfDay(for:date),count:Int(sqlite3_column_int(stmt,2)),photos:Int(sqlite3_column_int(stmt,3))))
                }
                return result
            }
        } catch { lastError = "日期索引读取失败，请重试。"; return [] }
    }

    @discardableResult func remove(id: String) -> Bool {
        do {
            try withDatabase { db in
                let stmt = try statement(db,"DELETE FROM messages WHERE id=?"); defer { sqlite3_finalize(stmt) }
                bind(stmt,1,id); guard sqlite3_step(stmt) == SQLITE_DONE else { throw StorageFailure() }
            }
            try pruneUnreferencedImages(); lastError = nil; return true
        } catch { lastError = "删除未完成，请重试。"; return false }
    }

    func adoptImage(at url: URL, id: String) -> String? {
        guard !id.contains("/"), !id.contains("..") else { lastError = "图片标识无效。"; return nil }
        do {
            try FileManager.default.createDirectory(at: imageDirectory, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.complete])
            let name = "\(id).\(url.pathExtension.isEmpty ? "jpg" : url.pathExtension)"
            let data = try Data(contentsOf: url)
            try data.write(to:imageDirectory.appendingPathComponent(name),options:[.atomic,.completeFileProtection])
            pendingAdoptions.insert(name)
            lastError = nil; return name
        } catch { lastError = "图片未能保存到本机，请重试。"; return nil }
    }
    nonisolated func imageURL(for name: String) -> URL { imageDirectory.appendingPathComponent(name) }
    private func pruneUnreferencedImages() throws {
        let names = try withDatabase { db -> Set<String> in
            let stmt = try statement(db,"SELECT DISTINCT image_file FROM messages WHERE image_file IS NOT NULL"); defer { sqlite3_finalize(stmt) }
            var names = Set<String>()
            while sqlite3_step(stmt) == SQLITE_ROW { names.insert(String(cString:sqlite3_column_text(stmt,0))) }
            return names
        }
        if FileManager.default.fileExists(atPath:imageDirectory.path) {
            for url in try FileManager.default.contentsOfDirectory(at:imageDirectory,includingPropertiesForKeys:nil) where !names.contains(url.lastPathComponent) && !pendingAdoptions.contains(url.lastPathComponent) {
                try FileManager.default.removeItem(at:url)
            }
        }
    }
    @discardableResult func clear() -> Bool {
        do {
            try withDatabase { db in try execute(db,"DELETE FROM messages") }
            // A completed migration marker prevents an old JSON backup resurrecting
            // cleared rows. Remove the backup only on this explicit deletion.
            if FileManager.default.fileExists(atPath:fileURL.path) { try FileManager.default.removeItem(at:fileURL) }
            pendingAdoptions = []
            try pruneUnreferencedImages(); lastError = nil; return true
        } catch { lastError = "清空未完成，请重试。"; return false }
    }
}
