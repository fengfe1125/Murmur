@preconcurrency import DeviceCheck
import Foundation
import UIKit

enum MurmurPhase: String, Sendable {
    case idle
    case preparingPhoto
    case ready
    case uploading
    case responding
    case complete
    case quiet
    case error

    var isBusy: Bool {
        self == .preparingPhoto || self == .uploading || self == .responding
    }
}

enum MurmurConnectionState: Equatable, Sendable {
    case checking
    case needsEnrollment
    case connected
    case offline(String)

    var label: String {
        switch self {
        case .checking: "正在连接"
        case .needsEnrollment: "等待邀请"
        case .connected: "已连接"
        case .offline: "连接异常"
        }
    }
}

struct MurmurFailure: Error, Equatable, Sendable {
    let code: String
    let message: String
    let retryable: Bool

    static func from(_ error: Error) -> Self {
        if let failure = error as? MurmurFailure {
            return failure
        }
        if error is CancellationError {
            return .init(code: "cancelled", message: "已取消。", retryable: true)
        }
        if let urlError = error as? URLError {
            switch urlError.code {
            case .timedOut:
                return .init(code: "timeout", message: "等待时间有点久，请再试一次。", retryable: true)
            case .notConnectedToInternet, .networkConnectionLost, .cannotFindHost, .cannotConnectToHost:
                return .init(code: "network_error", message: "暂时没有连上 Murmur。", retryable: true)
            default:
                return .init(code: "network_error", message: "暂时没有连上 Murmur。", retryable: true)
            }
        }
        let nsError = error as NSError
        if nsError.domain == DCErrorDomain {
            if nsError.code == DCError.invalidKey.rawValue {
                return .init(
                    code: "app_attest_invalid_key",
                    message: "这台设备的安全绑定已失效，需要重新连接。",
                    retryable: false
                )
            }
            if nsError.code == DCError.serverUnavailable.rawValue {
                return .init(
                    code: "app_attest_server_unavailable",
                    message: "Apple 暂时无法完成设备验证，请稍后重试。",
                    retryable: true
                )
            }
        }
        return .init(code: "network_error", message: "暂时没有连上 Murmur。", retryable: true)
    }

    static func fromHTTPStatus(_ statusCode: Int) -> Self {
        .init(
            code: "http_\(statusCode)",
            message: statusCode == 401 ? "设备验证已失效，请重新连接。" : "服务器暂时无法处理这个请求。",
            retryable: statusCode >= 500 || statusCode == 408 || statusCode == 429
        )
    }

    var requiresDeviceReconnect: Bool {
        code == "app_attest_invalid_key" || code == "attestation_key_unknown"
    }
}

/// What is left on screen after an outgoing turn failed: why it never landed,
/// and whether saying it again is on offer.
///
/// Distinct from `MurmurFailure`, which is an error in flight.  This one belongs
/// to a single transcript row and outlives the moment that produced it, so the
/// mark beside the bubble can explain itself without the app having to hold a
/// whole error somewhere global.
struct MurmurSendFailure: Equatable, Sendable {
    let message: String
    let canResend: Bool

    /// A row the app has no live reason for: still sending when the process was
    /// last killed, or failed in a session that has since ended.  The verdict
    /// survived in the transcript; the wording did not.
    ///
    /// The offer stands as long as the row still has something to send.  The
    /// transcript keeps the words and its own copy of the photo, so "the app
    /// was restarted" is no longer a reason to refuse — a screen of failed
    /// messages with no way to send any of them was the worst of the paths.
    static func interrupted(canResend: Bool) -> MurmurSendFailure {
        MurmurSendFailure(message: "这条没有发出去。", canResend: canResend)
    }
}

/// How fast Murmur's bubbles are allowed to land.
///
/// The server streams a whole reply the instant it is ready, which reads as a
/// machine emptying a buffer.  Holding each bubble back by roughly the time it
/// would take somebody to type it — measured from when the previous one landed,
/// so real server latency counts towards the wait rather than adding to it — is
/// what gives the rhythm a person's shape.
struct MurmurBubblePacing: Sendable, Equatable {
    var perCharacter: TimeInterval
    var minimum: TimeInterval
    var maximum: TimeInterval

    static let human = MurmurBubblePacing(perCharacter: 0.075, minimum: 0.7, maximum: 2.8)
    /// Tests and previews want the transcript, not the theatre.
    static let instant = MurmurBubblePacing(perCharacter: 0, minimum: 0, maximum: 0)

    func delay(for text: String) -> TimeInterval {
        guard perCharacter > 0 else { return 0 }
        return min(max(Double(text.count) * perCharacter, minimum), maximum)
    }
}

/// What a photo carries about itself: when it was taken, and where.
///
/// 当年今日 sends a re-encoded JPEG, and a re-encoded JPEG has no EXIF left —
/// without saying so explicitly, a photo from three years ago arrives looking
/// like it was taken a second ago, and the one screen whose whole job is to ask
/// about *that day* gets told the wrong day.  So the app states these facts
/// rather than leaving a parser downstream to infer them from an image that no
/// longer contains them.
///
/// `place` is reverse-geocoded on this device.  The coordinates still only ever
/// become an anonymised ~110m fingerprint on the server, which does no
/// geocoding of its own and keeps no latitude or longitude.
struct PhotoProvenance: Codable, Equatable, Sendable {
    var shotAt: Date?
    var latitude: Double?
    var longitude: Double?
    var place: String?

    enum CodingKeys: String, CodingKey {
        case shotAt = "shot_at"
        case latitude = "lat"
        case longitude = "lon"
        case place
    }

    /// Nothing worth saying.  An empty block is not sent at all rather than
    /// sent as `{}` — the server would have to decide what that meant.
    var isEmpty: Bool {
        shotAt == nil && latitude == nil && longitude == nil && place == nil
    }
}

struct PhotoAttachment: Identifiable, @unchecked Sendable {
    let id: UUID
    let originalURL: URL
    let preview: UIImage
    let filename: String
    let mimeType: String
    let byteCount: Int64
    /// Filled in after the encode rather than during it: the place name is
    /// resolved over the network alongside the encode, and whichever finishes
    /// first should not hold up the other.
    var provenance: PhotoProvenance?
}

struct MurmurBubble: Identifiable, Equatable, Sendable {
    let id: String
    let text: String
}

struct MurmurIdentity: Codable, Equatable, Sendable {
    let userID: String
    let deviceID: String
    let keyID: String

    enum CodingKeys: String, CodingKey {
        case userID = "user_id"
        case deviceID = "device_id"
        case keyID = "key_id"
    }
}

struct MurmurDevice: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let keyID: String
    let environment: String
    let timezone: String
    let deviceName: String?
    let pushEnabled: Bool
    let lastSeenAt: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case keyID = "key_id"
        case environment, timezone
        case deviceName = "device_name"
        case pushEnabled = "push_enabled"
        case lastSeenAt = "last_seen_at"
        case createdAt = "created_at"
    }
}

struct MurmurPreferences: Codable, Equatable, Sendable {
    var dailyFrequency: Int = 3
    var quietStart: String = "22:30"
    var quietEnd: String = "08:30"

    enum CodingKeys: String, CodingKey {
        case dailyFrequency = "daily_frequency"
        case quietStart = "quiet_start"
        case quietEnd = "quiet_end"
    }
}

struct MomentReceipt: Decodable, Sendable {
    let momentID: String
    let status: String

    enum CodingKeys: String, CodingKey {
        case momentID = "moment_id"
        case status
    }
}

struct ProactiveMoment: Decodable, Sendable {
    let momentID: String
    let bubbles: [String]
    let text: String?
    let move: String?
    let scene: String?

    enum CodingKeys: String, CodingKey {
        case momentID = "moment_id"
        case bubbles
        case text
        case move
        case scene
    }

    var resolvedBubbles: [String] {
        if !bubbles.isEmpty { return bubbles }
        if let text, !text.isEmpty { return [text] }
        return []
    }
}

/// What a moment is asking the server for, when it is asking for something
/// other than an ordinary reply.  `nil` — the chat's own case — is not a
/// member here: an ordinary moment carries no intent at all.
enum MurmurMomentIntent: String, Sendable {
    /// 当年今日's opening upload: one photo, no words.  The server reads the
    /// image and answers with a guess at what the person came to say, plus
    /// the three openers under it.
    case photoReading = "photo_reading"
}

enum MurmurStreamEvent: Equatable, Sendable {
    case accepted(id: String?)
    /// A normal bubble may carry one provider-neutral song snapshot.  This is
    /// deliberately still the existing durable bubble event: older payloads
    /// decode with no attachment and there is no parallel music event stream.
    case bubble(id: String?, text: String, musicTrack: MusicTrackAttachmentV1?)
    /// The three openers under a photo reading: another output of the same
    /// moment, not a new endpoint.  Short entry angles the person can pick up
    /// with one tap, and only 当年今日's room ever asks for them.
    case angles(id: String?, texts: [String])
    case quiet(id: String?)
    case done(id: String?, move: String?, scene: String?)
    case failure(id: String?, MurmurFailure)
}

extension MurmurStreamEvent {
    /// Source-compatible spelling for the many existing text-only producers.
    static func bubble(id: String?, text: String) -> Self {
        .bubble(id: id, text: text, musicTrack: nil)
    }
}

protocol MurmurAPIClient: Sendable {
    func storedIdentity() async throws -> MurmurIdentity?
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity
    func createMoment(
        note: String?,
        photo: PhotoAttachment?,
        idempotencyKey: String,
        intent: MurmurMomentIntent?,
        contextMomentIDs: [String]
    ) async throws -> MomentReceipt
    func createMoment(
        note: String?,
        photo: PhotoAttachment?,
        musicTrack: MusicTrackAttachmentV1?,
        idempotencyKey: String,
        intent: MurmurMomentIntent?,
        contextMomentIDs: [String]
    ) async throws -> MomentReceipt
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error>
    func currentProactive() async throws -> ProactiveMoment?
    func acknowledge(momentID: String, reply: String?) async throws
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws
    func devices() async throws -> [MurmurDevice]
    func removeDevice(deviceID: String) async throws
    func preferences() async throws -> MurmurPreferences
    func updatePreferences(_ preferences: MurmurPreferences) async throws
    func musicAvailability() async throws -> MusicFeatureAvailability
    func reportMusicPlayback(_ event: MusicPlaybackEvent) async throws
    func resolveSharedMusic(text: String, idempotencyKey: String) async throws -> MusicTrackAttachmentV1
    func createListenTogetherRoom(
        initialTrack: MusicTrackAttachmentV1,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1
    func currentListenTogetherRoom() async throws -> ListenTogetherRoomSnapshotV1?
    func commandListenTogetherRoom(
        handle: String,
        command: ListenTogetherCommand,
        track: MusicTrackAttachmentV1?,
        idempotencyKey: String
    ) async throws -> ListenTogetherCommandResultV1
    func closeListenTogetherRoom(
        handle: String,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1
    func resetLocalIdentity() async throws
    func deleteAccount() async throws
}

extension MurmurAPIClient {
    /// Old test doubles and offline clients stay safely feature-off until they
    /// opt into the authenticated server capability.
    func musicAvailability() async throws -> MusicFeatureAvailability {
        MusicFeatureAvailability(enabled: false, provider: "audius", playbackReporting: false)
    }

    func reportMusicPlayback(_ event: MusicPlaybackEvent) async throws {}

    func resolveSharedMusic(text: String, idempotencyKey: String) async throws -> MusicTrackAttachmentV1 {
        throw MurmurFailure(code: "netease_unavailable", message: "网易云音乐功能尚未开启。", retryable: false)
    }

    func createListenTogetherRoom(
        initialTrack: MusicTrackAttachmentV1,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1 {
        throw MurmurFailure(code: "listen_together_unavailable", message: "一起听功能尚未开启。", retryable: false)
    }

    func currentListenTogetherRoom() async throws -> ListenTogetherRoomSnapshotV1? { nil }

    func commandListenTogetherRoom(
        handle: String,
        command: ListenTogetherCommand,
        track: MusicTrackAttachmentV1?,
        idempotencyKey: String
    ) async throws -> ListenTogetherCommandResultV1 {
        throw MurmurFailure(code: "listen_together_unavailable", message: "一起听功能尚未开启。", retryable: false)
    }

    func closeListenTogetherRoom(
        handle: String,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1 {
        throw MurmurFailure(code: "listen_together_unavailable", message: "一起听功能尚未开启。", retryable: false)
    }

    /// Compatibility seam for clients and test doubles that predate song
    /// attachments.  Real transports override this requirement; old callers
    /// continue to use the original body byte-for-byte.
    func createMoment(
        note: String?,
        photo: PhotoAttachment?,
        musicTrack: MusicTrackAttachmentV1?,
        idempotencyKey: String,
        intent: MurmurMomentIntent?,
        contextMomentIDs: [String]
    ) async throws -> MomentReceipt {
        guard musicTrack == nil else {
            throw MurmurFailure(
                code: "music_unavailable",
                message: "这首歌暂时无法发送。",
                retryable: false
            )
        }
        return try await createMoment(
            note: note,
            photo: photo,
            idempotencyKey: idempotencyKey,
            intent: intent,
            contextMomentIDs: contextMomentIDs
        )
    }

    /// Ordinary chat and older call sites keep producing the exact same
    /// multipart body.  Only an archived-day continuation opts into context.
    func createMoment(
        note: String?,
        photo: PhotoAttachment?,
        idempotencyKey: String,
        intent: MurmurMomentIntent?
    ) async throws -> MomentReceipt {
        try await createMoment(
            note: note,
            photo: photo,
            idempotencyKey: idempotencyKey,
            intent: intent,
            contextMomentIDs: []
        )
    }

    func createMoment(
        note: String?,
        photo: PhotoAttachment?,
        musicTrack: MusicTrackAttachmentV1?,
        idempotencyKey: String,
        intent: MurmurMomentIntent?
    ) async throws -> MomentReceipt {
        try await createMoment(
            note: note,
            photo: photo,
            musicTrack: musicTrack,
            idempotencyKey: idempotencyKey,
            intent: intent,
            contextMomentIDs: []
        )
    }
}
