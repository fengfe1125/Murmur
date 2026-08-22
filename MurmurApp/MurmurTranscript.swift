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
    let sentAt: Date
    var delivery: MurmurDeliveryState
    var momentID: String?

    init(
        id: String = UUID().uuidString,
        author: MurmurMessageAuthor,
        text: String,
        imageFile: String? = nil,
        sentAt: Date = Date(),
        delivery: MurmurDeliveryState = .sent,
        momentID: String? = nil
    ) {
        self.id = id
        self.author = author
        self.text = text
        self.imageFile = imageFile
        self.sentAt = sentAt
        self.delivery = delivery
        self.momentID = momentID
    }
}

/// Somewhere to write a row into the conversation's scrollback from outside it.
///
/// 当年今日's room holds its own screen and its own turn, but what is said in
/// there is still said to Murmur — so it belongs in the same history as
/// everything else rather than disappearing when the room closes.  The room
/// writes through this and never touches the store, which stays the session's
/// to own: two writers on one JSON file would each overwrite the other's turn.
@MainActor
protocol MurmurTranscriptRecorder: AnyObject {
    /// Appends one row.  `photoURL` is copied into transcript storage before
    /// the row lands, so a row never names a file that is about to be deleted.
    func record(_ message: MurmurMessage, photoURL: URL?) async
    func setDelivery(_ delivery: MurmurDeliveryState, for messageID: String)
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

    private let directory: URL
    private let fileURL: URL
    private let imageDirectory: URL
    /// Photos copied in but not yet named by any saved message.  A send saves
    /// the transcript at least twice — once when the line appears, again when
    /// the reply lands — and the copy finishes somewhere in between.  Without
    /// this, the save in the middle prunes the photo it has not been told
    /// about, and the message ends up pointing at a file that no longer exists.
    private var pendingAdoptions: Set<String> = []

    init(directory: URL? = nil) {
        let base = directory ?? FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Murmur", isDirectory: true)
        self.directory = base
        self.fileURL = base.appendingPathComponent("transcript.json")
        self.imageDirectory = base.appendingPathComponent("images", isDirectory: true)
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
        let trimmed = messages.suffix(Self.historyLimit)
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
