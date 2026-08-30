import Foundation
import UIKit

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
    /// Appends one row.  `photoURL` is copied into transcript storage before
    /// the row lands, so a row never names a file that is about to be deleted.
    func record(_ message: MurmurMessage, photoURL: URL?) async
    func setDelivery(_ delivery: MurmurDeliveryState, for messageID: String)
    func setMomentID(_ momentID: String, for messageID: String)
    /// Takes a row back out.  For a line that never left: the room puts those
    /// words back in the field, and the history must not claim they were sent.
    func withdraw(_ messageID: String)
}

/// The on-device chat history.
///
/// Murmur's server keeps private memory, never a transcript, so the history a
/// person scrolls through exists only here.  Deleting the app deletes it, and
/// `clear()` is what the settings screen calls.
actor MurmurTranscriptStore {
    /// Old turns are dropped rather than kept forever: the transcript is a
    /// convenience for the reader, not an archive, and an unbounded JSON file
    /// would eventually cost a visible pause on launch.
    static let historyLimit = 600
    /// 当年今日's archive is the one place that *is* an archive — a day you
    /// talked about a photo should still be on the calendar next year — so it
    /// takes a far higher ceiling.  Still a ceiling: an unbounded file would
    /// eventually be read on every launch.
    static let archiveLimit = 6_000

    private let directory: URL
    private let fileURL: URL
    private let imageDirectory: URL
    private let limit: Int
    /// Photos copied in but not yet named by any saved message.  A send saves
    /// the transcript at least twice — once when the line appears, again when
    /// the reply lands — and the copy finishes somewhere in between.  Without
    /// this, the save in the middle prunes the photo it has not been told
    /// about, and the message ends up pointing at a file that no longer exists.
    private var pendingAdoptions: Set<String> = []

    init(directory: URL? = nil, limit: Int = MurmurTranscriptStore.historyLimit) {
        let base = directory ?? FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Murmur", isDirectory: true)
        self.directory = base
        self.fileURL = base.appendingPathComponent("transcript.json")
        self.imageDirectory = base.appendingPathComponent("images", isDirectory: true)
        self.limit = limit
    }

    /// The store 当年今日's rooms write into.  A directory of its own, beside
    /// the conversation and never mixed into it: what was said about an old
    /// photo belongs to the day it was said on, not to the chat.
    static func archive(directory: URL? = nil) -> MurmurTranscriptStore {
        let base = directory ?? FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Murmur", isDirectory: true)
            .appendingPathComponent("archive", isDirectory: true)
        return MurmurTranscriptStore(directory: base, limit: archiveLimit)
    }

    private func ensureDirectories() {
        for url in [directory, imageDirectory] {
            try? FileManager.default.createDirectory(
                at: url, withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.complete]
            )
        }
    }

    func load() -> [MurmurMessage] {
        guard let data = try? Data(contentsOf: fileURL) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard var messages = try? decoder.decode([MurmurMessage].self, from: data) else {
            return []
        }
        // A send interrupted by a crash or a force quit must not sit on a
        // spinner forever; it never reached the server.
        for index in messages.indices where messages[index].delivery == .sending {
            messages[index].delivery = .failed
        }
        return messages
    }

    func save(_ messages: [MurmurMessage]) {
        ensureDirectories()
        let trimmed = messages.suffix(limit)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(Array(trimmed)) else { return }
        try? data.write(to: fileURL, options: [.atomic, .completeFileProtection])
        let referenced = Set(trimmed.compactMap(\.imageFile))
        pruneImages(keeping: referenced.union(pendingAdoptions))
        // Anything this snapshot names is durable now and no longer pending.
        pendingAdoptions.subtract(referenced)
    }

    /// Copies a picked photo out of the temporary directory, which the photo
    /// loader clears, and into storage the transcript controls.
    func adoptImage(at url: URL, id: String) -> String? {
        ensureDirectories()
        let name = "\(id).\(url.pathExtension.isEmpty ? "jpg" : url.pathExtension)"
        let destination = imageDirectory.appendingPathComponent(name)
        try? FileManager.default.removeItem(at: destination)
        do {
            try FileManager.default.copyItem(at: url, to: destination)
            try? FileManager.default.setAttributes(
                [.protectionKey: FileProtectionType.complete], ofItemAtPath: destination.path
            )
            pendingAdoptions.insert(name)
            return name
        } catch {
            return nil
        }
    }

    nonisolated func imageURL(for name: String) -> URL {
        imageDirectory.appendingPathComponent(name)
    }

    private func pruneImages(keeping names: Set<String>) {
        guard let urls = try? FileManager.default.contentsOfDirectory(
            at: imageDirectory, includingPropertiesForKeys: nil
        ) else { return }
        for url in urls where !names.contains(url.lastPathComponent) {
            try? FileManager.default.removeItem(at: url)
        }
    }

    func clear() {
        pendingAdoptions = []
        try? FileManager.default.removeItem(at: fileURL)
        try? FileManager.default.removeItem(at: imageDirectory)
    }
}
