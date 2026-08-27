import CryptoKit
import Foundation
import XCTest
@testable import Murmur

#if DEBUG && targetEnvironment(simulator)
final class MurmurSecurityContractTests: XCTestCase {
    func testDevelopmentKeyUsesServerContractPrefix() async throws {
        let keyID = DevelopmentIdentity.makeKeyID()
        XCTAssertTrue(keyID.hasPrefix("dev-"))
    }

    func testProtectedRequestGateSerializesAssertionHandshakes() async throws {
        let gate = ProtectedRequestGate()
        let recorder = GateRecorder()
        await gate.acquire()
        await recorder.append("first-acquired")

        let second = Task {
            await gate.acquire()
            await recorder.append("second-acquired")
            await gate.release()
        }
        try await Task.sleep(for: .milliseconds(40))
        let beforeRelease = await recorder.values
        XCTAssertEqual(beforeRelease, ["first-acquired"])

        await gate.release()
        await second.value
        let afterRelease = await recorder.values
        XCTAssertEqual(afterRelease, ["first-acquired", "second-acquired"])
    }
}

private actor GateRecorder {
    private(set) var values: [String] = []
    func append(_ value: String) { values.append(value) }
}
#endif

final class MurmurNotificationPolicyTests: XCTestCase {
    func testNotDeterminedNeverSendsAPNSToken() {
        XCTAssertNil(MurmurNotificationBridge.serverToken("secret-token", authorization: .notDetermined))
    }

    func testDeniedNeverSendsAPNSToken() {
        XCTAssertNil(MurmurNotificationBridge.serverToken("secret-token", authorization: .denied))
    }

    func testAllowedSendsLatestAPNSToken() {
        XCTAssertEqual(
            MurmurNotificationBridge.serverToken("latest-token", authorization: .allowed),
            "latest-token"
        )
    }

    func testDeniedPayloadExplicitlyClearsServerTokenWithNull() throws {
        let payload = DeviceRequest(
            apnsToken: MurmurNotificationBridge.serverToken("secret-token", authorization: .denied),
            environment: "development",
            timezone: "Asia/Shanghai",
            deviceName: "iPhone · iOS 18"
        )

        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as? [String: Any])
        XCTAssertTrue(object.keys.contains("apns_token"))
        XCTAssertTrue(object["apns_token"] is NSNull)
    }
}

final class MurmurMomentMultipartTests: XCTestCase {
    func testArchiveContextIsEncodedWhileOrdinaryMomentsKeepTheOldBody() async throws {
        let recorder = RecoveryRequestRecorder()
        RecoveryURLProtocol.handler = { request in
            recorder.record(request)
            let url = try XCTUnwrap(request.url)
            let response = try XCTUnwrap(HTTPURLResponse(
                url: url,
                statusCode: url.path == "/v1/moments" ? 202 : 200,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            ))
            if url.path == "/v1/auth/challenges" {
                return (response, Data(#"{"challenge_id":"moment-challenge","challenge":"Y2hhbGxlbmdl","expires_at":null}"#.utf8))
            }
            return (response, Data(#"{"moment_id":"moment-response","status":"queued"}"#.utf8))
        }
        defer { RecoveryURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RecoveryURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = URLSessionMurmurAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://murmur.test")),
            authenticator: MomentAuthenticator(),
            session: session
        )

        _ = try await client.createMoment(
            note: "接着说",
            photo: nil,
            idempotencyKey: "context-key",
            intent: nil,
            contextMomentIDs: ["older", "latest"]
        )
        _ = try await client.createMoment(
            note: "普通聊天",
            photo: nil,
            idempotencyKey: "ordinary-key",
            intent: nil
        )

        let momentRequests = recorder.requests.filter { $0.url.path == "/v1/moments" }
        XCTAssertEqual(momentRequests.count, 2)
        let contextBody = String(
            data: try XCTUnwrap(momentRequests[0].body), encoding: .utf8
        ) ?? ""
        let ordinaryBody = String(
            data: try XCTUnwrap(momentRequests[1].body), encoding: .utf8
        ) ?? ""
        XCTAssertTrue(contextBody.contains("name=\"context_moment_ids\""))
        XCTAssertTrue(contextBody.contains("[\"older\",\"latest\"]"))
        XCTAssertFalse(ordinaryBody.contains("context_moment_ids"))
    }
}

final class MurmurEnrollmentRecoveryTests: XCTestCase {
    func testLostEnrollmentResponseRecoversPendingIdentityWithoutAttestingAgain() async throws {
        let recorder = RecoveryRequestRecorder()
        RecoveryURLProtocol.handler = { request in
            recorder.record(request)
            let url = try XCTUnwrap(request.url)
            let response = try XCTUnwrap(HTTPURLResponse(
                url: url,
                statusCode: 200,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            ))
            switch url.path {
            case "/v1/auth/challenges":
                return (response, Data(#"{"challenge_id":"recover-challenge","challenge":"cmVjb3Zlcg","expires_at":"2026-08-14T13:00:00Z"}"#.utf8))
            case "/v1/enrollments/recover":
                return (response, Data(#"{"user_id":"recovered-user","device_id":"recovered-device","key_id":"pending-key"}"#.utf8))
            default:
                throw MurmurFailure(code: "unexpected_request", message: url.path, retryable: false)
            }
        }
        defer { RecoveryURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RecoveryURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let authenticator = RecoveryAuthenticator()
        let client = URLSessionMurmurAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://murmur.test")),
            authenticator: authenticator,
            session: session
        )

        let identity = try await client.enroll(inviteCode: "already-consumed", deviceName: "iPhone · iOS 18")

        XCTAssertEqual(identity, .init(userID: "recovered-user", deviceID: "recovered-device", keyID: "pending-key"))
        let saved = await authenticator.storedIdentity()
        XCTAssertEqual(saved, identity)
        let attestationCalls = await authenticator.attestationCalls
        XCTAssertEqual(attestationCalls, 0)
        let requests = recorder.requests
        XCTAssertEqual(requests.map(\.url.path), ["/v1/auth/challenges", "/v1/enrollments/recover"])
        let challengeBody = try XCTUnwrap(requests.first?.body)
        let challengeJSON = try XCTUnwrap(JSONSerialization.jsonObject(with: challengeBody) as? [String: String])
        XCTAssertEqual(challengeJSON["purpose"], "request")
        XCTAssertEqual(challengeJSON["key_id"], "pending-key")
        let recoveryRequest = try XCTUnwrap(requests.last)
        XCTAssertEqual(recoveryRequest.body, Data("{}".utf8))
        XCTAssertEqual(recoveryRequest.header("X-Murmur-Key-ID"), "pending-key")
        XCTAssertEqual(recoveryRequest.header("X-Murmur-Challenge-ID"), "recover-challenge")
        XCTAssertEqual(recoveryRequest.header("X-Murmur-Assertion"), "recover-assertion")
    }

    func testRecoveryServerFailureKeepsPendingKeyForSafeRetry() async throws {
        RecoveryURLProtocol.handler = { request in
            let url = try XCTUnwrap(request.url)
            let response = try XCTUnwrap(HTTPURLResponse(
                url: url,
                statusCode: 503,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            ))
            return (response, Data(#"{"error":{"code":"service_unavailable","message":"later","retryable":true}}"#.utf8))
        }
        defer { RecoveryURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RecoveryURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let authenticator = RecoveryAuthenticator()
        let client = URLSessionMurmurAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://murmur.test")),
            authenticator: authenticator,
            session: session
        )

        do {
            _ = try await client.enroll(inviteCode: "already-consumed", deviceName: "iPhone · iOS 18")
            XCTFail("Expected a retryable recovery failure.")
        } catch let failure as MurmurFailure {
            XCTAssertEqual(failure.code, "service_unavailable")
            XCTAssertTrue(failure.retryable)
        }
        let pending = await authenticator.pendingEnrollmentKeyID()
        let discardCalls = await authenticator.discardCalls
        XCTAssertEqual(pending, "pending-key")
        XCTAssertEqual(discardCalls, 0)
    }

    func testUnknownPendingKeyFallsBackToFreshAttestation() async throws {
        RecoveryURLProtocol.handler = { request in
            let url = try XCTUnwrap(request.url)
            if url.path == "/v1/auth/challenges" {
                let body = try requestBody(request)
                let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])
                if json["purpose"] == "request" {
                    let response = try XCTUnwrap(HTTPURLResponse(
                        url: url,
                        statusCode: 401,
                        httpVersion: "HTTP/1.1",
                        headerFields: ["Content-Type": "application/json"]
                    ))
                    return (response, Data(#"{"error":{"code":"attestation_key_unknown","message":"unknown","retryable":false}}"#.utf8))
                }
                let response = try XCTUnwrap(HTTPURLResponse(
                    url: url,
                    statusCode: 200,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["Content-Type": "application/json"]
                ))
                return (response, Data(#"{"challenge_id":"fresh-challenge","challenge":"ZnJlc2g","expires_at":null}"#.utf8))
            }
            guard url.path == "/v1/enrollments" else {
                throw MurmurFailure(code: "unexpected_request", message: url.path, retryable: false)
            }
            let response = try XCTUnwrap(HTTPURLResponse(
                url: url,
                statusCode: 201,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            ))
            return (response, Data(#"{"user_id":"fresh-user","device_id":"fresh-device","key_id":"fresh-key"}"#.utf8))
        }
        defer { RecoveryURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RecoveryURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let authenticator = RecoveryAuthenticator(allowsFreshEnrollment: true)
        let client = URLSessionMurmurAPIClient(
            baseURL: try XCTUnwrap(URL(string: "https://murmur.test")),
            authenticator: authenticator,
            session: session
        )

        let identity = try await client.enroll(inviteCode: "fresh-code", deviceName: "iPhone · iOS 18")

        XCTAssertEqual(identity.keyID, "fresh-key")
        let discardCalls = await authenticator.discardCalls
        let attestationCalls = await authenticator.attestationCalls
        XCTAssertEqual(discardCalls, 1)
        XCTAssertEqual(attestationCalls, 1)
    }
}

private actor RecoveryAuthenticator: MurmurAuthenticator {
    nonisolated let environment = "development"
    private var identity: MurmurIdentity?
    private var pendingKeyID: String? = "pending-key"
    private let allowsFreshEnrollment: Bool
    private(set) var attestationCalls = 0
    private(set) var discardCalls = 0

    init(allowsFreshEnrollment: Bool = false) {
        self.allowsFreshEnrollment = allowsFreshEnrollment
    }

    func publicHeaders() -> [String: String] { ["X-Murmur-Development-Token": "test-token"] }
    func storedIdentity() -> MurmurIdentity? { identity }
    func pendingEnrollmentKeyID() -> String? { pendingKeyID }
    func enrollmentKeyID() throws -> String {
        guard allowsFreshEnrollment else {
            throw MurmurFailure(code: "unexpected_attestation", message: "Recovery should run first.", retryable: false)
        }
        pendingKeyID = "fresh-key"
        return "fresh-key"
    }
    func enrollmentAttestation(for challenge: AppAttestChallenge, keyID: String) throws -> String {
        attestationCalls += 1
        guard allowsFreshEnrollment, keyID == "fresh-key" else {
            throw MurmurFailure(code: "unexpected_attestation", message: "Recovery should not attest again.", retryable: false)
        }
        return "fresh-attestation"
    }
    func assertion(
        for challenge: AppAttestChallenge,
        method: String,
        path: String,
        bodyDigest: Data,
        keyID: String
    ) throws -> String {
        guard method == "POST",
              path == "/v1/enrollments/recover",
              bodyDigest == Data(SHA256.hash(data: Data("{}".utf8))),
              keyID == "pending-key" else {
            throw MurmurFailure(code: "bad_recovery_signature", message: "Unexpected recovery signature input.", retryable: false)
        }
        return "recover-assertion"
    }
    func completeEnrollment(_ identity: MurmurIdentity) { self.identity = identity }
    func discardPendingEnrollmentKey() { discardCalls += 1; pendingKeyID = nil }
    func clearIdentity() { identity = nil; pendingKeyID = nil }
}

private actor MomentAuthenticator: MurmurAuthenticator {
    nonisolated let environment = "development"

    func publicHeaders() -> [String: String] { [:] }
    func storedIdentity() -> MurmurIdentity? {
        .init(userID: "moment-user", deviceID: "moment-device", keyID: "moment-key")
    }
    func pendingEnrollmentKeyID() -> String? { nil }
    func enrollmentKeyID() throws -> String { "moment-key" }
    func enrollmentAttestation(for challenge: AppAttestChallenge, keyID: String) -> String {
        "moment-attestation"
    }
    func assertion(
        for challenge: AppAttestChallenge,
        method: String,
        path: String,
        bodyDigest: Data,
        keyID: String
    ) -> String {
        "moment-assertion"
    }
    func completeEnrollment(_ identity: MurmurIdentity) {}
    func discardPendingEnrollmentKey() {}
    func clearIdentity() {}
}

private final class RecoveryRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [RecoveryRecordedRequest] = []

    var requests: [RecoveryRecordedRequest] {
        lock.withLock { storage }
    }

    func record(_ request: URLRequest) {
        guard let url = request.url else { return }
        let recorded = RecoveryRecordedRequest(
            url: url,
            body: try? requestBody(request),
            headers: request.allHTTPHeaderFields ?? [:]
        )
        lock.withLock { storage.append(recorded) }
    }
}

private struct RecoveryRecordedRequest: Sendable {
    let url: URL
    let body: Data?
    let headers: [String: String]

    func header(_ name: String) -> String? {
        headers.first { $0.key.caseInsensitiveCompare(name) == .orderedSame }?.value
    }
}

private func requestBody(_ request: URLRequest) throws -> Data {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { return Data() }
    stream.open()
    defer { stream.close() }
    var result = Data()
    var buffer = [UInt8](repeating: 0, count: 4_096)
    while true {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count < 0 { throw stream.streamError ?? URLError(.cannotDecodeContentData) }
        if count == 0 { break }
        result.append(buffer, count: count)
    }
    return result
}

private final class RecoveryURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            guard let handler = Self.handler else {
                throw MurmurFailure(code: "missing_handler", message: "Missing URLProtocol handler.", retryable: false)
            }
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
