import Foundation
import Metal

/// The dissolve shader's availability, proven before any gesture can reach it.
///
/// `ShaderLibrary.default` resolves functions by name at draw time, so a
/// .metal file that never made Compile Sources — or a misspelled function
/// name — compiles clean and crashes mid-swipe.  Reading the default Metal
/// library's symbol table up front demotes a missing shader to a plain fade
/// instead of a crash.
enum MurmurShaderSupport {
    static let particleDissolve: Bool = {
        guard let device = MTLCreateSystemDefaultDevice(),
              let library = try? device.makeDefaultLibrary(bundle: .main)
        else { return false }
        let found = library.functionNames.contains("onThisDayDissolve")
#if DEBUG
        if !found {
            Task { @MainActor in
                MurmurDiagnostics.record("onThisDayDissolve shader missing; send falls back to fade")
            }
        }
#endif
        return found
    }()
}

enum MurmurEnvironment {
    @MainActor
    static func makeAPIClient() -> any MurmurAPIClient {
#if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--murmur-ui-testing") {
            return UITestMurmurAPIClient()
        }
#endif
        let environment = ProcessInfo.processInfo.environment
        let bundleURL = Bundle.main.object(forInfoDictionaryKey: "MurmurAPIBaseURL") as? String
        let rawURL = environment["MURMUR_API_BASE_URL"] ?? bundleURL ?? defaultBaseURL
        guard let baseURL = URL(string: rawURL), !rawURL.isEmpty else {
            return UnavailableMurmurAPIClient(message: "尚未配置 Murmur 的 HTTPS 服务地址。")
        }
#if !DEBUG
        let bundleToken = Bundle.main.object(forInfoDictionaryKey: "MurmurDevelopmentToken") as? String
        let envToken = environment["MURMUR_DEV_BYPASS_TOKEN"]
        if [bundleToken, envToken].contains(where: { token in
            guard let token else { return false }
            return !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }) {
            fatalError("Release builds cannot include a Murmur development bypass.")
        }
        guard baseURL.scheme?.lowercased() == "https" else {
            return UnavailableMurmurAPIClient(message: "正式版只允许连接 HTTPS 服务。")
        }
#endif

        let authenticator: any MurmurAuthenticator
#if DEBUG
        // The bypass covers debug builds on a real device too, not just the
        // Simulator.  App Attest can only be verified against a paid team's
        // Team ID, so without one a device could not enrol at all.  The blast
        // radius stays at debug builds: the `#if !DEBUG` block above refuses
        // to launch a release build that carries a token, and release still
        // demands HTTPS.
        let bundleToken = Bundle.main.object(forInfoDictionaryKey: "MurmurDevelopmentToken") as? String
        if let token = environment["MURMUR_DEV_BYPASS_TOKEN"] ?? bundleToken, !token.isEmpty {
            authenticator = DevelopmentAuthenticator(token: token)
        } else {
            authenticator = AppAttestAuthenticator(environment: "development")
        }
#else
        authenticator = AppAttestAuthenticator(environment: "production")
#endif
        return URLSessionMurmurAPIClient(baseURL: baseURL, authenticator: authenticator)
    }

    /// UI tests run against a persisted transcript, so without somewhere to put
    /// it they inherit whatever the last test said.  Under the UI-testing flag
    /// the history goes to a directory of its own that a second flag empties,
    /// which lets one test still check that a moment survives a cold launch.
    @MainActor
    static func makeTranscriptStore() -> MurmurTranscriptStore {
#if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--murmur-ui-testing") {
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("murmur-ui-transcript", isDirectory: true)
            if arguments.contains("--murmur-reset-transcript") {
                try? FileManager.default.removeItem(at: directory)
            }
            return MurmurTranscriptStore(directory: directory)
        }
#endif
        return MurmurTranscriptStore()
    }

    /// 当年今日's archive, redirected the same way and under the same flags —
    /// a UI test that opened a room yesterday must not leave a mark on today's
    /// calendar for the next one to trip over.
    @MainActor
    static func makeArchive() -> MurmurArchive {
#if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--murmur-ui-testing") {
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("murmur-ui-archive", isDirectory: true)
            if arguments.contains("--murmur-reset-transcript") {
                try? FileManager.default.removeItem(at: directory)
            }
            let seedDaysAgo: Int?
            if arguments.contains("--murmur-seed-historical-archive") {
                seedDaysAgo = 2
            } else if arguments.contains("--murmur-seed-today-archive") {
                seedDaysAgo = 0
            } else {
                seedDaysAgo = nil
            }
            if let seedDaysAgo {
                let calendar = Calendar.murmur
                let day = calendar.date(byAdding: .day, value: -seedDaysAgo, to: Date()) ?? Date()
                return MurmurArchive(
                    store: MurmurTranscriptStore(directory: directory),
                    initialRows: [
                        MurmurMessage(
                            author: .murmur,
                            text: "这是一条旧日期里的回答",
                            sentAt: day,
                            momentID: "ui-historical-seed"
                        )
                    ]
                )
            }
            return MurmurArchive(store: MurmurTranscriptStore(directory: directory))
        }
#endif
        return MurmurArchive()
    }

    private static var defaultBaseURL: String {
#if DEBUG
        "http://127.0.0.1:8766"
#else
        ""
#endif
    }
}

private actor UnavailableMurmurAPIClient: MurmurAPIClient {
    let message: String

    init(message: String) {
        self.message = message
    }

    func storedIdentity() async throws -> MurmurIdentity? { nil }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity { throw unavailable }
    func createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String, intent: MurmurMomentIntent?, contextMomentIDs: [String]) async throws -> MomentReceipt { throw unavailable }
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        AsyncThrowingStream { $0.finish(throwing: unavailable) }
    }
    func currentProactive() async throws -> ProactiveMoment? { throw unavailable }
    func acknowledge(momentID: String, reply: String?) async throws { throw unavailable }
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws { throw unavailable }
    func devices() async throws -> [MurmurDevice] { throw unavailable }
    func removeDevice(deviceID: String) async throws { throw unavailable }
    func preferences() async throws -> MurmurPreferences { throw unavailable }
    func updatePreferences(_ preferences: MurmurPreferences) async throws { throw unavailable }
    func resetLocalIdentity() async throws {}
    func deleteAccount() async throws { throw unavailable }

    private var unavailable: MurmurFailure {
        .init(code: "not_configured", message: message, retryable: false)
    }
}

#if DEBUG
private actor UITestMurmurAPIClient: MurmurAPIClient {
    private let identity = MurmurIdentity(userID: "ui-user", deviceID: "ui-device", keyID: "ui-key")
    /// Every moment needs its own id.  Handing the same one back twice gives
    /// two replies the same message id, and the transcript's `ForEach` then
    /// draws only the first of them.
    private var moments = 0
    /// Under `--murmur-fail-first-send` the first send of the run does not
    /// land and every one after it does.  That is what makes the failure mark
    /// reachable on screen, and testable as an offer rather than a dead end:
    /// press it and the same row goes through.
    private let failsFirstSend = ProcessInfo.processInfo.arguments.contains("--murmur-fail-first-send")
    private var sends = 0
    /// Which moments came in as 当年今日 readings, so their event stream can
    /// answer with a guess and three openers instead of an ordinary reply.
    private var readings: Set<String> = []
    private var notesByMoment: [String: String] = [:]
    /// Under `--murmur-stub-reading-fails` the room's opening upload never
    /// lands.  That is what makes the room's own failure state reachable.
    private let failsReading = ProcessInfo.processInfo.arguments.contains("--murmur-stub-reading-fails")

    func storedIdentity() async throws -> MurmurIdentity? { identity }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity { identity }
    func createMoment(
        note: String?, photo: PhotoAttachment?, idempotencyKey: String,
        intent: MurmurMomentIntent?, contextMomentIDs: [String]
    ) async throws -> MomentReceipt {
        sends += 1
        if failsFirstSend, sends == 1 {
            throw MurmurFailure(code: "network_error", message: "暂时没有连上 Murmur。", retryable: true)
        }
        if intent == .photoReading, failsReading {
            throw MurmurFailure(code: "network_error", message: "暂时没有连上 Murmur。", retryable: true)
        }
        moments += 1
        let momentID = "ui-moment-\(moments)"
        if intent == .photoReading { readings.insert(momentID) }
        if let note { notesByMoment[momentID] = note }
        return .init(momentID: momentID, status: "queued")
    }
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        let isReading = readings.contains(momentID)
        let note = notesByMoment[momentID]
        return AsyncThrowingStream { continuation in
            // Production sequence numbers restart for every moment.  Keeping
            // that wire shape in UI tests guards the room-wide SwiftUI IDs.
            continuation.yield(.accepted(id: "1"))
            if isReading {
                continuation.yield(.bubble(id: "2", text: "这是……刚下过雨？"))
                continuation.yield(.angles(
                    id: "3",
                    texts: ["那天的天气", "右边那个人", "上次说要再来"]
                ))
            } else {
                let answer: String
                switch note {
                case "日期续聊第一句": answer = "接住第一句"
                case "日期续聊第二句": answer = "接住第二句"
                default: answer = "这一刻，我收到了。"
                }
                continuation.yield(.bubble(id: "2", text: answer))
            }
            continuation.yield(.done(id: "4", move: nil, scene: nil))
            continuation.finish()
        }
    }
    func currentProactive() async throws -> ProactiveMoment? { nil }
    func acknowledge(momentID: String, reply: String?) async throws {}
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws {}
    func devices() async throws -> [MurmurDevice] { [] }
    func removeDevice(deviceID: String) async throws {}
    func preferences() async throws -> MurmurPreferences { MurmurPreferences() }
    func updatePreferences(_ preferences: MurmurPreferences) async throws {}
    func resetLocalIdentity() async throws {}
    func deleteAccount() async throws {}
}
#endif
