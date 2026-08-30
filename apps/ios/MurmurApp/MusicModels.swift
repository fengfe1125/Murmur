import Foundation

/// The stable, provider-neutral song reference that may cross the Murmur wire
/// and live in the on-device transcript. It is deliberately only a metadata
/// snapshot: credentials and playable URLs never belong in a chat message.
struct MusicTrackAttachmentV1: Codable, Equatable, Identifiable, Sendable {
    static let currentVersion = 1

    let version: Int
    let provider: String
    let trackID: String
    let title: String
    let artists: [String]
    let artworkURL: URL?
    let canonicalURL: URL
    let durationSeconds: Int?
    let explicit: Bool?

    var id: String { "\(provider):\(trackID)" }

    init(
        version: Int = Self.currentVersion,
        provider: String = "audius",
        trackID: String,
        title: String,
        artists: [String],
        artworkURL: URL?,
        canonicalURL: URL,
        durationSeconds: Int?,
        explicit: Bool?
    ) {
        self.version = version
        self.provider = provider
        self.trackID = trackID
        self.title = title
        self.artists = artists
        self.artworkURL = artworkURL
        self.canonicalURL = canonicalURL
        self.durationSeconds = durationSeconds
        self.explicit = explicit
    }

    enum CodingKeys: String, CodingKey {
        case version
        case provider
        case trackID = "track_id"
        case title
        case artists
        case artworkURL = "artwork_url"
        case canonicalURL = "canonical_url"
        case durationSeconds = "duration_seconds"
        case explicit
    }
}

struct MusicFeatureConfiguration: Equatable, Sendable {
    let audiusAPIBaseURL: URL
    let audiusAPIKey: String
    let audiusRedirectURI: URL
    let appName: String

    var isConfigured: Bool {
        !audiusAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && audiusRedirectURI.scheme != nil
            && audiusRedirectURI.host != nil
    }

    var callbackScheme: String? { audiusRedirectURI.scheme }

    /// A build with no Audius registration still constructs the music module,
    /// so nothing in the app has to be optional. Every call made through this
    /// configuration fails with `notConfigured`, and `isConfigured` is false,
    /// which is what actually keeps the feature off screen.
    static let unconfigured = MusicFeatureConfiguration(
        audiusAPIBaseURL: URL(string: "https://api.audius.co/v1")!,
        audiusAPIKey: "",
        audiusRedirectURI: URL(string: "murmur-audius://oauth/callback")!,
        appName: "Murmur"
    )

    static func from(bundle: Bundle = .main) -> MusicFeatureConfiguration? {
        let key = bundle.object(forInfoDictionaryKey: "AudiusAPIKey") as? String ?? ""
        let redirect = bundle.object(forInfoDictionaryKey: "AudiusRedirectURI") as? String ?? ""
        guard let baseURL = URL(string: "https://api.audius.co/v1"),
              let redirectURI = URL(string: redirect)
        else { return nil }
        let configuration = MusicFeatureConfiguration(
            audiusAPIBaseURL: baseURL,
            audiusAPIKey: key,
            audiusRedirectURI: redirectURI,
            appName: "Murmur"
        )
        return configuration.isConfigured ? configuration : nil
    }
}

/// Server-advertised capability gate. This is intentionally separate from the
/// local Audius app registration above: having an API key must not enable UI
/// before Murmur's own server says the matching wire is available.
struct MusicFeatureAvailability: Codable, Equatable, Sendable {
    let enabled: Bool
    let provider: String
    let playbackReporting: Bool

    enum CodingKeys: String, CodingKey {
        case enabled, provider
        case playbackReporting = "playback_reporting"
    }
}

struct AudiusUser: Codable, Equatable, Sendable {
    let id: String
    let name: String
    let handle: String
    let profilePictureURL: URL?
    let verified: Bool
}

struct AudiusPlaylist: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let name: String
    let artworkURL: URL?
    let trackCount: Int?
}

enum MusicPlaybackReportState: String, Codable, CaseIterable, Sendable {
    case started
    case paused
    case resumed
    case completed
    case stopped
}

struct MusicPlaybackEventTrackV1: Codable, Equatable, Sendable {
    let provider: String
    let trackID: String
    let title: String
    let artists: [String]
    let durationSeconds: Int?

    enum CodingKeys: String, CodingKey {
        case provider
        case trackID = "track_id"
        case title, artists
        case durationSeconds = "duration_seconds"
    }
}

struct MusicPlaybackEvent: Codable, Equatable, Sendable {
    let version: Int
    let sessionID: UUID
    let sequence: Int
    let state: MusicPlaybackReportState
    let track: MusicPlaybackEventTrackV1
    let occurredAt: Date

    enum CodingKeys: String, CodingKey {
        case version
        case sessionID = "session_id"
        case sequence, state, track
        case occurredAt = "occurred_at"
    }
}
