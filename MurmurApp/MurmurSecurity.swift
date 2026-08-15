@preconcurrency import DeviceCheck
import CryptoKit
import Foundation
import Security

struct AppAttestChallenge: Decodable, Sendable {
    let challengeID: String
    let challenge: String
    let expiresAt: String?

    enum CodingKeys: String, CodingKey {
        case challengeID = "challenge_id"
        case challenge
        case expiresAt = "expires_at"
    }

    var bytes: Data? { Data(base64URL: challenge) }
}

protocol MurmurAuthenticator: Sendable {
    var environment: String { get }
    func publicHeaders() async -> [String: String]
    func storedIdentity() async throws -> MurmurIdentity?
    func pendingEnrollmentKeyID() async throws -> String?
    func enrollmentKeyID() async throws -> String
    func enrollmentAttestation(for challenge: AppAttestChallenge, keyID: String) async throws -> String
    func assertion(
        for challenge: AppAttestChallenge,
        method: String,
        path: String,
        bodyDigest: Data,
        keyID: String
    ) async throws -> String
    func completeEnrollment(_ identity: MurmurIdentity) async throws
    func discardPendingEnrollmentKey() async throws
    func clearIdentity() async throws
}

actor KeychainIdentityStore {
    private let service: String
    private let identityAccount = "identity"
    private let pendingKeyAccount = "pending-app-attest-key"

    init(service: String = "com.sakura.Murmur.identity") {
        self.service = service
    }

    func loadIdentity() throws -> MurmurIdentity? {
        guard let data = try load(account: identityAccount) else { return nil }
        return try JSONDecoder().decode(MurmurIdentity.self, from: data)
    }

    func saveIdentity(_ identity: MurmurIdentity) throws {
        try save(JSONEncoder().encode(identity), account: identityAccount)
        try save(Data(identity.keyID.utf8), account: pendingKeyAccount)
    }

    func loadPendingKeyID() throws -> String? {
        guard let data = try load(account: pendingKeyAccount) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func savePendingKeyID(_ keyID: String) throws {
        try save(Data(keyID.utf8), account: pendingKeyAccount)
    }

    func clear() throws {
        try delete(account: identityAccount)
        try delete(account: pendingKeyAccount)
    }

    func clearPendingKeyID() throws {
        try delete(account: pendingKeyAccount)
    }

    private func load(account: String) throws -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw MurmurFailure(code: "keychain_read_failed", message: "无法读取设备身份。", retryable: true)
        }
        return data
    }

    private func save(_ data: Data, account: String) throws {
        let key: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]
        let update = SecItemUpdate(key as CFDictionary, attributes as CFDictionary)
        if update == errSecItemNotFound {
            var insert = key
            attributes.forEach { insert[$0.key] = $0.value }
            let status = SecItemAdd(insert as CFDictionary, nil)
            guard status == errSecSuccess else {
                throw MurmurFailure(code: "keychain_write_failed", message: "无法保存设备身份。", retryable: true)
            }
        } else if update != errSecSuccess {
            throw MurmurFailure(code: "keychain_write_failed", message: "无法保存设备身份。", retryable: true)
        }
    }

    private func delete(account: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw MurmurFailure(code: "keychain_delete_failed", message: "无法清除设备身份。", retryable: true)
        }
    }
}

actor AppAttestAuthenticator: MurmurAuthenticator {
    nonisolated let environment: String
    private let service = DCAppAttestService.shared
    private let store: KeychainIdentityStore

    init(store: KeychainIdentityStore = KeychainIdentityStore(), environment: String) {
        self.store = store
        self.environment = environment
    }

    func publicHeaders() -> [String: String] { [:] }

    func storedIdentity() async throws -> MurmurIdentity? {
        try await store.loadIdentity()
    }

    func pendingEnrollmentKeyID() async throws -> String? {
        try await store.loadPendingKeyID()
    }

    func enrollmentKeyID() async throws -> String {
        guard service.isSupported else {
            throw MurmurFailure(
                code: "app_attest_unsupported",
                message: "这台设备不支持 Murmur 所需的安全验证。",
                retryable: false
            )
        }
        if let existing = try await store.loadPendingKeyID() {
            return existing
        }
        let keyID = try await service.generateKey()
        try await store.savePendingKeyID(keyID)
        return keyID
    }

    func enrollmentAttestation(for challenge: AppAttestChallenge, keyID: String) async throws -> String {
        guard let challengeBytes = challenge.bytes else {
            throw MurmurFailure(code: "invalid_challenge", message: "服务器返回了无效挑战。", retryable: true)
        }
        let clientDataHash = Data(SHA256.hash(data: challengeBytes))
        let attestation = try await service.attestKey(keyID, clientDataHash: clientDataHash)
        return attestation.base64URLEncodedString()
    }

    func assertion(
        for challenge: AppAttestChallenge,
        method: String,
        path: String,
        bodyDigest: Data,
        keyID: String
    ) async throws -> String {
        guard service.isSupported, let challengeBytes = challenge.bytes else {
            throw MurmurFailure(code: "app_attest_unsupported", message: "无法验证这台设备。", retryable: false)
        }
        var clientData = Data()
        clientData.append(challengeBytes)
        clientData.append(Data(method.uppercased().utf8))
        clientData.append(Data(path.utf8))
        clientData.append(bodyDigest)
        let clientDataHash = Data(SHA256.hash(data: clientData))
        let assertion = try await service.generateAssertion(keyID, clientDataHash: clientDataHash)
        return assertion.base64URLEncodedString()
    }

    func completeEnrollment(_ identity: MurmurIdentity) async throws {
        try await store.saveIdentity(identity)
    }

    func discardPendingEnrollmentKey() async throws {
        try await store.clearPendingKeyID()
    }

    func clearIdentity() async throws {
        try await store.clear()
    }
}

#if DEBUG && targetEnvironment(simulator)
enum DevelopmentIdentity {
    static func makeKeyID() -> String {
        "dev-\(UUID().uuidString.lowercased())"
    }
}

actor DevelopmentAuthenticator: MurmurAuthenticator {
    nonisolated let environment = "development"
    private let token: String
    private let store: KeychainIdentityStore

    init(token: String, store: KeychainIdentityStore = KeychainIdentityStore()) {
        self.token = token
        self.store = store
    }

    func publicHeaders() -> [String: String] {
        ["X-Murmur-Development-Token": token]
    }

    func storedIdentity() async throws -> MurmurIdentity? {
        try await store.loadIdentity()
    }

    func pendingEnrollmentKeyID() async throws -> String? {
        try await store.loadPendingKeyID()
    }

    func enrollmentKeyID() async throws -> String {
        if let existing = try await store.loadPendingKeyID(), existing.hasPrefix("dev-") { return existing }
        let keyID = DevelopmentIdentity.makeKeyID()
        try await store.savePendingKeyID(keyID)
        return keyID
    }

    func enrollmentAttestation(for challenge: AppAttestChallenge, keyID: String) -> String {
        Data("development:\(challenge.challengeID):\(keyID)".utf8).base64URLEncodedString()
    }

    func assertion(
        for challenge: AppAttestChallenge,
        method: String,
        path: String,
        bodyDigest: Data,
        keyID: String
    ) -> String {
        var value = Data(challenge.challenge.utf8)
        value.append(Data(method.uppercased().utf8))
        value.append(Data(path.utf8))
        value.append(bodyDigest)
        value.append(Data(token.utf8))
        return Data(SHA256.hash(data: value)).base64URLEncodedString()
    }

    func completeEnrollment(_ identity: MurmurIdentity) async throws {
        try await store.saveIdentity(identity)
    }

    func discardPendingEnrollmentKey() async throws {
        try await store.clearPendingKeyID()
    }

    func clearIdentity() async throws {
        try await store.clear()
    }
}
#endif

extension Data {
    init?(base64URL: String) {
        var value = base64URL.replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        value += String(repeating: "=", count: (4 - value.count % 4) % 4)
        self.init(base64Encoded: value)
    }

    func base64URLEncodedString() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
