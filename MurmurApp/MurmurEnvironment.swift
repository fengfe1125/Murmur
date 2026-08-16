import Foundation

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
    func createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String) async throws -> MomentReceipt { throw unavailable }
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

    func storedIdentity() async throws -> MurmurIdentity? { identity }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity { identity }
    func createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String) async throws -> MomentReceipt {
        moments += 1
        return .init(momentID: "ui-moment-\(moments)", status: "queued")
    }
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            continuation.yield(.accepted(id: "\(momentID)-1"))
            continuation.yield(.bubble(id: "\(momentID)-2", text: "这一刻，我收到了。"))
            continuation.yield(.done(id: "\(momentID)-3", move: nil, scene: nil))
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
