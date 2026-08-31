import Foundation

/// The only value the share extension hands to the main app. Text from a share
/// sheet is untrusted: titles, artists and cover URLs in it are never treated
/// as metadata. The authenticated server resolves it after the person opens
/// Murmur and explicitly reviews the result.
struct SharedMusicDraftV1: Codable, Equatable, Identifiable, Sendable {
    static let currentVersion = 1

    let version: Int
    let id: UUID
    let rawText: String
    let createdAt: Date

    init(
        version: Int = Self.currentVersion,
        id: UUID = UUID(),
        rawText: String,
        createdAt: Date = Date()
    ) {
        self.version = version
        self.id = id
        self.rawText = rawText
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case version, id
        case rawText = "raw_text"
        case createdAt = "created_at"
    }
}

/// One shared file is enough: iOS invokes a share extension for one explicit
/// share at a time, and a newer share should replace an older unconfirmed one.
/// Atomic replacement keeps the app from observing half-written JSON.
struct SharedMusicDraftStore {
    static let appGroupIdentifier = "group.com.sakura.Murmur"
    static let filename = "pending-music-share-v1.json"

    private let directory: URL?
    private let fileManager: FileManager

    init(
        appGroupIdentifier: String = Self.appGroupIdentifier,
        fileManager: FileManager = .default
    ) {
        self.fileManager = fileManager
        self.directory = fileManager.containerURL(
            forSecurityApplicationGroupIdentifier: appGroupIdentifier
        )
    }

    init(directory: URL, fileManager: FileManager = .default) {
        self.fileManager = fileManager
        self.directory = directory
    }

    var isAvailable: Bool { directory != nil }

    func save(_ draft: SharedMusicDraftV1) throws {
        guard let directory else { throw SharedMusicDraftStoreError.appGroupUnavailable }
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        let data = try JSONEncoder.sharedMusic.encode(draft)
        try data.write(to: directory.appendingPathComponent(Self.filename), options: .atomic)
    }

    func load() throws -> SharedMusicDraftV1? {
        guard let directory else { throw SharedMusicDraftStoreError.appGroupUnavailable }
        let url = directory.appendingPathComponent(Self.filename)
        guard fileManager.fileExists(atPath: url.path) else { return nil }
        return try JSONDecoder.sharedMusic.decode(SharedMusicDraftV1.self, from: Data(contentsOf: url))
    }

    func remove(id: UUID? = nil) throws {
        guard let directory else { throw SharedMusicDraftStoreError.appGroupUnavailable }
        let url = directory.appendingPathComponent(Self.filename)
        guard fileManager.fileExists(atPath: url.path) else { return }
        if let id, try load()?.id != id { return }
        try fileManager.removeItem(at: url)
    }
}

enum SharedMusicDraftStoreError: Error, Equatable {
    case appGroupUnavailable
}

private extension JSONEncoder {
    static var sharedMusic: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}

private extension JSONDecoder {
    static var sharedMusic: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}
