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

    /// A row that was still sending when the app was last killed.  The verdict
    /// survived in the transcript; the reason and the submission did not.
    static let interrupted = MurmurSendFailure(message: "这条没有发出去。", canResend: false)
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

struct PhotoAttachment: Identifiable, @unchecked Sendable {
    let id: UUID
    let originalURL: URL
    let preview: UIImage
    let filename: String
    let mimeType: String
    let byteCount: Int64
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

enum MurmurStreamEvent: Equatable, Sendable {
    case accepted(id: String?)
    case bubble(id: String?, text: String)
    case quiet(id: String?)
    case done(id: String?, move: String?, scene: String?)
    case failure(id: String?, MurmurFailure)
}

protocol MurmurAPIClient: Sendable {
    func storedIdentity() async throws -> MurmurIdentity?
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity
    func createMoment(
        note: String?,
        photo: PhotoAttachment?,
        idempotencyKey: String
    ) async throws -> MomentReceipt
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error>
    func currentProactive() async throws -> ProactiveMoment?
    func acknowledge(momentID: String, reply: String?) async throws
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws
    func devices() async throws -> [MurmurDevice]
    func removeDevice(deviceID: String) async throws
    func preferences() async throws -> MurmurPreferences
    func updatePreferences(_ preferences: MurmurPreferences) async throws
    func resetLocalIdentity() async throws
    func deleteAccount() async throws
}
