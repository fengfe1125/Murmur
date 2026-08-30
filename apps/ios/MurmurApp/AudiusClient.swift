import AuthenticationServices
import CryptoKit
import Foundation
import Security
import UIKit

enum AudiusClientError: Error, Equatable, Sendable {
    case notConfigured
    case loginCancelled
    case invalidAuthorizationResponse
    case stateMismatch
    case notAuthenticated
    case accountMismatch
    case unauthorized
    case rateLimited(retryAfter: TimeInterval?)
    case unavailable(statusCode: Int)
    case invalidResponse
    case keychain(status: OSStatus)
}

struct AudiusCredential: Codable, Equatable, Sendable {
    var accessToken: String
    var refreshToken: String
    var scope: String
    var expiresAt: Date?
    var audiusUserID: String?
    let ownerMurmurUserID: String
}

protocol AudiusCredentialStoring: Sendable {
    func load() async throws -> AudiusCredential?
    func save(_ credential: AudiusCredential) async throws
    func clear() async throws
}

actor AudiusKeychainCredentialStore: AudiusCredentialStoring {
    private let service: String
    private let account = "oauth"

    init(service: String = "com.sakura.Murmur.audius.oauth") {
        self.service = service
    }

    func load() throws -> AudiusCredential? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw AudiusClientError.keychain(status: status)
        }
        return try JSONDecoder().decode(AudiusCredential.self, from: data)
    }

    func save(_ credential: AudiusCredential) throws {
        let data = try JSONEncoder().encode(credential)
        let key: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus = SecItemUpdate(key as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var insert = key
            attributes.forEach { insert[$0.key] = $0.value }
            let addStatus = SecItemAdd(insert as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw AudiusClientError.keychain(status: addStatus)
            }
        } else if updateStatus != errSecSuccess {
            throw AudiusClientError.keychain(status: updateStatus)
        }
    }

    func clear() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw AudiusClientError.keychain(status: status)
        }
    }
}

protocol AudiusAuthorizing: Sendable {
    @MainActor
    func authorize(at url: URL, callbackScheme: String) async throws -> URL
}

final class AudiusWebAuthorizer: NSObject, AudiusAuthorizing,
    ASWebAuthenticationPresentationContextProviding, @unchecked Sendable {
    @MainActor
    private var session: ASWebAuthenticationSession?

    /// Conforming to the presentation-context protocol isolates this class to
    /// the main actor, which would otherwise make it unusable as a default
    /// argument.  Building one touches nothing isolated — only `authorize` and
    /// `presentationAnchor` do, and both say so themselves.
    nonisolated override init() { super.init() }

    @MainActor
    func authorize(at url: URL, callbackScheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: url,
                callbackURLScheme: callbackScheme
            ) { callbackURL, error in
                self.session = nil
                if let authenticationError = error as? ASWebAuthenticationSessionError,
                   authenticationError.code == .canceledLogin {
                    continuation.resume(throwing: AudiusClientError.loginCancelled)
                    return
                }
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: AudiusClientError.invalidAuthorizationResponse)
                    return
                }
                continuation.resume(returning: callbackURL)
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            self.session = session
            guard session.start() else {
                self.session = nil
                continuation.resume(throwing: AudiusClientError.invalidAuthorizationResponse)
                return
            }
        }
    }

    @MainActor
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow) ?? ASPresentationAnchor()
    }
}

actor AudiusClient {
    private let configuration: MusicFeatureConfiguration
    private let credentialStore: any AudiusCredentialStoring
    private let authorizer: any AudiusAuthorizing
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()
    private var refreshTask: Task<AudiusCredential, Error>?

    init(
        configuration: MusicFeatureConfiguration,
        credentialStore: any AudiusCredentialStoring = AudiusKeychainCredentialStore(),
        authorizer: any AudiusAuthorizing = AudiusWebAuthorizer(),
        session: URLSession? = nil
    ) {
        self.configuration = configuration
        self.credentialStore = credentialStore
        self.authorizer = authorizer
        if let session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.ephemeral
            config.httpCookieStorage = nil
            config.urlCache = nil
            config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
            self.session = URLSession(configuration: config)
        }
        self.encoder.dateEncodingStrategy = .iso8601
        self.decoder.dateDecodingStrategy = .iso8601
    }

    func login(ownerMurmurUserID: String) async throws -> AudiusUser {
        guard configuration.isConfigured, let callbackScheme = configuration.callbackScheme else {
            throw AudiusClientError.notConfigured
        }
        let pkce = try Self.makePKCE()
        let authorizationURL = try makeURL(
            path: "oauth/authorize",
            query: [
                .init(name: "response_type", value: "code"),
                .init(name: "scope", value: "read"),
                .init(name: "api_key", value: configuration.audiusAPIKey),
                .init(name: "redirect_uri", value: configuration.audiusRedirectURI.absoluteString),
                .init(name: "state", value: pkce.state),
                .init(name: "code_challenge", value: pkce.challenge),
                .init(name: "code_challenge_method", value: "S256"),
                .init(name: "response_mode", value: "query"),
                .init(name: "display", value: "fullScreen"),
            ]
        )

        // Audius documents custom schemes for mobile, while its manual-flow
        // validation paragraph currently says http/https only. This remains a
        // configurable Stage-0 integration point until verified in Audius's
        // developer console and on a real iPhone.
        let callback = try await authorizer.authorize(at: authorizationURL, callbackScheme: callbackScheme)
        let callbackItems = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
        guard callbackItems.first(where: { $0.name == "state" })?.value == pkce.state else {
            throw AudiusClientError.stateMismatch
        }
        guard let code = callbackItems.first(where: { $0.name == "code" })?.value, !code.isEmpty else {
            throw AudiusClientError.invalidAuthorizationResponse
        }

        let token = try await exchangeToken(
            .authorizationCode(
                code: code,
                verifier: pkce.verifier,
                redirectURI: configuration.audiusRedirectURI.absoluteString
            )
        )
        var credential = AudiusCredential(
            accessToken: token.accessToken,
            refreshToken: token.refreshToken,
            scope: token.scope ?? "read",
            expiresAt: token.expiresIn.map { Date().addingTimeInterval(TimeInterval($0)) },
            audiusUserID: nil,
            ownerMurmurUserID: ownerMurmurUserID
        )
        let user = try await fetchMe(accessToken: credential.accessToken)
        credential.audiusUserID = user.id
        try await credentialStore.save(credential)
        return user
    }

    func restore(ownerMurmurUserID: String) async throws -> AudiusUser? {
        guard var credential = try await credentialStore.load() else { return nil }
        guard credential.ownerMurmurUserID == ownerMurmurUserID else {
            try await credentialStore.clear()
            throw AudiusClientError.accountMismatch
        }
        if let expiresAt = credential.expiresAt, expiresAt <= Date().addingTimeInterval(60) {
            credential = try await refresh(credential)
        }
        do {
            let user = try await fetchMe(accessToken: credential.accessToken)
            if credential.audiusUserID != user.id {
                credential.audiusUserID = user.id
                try await credentialStore.save(credential)
            }
            return user
        } catch AudiusClientError.unauthorized {
            credential = try await refresh(credential)
            return try await fetchMe(accessToken: credential.accessToken)
        }
    }

    func logout() async {
        if let credential = try? await credentialStore.load() {
            try? await revoke(refreshToken: credential.refreshToken)
        }
        try? await credentialStore.clear()
    }

    func searchTracks(query: String, limit: Int = 20, offset: Int = 0) async throws -> [MusicTrackAttachmentV1] {
        let data = try await request(
            path: "tracks/search",
            query: publicQuery([
                .init(name: "query", value: query),
                .init(name: "limit", value: String(limit)),
                .init(name: "offset", value: String(offset)),
            ])
        )
        return try decodeTracks(data)
    }

    func favoriteTracks(limit: Int = 20, offset: Int = 0) async throws -> [MusicTrackAttachmentV1] {
        let credential = try await validCredential()
        guard let userID = credential.audiusUserID else { throw AudiusClientError.notAuthenticated }
        let data = try await authorizedRequest(
            path: "users/\(userID)/favorites/tracks",
            query: paginationQuery(limit: limit, offset: offset),
            credential: credential
        )
        return try decodeTracks(data)
    }

    func libraryTracks(limit: Int = 20, offset: Int = 0) async throws -> [MusicTrackAttachmentV1] {
        let credential = try await validCredential()
        guard let userID = credential.audiusUserID else { throw AudiusClientError.notAuthenticated }
        let data = try await authorizedRequest(
            path: "users/\(userID)/library/tracks",
            query: paginationQuery(limit: limit, offset: offset),
            credential: credential
        )
        return try decodeTracks(data)
    }

    func playlists(limit: Int = 20, offset: Int = 0) async throws -> [AudiusPlaylist] {
        let credential = try await validCredential()
        guard let userID = credential.audiusUserID else { throw AudiusClientError.notAuthenticated }
        let data = try await authorizedRequest(
            path: "users/\(userID)/library/playlists",
            query: paginationQuery(limit: limit, offset: offset),
            credential: credential
        )
        return try decoder.decode(AudiusEnvelope<[AudiusPlaylistDTO]>.self, from: data).data.map(\.model)
    }

    func playlistTracks(playlistID: String, limit: Int = 20, offset: Int = 0) async throws -> [MusicTrackAttachmentV1] {
        let credential = try await validCredential()
        let data = try await authorizedRequest(
            path: "playlists/\(playlistID)/tracks",
            query: paginationQuery(limit: limit, offset: offset),
            credential: credential
        )
        return try decodeTracks(data)
    }

    func track(trackID: String) async throws -> MusicTrackAttachmentV1 {
        let data = try await request(path: "tracks/\(trackID)", query: publicQuery([]))
        return try decoder.decode(AudiusEnvelope<AudiusTrackDTO>.self, from: data).data.model
    }

    struct PlayableStream: Equatable, Sendable {
        let track: MusicTrackAttachmentV1
        let url: URL
    }

    /// Revalidates metadata and the provider's access decision immediately
    /// before constructing a one-use stream endpoint. The endpoint may redirect
    /// inside AVPlayer; neither this value nor the redirected CDN URL is stored.
    func resolvePlayableStream(trackID: String) async throws -> PlayableStream {
        let freshTrack = try await track(trackID: trackID)
        let accessData = try await request(
            path: "tracks/\(trackID)/access-info",
            query: publicQuery([])
        )
        let access = try decoder.decode(AudiusEnvelope<AudiusTrackAccessInfoDTO>.self, from: accessData).data
        guard access.isPubliclyStreamable else {
            throw AudiusClientError.unavailable(statusCode: 403)
        }
        return PlayableStream(track: freshTrack, url: try streamURL(trackID: trackID))
    }

    /// Returns the official Audius stream endpoint, not its redirected media
    /// URL. The value is created on demand and must never be persisted.
    func streamURL(trackID: String) throws -> URL {
        try makeURL(path: "tracks/\(trackID)/stream", query: publicQuery([]))
    }

    private func validCredential() async throws -> AudiusCredential {
        guard var credential = try await credentialStore.load() else {
            throw AudiusClientError.notAuthenticated
        }
        if let expiresAt = credential.expiresAt, expiresAt <= Date().addingTimeInterval(60) {
            credential = try await refresh(credential)
        }
        return credential
    }

    private func authorizedRequest(
        path: String,
        query: [URLQueryItem],
        credential: AudiusCredential
    ) async throws -> Data {
        do {
            return try await request(path: path, query: query, bearerToken: credential.accessToken)
        } catch AudiusClientError.unauthorized {
            let refreshed = try await refresh(credential)
            return try await request(path: path, query: query, bearerToken: refreshed.accessToken)
        }
    }

    private func refresh(_ credential: AudiusCredential) async throws -> AudiusCredential {
        if let current = try await credentialStore.load(), current.accessToken != credential.accessToken {
            return current
        }
        if let refreshTask { return try await refreshTask.value }
        let task = Task { [weak self] in
            guard let self else { throw AudiusClientError.notAuthenticated }
            return try await self.performRefresh(credential)
        }
        refreshTask = task
        defer { refreshTask = nil }
        return try await task.value
    }

    private func performRefresh(_ credential: AudiusCredential) async throws -> AudiusCredential {
        do {
            let token = try await exchangeToken(.refreshToken(credential.refreshToken))
            var updated = credential
            updated.accessToken = token.accessToken
            updated.refreshToken = token.refreshToken
            updated.scope = token.scope ?? credential.scope
            updated.expiresAt = token.expiresIn.map { Date().addingTimeInterval(TimeInterval($0)) }
            try await credentialStore.save(updated)
            return updated
        } catch {
            try? await credentialStore.clear()
            throw AudiusClientError.notAuthenticated
        }
    }

    private func fetchMe(accessToken: String) async throws -> AudiusUser {
        let data = try await request(path: "me", query: [], bearerToken: accessToken)
        let dto = try decoder.decode(AudiusMeDTO.self, from: data)
        return dto.model
    }

    private enum TokenGrant {
        case authorizationCode(code: String, verifier: String, redirectURI: String)
        case refreshToken(String)
    }

    private func exchangeToken(_ grant: TokenGrant) async throws -> AudiusTokenResponse {
        let body: Data
        switch grant {
        case let .authorizationCode(code, verifier, redirectURI):
            body = try JSONSerialization.data(withJSONObject: [
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": configuration.audiusAPIKey,
                "redirect_uri": redirectURI,
            ])
        case let .refreshToken(refreshToken):
            body = try JSONSerialization.data(withJSONObject: [
                "grant_type": "refresh_token",
                "refresh_token": refreshToken,
                "client_id": configuration.audiusAPIKey,
            ])
        }
        let data = try await request(path: "oauth/token", method: "POST", body: body)
        return try decoder.decode(AudiusTokenResponse.self, from: data)
    }

    private func revoke(refreshToken: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: [
            "token": refreshToken,
            "client_id": configuration.audiusAPIKey,
        ])
        _ = try await request(path: "oauth/revoke", method: "POST", body: body)
    }

    private func request(
        path: String,
        method: String = "GET",
        query: [URLQueryItem] = [],
        body: Data? = nil,
        bearerToken: String? = nil
    ) async throws -> Data {
        var request = URLRequest(url: try makeURL(path: path, query: query))
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let bearerToken { request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization") }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw AudiusClientError.invalidResponse }
        switch http.statusCode {
        case 200..<300:
            return data
        case 401:
            throw AudiusClientError.unauthorized
        case 429:
            throw AudiusClientError.rateLimited(
                retryAfter: http.value(forHTTPHeaderField: "Retry-After").flatMap(TimeInterval.init)
            )
        default:
            throw AudiusClientError.unavailable(statusCode: http.statusCode)
        }
    }

    private func makeURL(path: String, query: [URLQueryItem]) throws -> URL {
        let base = configuration.audiusAPIBaseURL.appending(path: path)
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            throw AudiusClientError.notConfigured
        }
        if !query.isEmpty { components.queryItems = query }
        guard let url = components.url else { throw AudiusClientError.notConfigured }
        return url
    }

    private func publicQuery(_ items: [URLQueryItem]) -> [URLQueryItem] {
        items + [.init(name: "api_key", value: configuration.audiusAPIKey)]
    }

    private func paginationQuery(limit: Int, offset: Int) -> [URLQueryItem] {
        publicQuery([
            .init(name: "limit", value: String(limit)),
            .init(name: "offset", value: String(offset)),
        ])
    }

    private func decodeTracks(_ data: Data) throws -> [MusicTrackAttachmentV1] {
        try decoder.decode(AudiusEnvelope<[AudiusTrackDTO]>.self, from: data).data.map(\.model)
    }

    private struct PKCE {
        let verifier: String
        let challenge: String
        let state: String
    }

    private static func makePKCE() throws -> PKCE {
        var verifierBytes = [UInt8](repeating: 0, count: 32)
        var stateBytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, verifierBytes.count, &verifierBytes) == errSecSuccess,
              SecRandomCopyBytes(kSecRandomDefault, stateBytes.count, &stateBytes) == errSecSuccess
        else { throw AudiusClientError.invalidAuthorizationResponse }
        let verifier = Data(verifierBytes).base64URL
        let challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
        return PKCE(verifier: verifier, challenge: challenge, state: Data(stateBytes).base64URL)
    }
}

private struct AudiusEnvelope<Value: Decodable>: Decodable {
    let data: Value
}

private struct AudiusTokenResponse: Decodable {
    let accessToken: String
    let refreshToken: String
    let expiresIn: Int?
    let scope: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case expiresIn = "expires_in"
        case scope
    }
}

/// The endpoint is present in the current official OpenAPI, but Audius's
/// public documentation does not yet spell out every access variant. These
/// accepted keys are intentionally conservative and must be confirmed against
/// a registered app during Stage 0; an unknown shape fails closed.
private struct AudiusTrackAccessInfoDTO: Decodable {
    let isPubliclyStreamable: Bool

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: DynamicCodingKey.self)
        let direct = try container.decodeIfPresent(Bool.self, forKey: .init("is_streamable"))
            ?? container.decodeIfPresent(Bool.self, forKey: .init("streamable"))
        if let direct {
            isPubliclyStreamable = direct
            return
        }
        if let access = try container.decodeIfPresent(
            [String: Bool].self,
            forKey: .init("access")
        ) {
            isPubliclyStreamable = access["stream"] ?? access["streamable"] ?? false
            return
        }
        isPubliclyStreamable = false
    }
}

private struct DynamicCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int? = nil

    init(_ stringValue: String) { self.stringValue = stringValue }
    init?(stringValue: String) { self.init(stringValue) }
    init?(intValue: Int) { return nil }
}

private struct AudiusMeDTO: Decodable {
    let userID: FlexibleID
    let name: String
    let handle: String
    let verified: Bool?
    let profilePicture: AudiusImageSet?

    enum CodingKeys: String, CodingKey {
        case userID
        case name, handle, verified
        case profilePicture
    }

    var model: AudiusUser {
        AudiusUser(
            id: userID.value,
            name: name,
            handle: handle,
            profilePictureURL: profilePicture?.preferred,
            verified: verified ?? false
        )
    }
}

private struct AudiusTrackDTO: Decodable {
    let id: FlexibleID
    let title: String
    let duration: Int?
    let explicit: Bool?
    let permalink: URL?
    let permalinkURL: URL?
    let artwork: AudiusImageSet?
    let user: AudiusTrackUserDTO?

    enum CodingKeys: String, CodingKey {
        case id, title, duration, explicit, permalink, artwork, user
        case permalinkURL = "permalink_url"
    }

    var model: MusicTrackAttachmentV1 {
        let artist = user?.name ?? user?.handle ?? "Audius"
        let canonical = permalinkURL ?? permalink
            ?? URL(string: "https://audius.co/tracks/\(id.value)")!
        return MusicTrackAttachmentV1(
            trackID: id.value,
            title: title,
            artists: [artist],
            artworkURL: artwork?.preferred,
            canonicalURL: canonical,
            durationSeconds: duration,
            explicit: explicit
        )
    }
}

private struct AudiusTrackUserDTO: Decodable {
    let name: String?
    let handle: String?
}

private struct AudiusPlaylistDTO: Decodable {
    let id: FlexibleID
    let name: String
    let artwork: AudiusImageSet?
    let trackCount: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case name = "playlistName"
        case artwork
        case trackCount
    }

    var model: AudiusPlaylist {
        AudiusPlaylist(id: id.value, name: name, artworkURL: artwork?.preferred, trackCount: trackCount)
    }
}

private struct AudiusImageSet: Decodable {
    let small: URL?
    let medium: URL?
    let large: URL?

    enum CodingKeys: String, CodingKey {
        case small = "150x150"
        case medium = "480x480"
        case large = "1000x1000"
    }

    var preferred: URL? { medium ?? large ?? small }
}

private struct FlexibleID: Decodable {
    let value: String

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let string = try? container.decode(String.self) {
            value = string
        } else if let integer = try? container.decode(Int.self) {
            value = String(integer)
        } else {
            throw DecodingError.typeMismatch(
                String.self,
                .init(codingPath: decoder.codingPath, debugDescription: "Expected a string or integer ID")
            )
        }
    }
}

private extension Data {
    var base64URL: String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
