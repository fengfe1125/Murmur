import Foundation

@MainActor
final class AudiusAccountModel: ObservableObject {
    enum State: Equatable {
        case unavailable
        case signedOut
        case authorizing
        case signedIn(AudiusUser)
        case needsReconnect
        case failed(String)
    }

    @Published private(set) var state: State
    private let client: AudiusClient?
    private var ownerMurmurUserID: String?

    init(client: AudiusClient?) {
        self.client = client
        self.state = client == nil ? .unavailable : .signedOut
    }

    var user: AudiusUser? {
        guard case let .signedIn(user) = state else { return nil }
        return user
    }

    var isSignedIn: Bool { user != nil }

    func restore(ownerMurmurUserID: String) async {
        guard let client else {
            state = .unavailable
            return
        }
        self.ownerMurmurUserID = ownerMurmurUserID
        do {
            state = try await client.restore(ownerMurmurUserID: ownerMurmurUserID)
                .map(State.signedIn) ?? .signedOut
        } catch AudiusClientError.accountMismatch {
            state = .signedOut
        } catch AudiusClientError.notAuthenticated {
            state = .needsReconnect
        } catch {
            state = .failed(Self.message(for: error))
        }
    }

    func login() async {
        guard let client else {
            state = .unavailable
            return
        }
        guard let ownerMurmurUserID else {
            state = .failed("请先连接 Murmur 账号。")
            return
        }
        let previous = state
        state = .authorizing
        do {
            state = .signedIn(try await client.login(ownerMurmurUserID: ownerMurmurUserID))
        } catch AudiusClientError.loginCancelled {
            state = previous == .authorizing ? .signedOut : previous
        } catch {
            state = .failed(Self.message(for: error))
        }
    }

    func logout() async {
        await client?.logout()
        state = client == nil ? .unavailable : .signedOut
    }

    /// Used when the Murmur identity is removed or changes. Audius credentials
    /// are device-local but must not leak from one invited Murmur user to the
    /// next person enrolling on the same phone.
    func clearForMurmurIdentityChange() async {
        ownerMurmurUserID = nil
        await client?.logout()
        state = client == nil ? .unavailable : .signedOut
    }

    private static func message(for error: Error) -> String {
        switch error {
        case AudiusClientError.rateLimited:
            "Audius 请求太频繁，请稍后再试。"
        case AudiusClientError.notConfigured:
            "这个版本还没有配置 Audius。"
        case AudiusClientError.stateMismatch, AudiusClientError.invalidAuthorizationResponse:
            "Audius 登录没有安全完成，请重新连接。"
        default:
            "暂时没有连上 Audius。"
        }
    }
}
