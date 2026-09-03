import CryptoKit
import Foundation

actor URLSessionMurmurAPIClient: MurmurAPIClient {
    private let baseURL: URL
    private let session: URLSession
    private let authenticator: any MurmurAuthenticator
    private let protectedRequestGate = ProtectedRequestGate()
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    init(baseURL: URL, authenticator: any MurmurAuthenticator, session: URLSession? = nil) {
        self.baseURL = baseURL
        self.authenticator = authenticator
        if let session {
            self.session = session
            return
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.timeoutIntervalForRequest = 45
        configuration.timeoutIntervalForResource = 300
        self.session = URLSession(configuration: configuration)
    }

    func storedIdentity() async throws -> MurmurIdentity? {
        try await authenticator.storedIdentity()
    }

    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity {
        if let pendingKeyID = try await authenticator.pendingEnrollmentKeyID() {
            do {
                return try await recoverEnrollment(keyID: pendingKeyID)
            } catch {
                let failure = MurmurFailure.from(error)
                if failure.code == "attestation_key_unknown" || failure.code == "app_attest_invalid_key" {
                    try await authenticator.discardPendingEnrollmentKey()
                } else {
                    throw failure
                }
            }
        }
        let keyID = try await authenticator.enrollmentKeyID()
        do {
            let challenge = try await challenge(purpose: "enrollment", keyID: keyID)
            let attestation = try await authenticator.enrollmentAttestation(for: challenge, keyID: keyID)
            let payload = EnrollmentRequest(
                challengeID: challenge.challengeID,
                inviteCode: inviteCode,
                keyID: keyID,
                attestation: attestation,
                deviceName: deviceName,
                environment: authenticator.environment
            )
            let body = try encoder.encode(payload)
            let identity: MurmurIdentity = try await send(
                path: "/v1/enrollments",
                method: "POST",
                body: body,
                contentType: "application/json",
                authenticated: false
            )
            try await authenticator.completeEnrollment(identity)
            return identity
        } catch {
            let failure = MurmurFailure.from(error)
            if failure.code == "invite_invalid"
                || failure.code == "invalid_attestation"
                || failure.code == "app_attest_invalid_key" {
                try await authenticator.discardPendingEnrollmentKey()
            }
            throw failure
        }
    }

    func createMoment(
        note: String?,
        photo: PhotoAttachment?,
        idempotencyKey: String,
        intent: MurmurMomentIntent?,
        contextMomentIDs: [String]
    ) async throws -> MomentReceipt {
        try await createMoment(
            note: note,
            photo: photo,
            musicTrack: nil,
            idempotencyKey: idempotencyKey,
            intent: intent,
            contextMomentIDs: contextMomentIDs
        )
    }

    func createMoment(
        note: String?,
        photo: PhotoAttachment?,
        musicTrack: MusicTrackAttachmentV1?,
        idempotencyKey: String,
        intent: MurmurMomentIntent?,
        contextMomentIDs: [String]
    ) async throws -> MomentReceipt {
        let boundary = "Murmur-\(UUID().uuidString)"
        let bodyURL = try makeMultipartBody(
            boundary: boundary,
            note: note,
            photo: photo,
            musicTrack: musicTrack,
            idempotencyKey: idempotencyKey,
            intent: intent,
            contextMomentIDs: contextMomentIDs
        )
        defer { try? FileManager.default.removeItem(at: bodyURL) }
        let digest = try sha256(fileURL: bodyURL)
        await protectedRequestGate.acquire()
        do {
            var request = try await authorizedRequest(
                path: "/v1/moments",
                method: "POST",
                bodyDigest: digest
            )
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            request.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key")
            let (data, response) = try await session.upload(for: request, fromFile: bodyURL)
            try validate(response: response, data: data, expected: 200..<300)
            let result = try decoder.decode(MomentReceipt.self, from: data)
            await protectedRequestGate.release()
            return result
        } catch {
            await protectedRequestGate.release()
            throw error
        }
    }

    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    try await self.consumeEvents(momentID: momentID, lastEventID: lastEventID, continuation: continuation)
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func currentProactive() async throws -> ProactiveMoment? {
        let path = "/v1/proactive/current"
        await protectedRequestGate.acquire()
        do {
            let request = try await authorizedRequest(path: path, method: "GET", bodyDigest: Self.emptyDigest)
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw MurmurFailure(code: "invalid_response", message: "服务器没有正确回应。", retryable: true)
            }
            if http.statusCode == 204 {
                await protectedRequestGate.release()
                return nil
            }
            try validate(response: response, data: data, expected: 200..<300)
            let result = try decoder.decode(ProactiveMoment.self, from: data)
            await protectedRequestGate.release()
            return result
        } catch {
            await protectedRequestGate.release()
            throw error
        }
    }

    func acknowledge(momentID: String, reply: String?) async throws {
        let body = try encoder.encode(AcknowledgeRequest(reply: reply))
        let _: EmptyResponse = try await send(
            path: "/v1/moments/\(pathComponent(momentID))/ack",
            method: "POST",
            body: body,
            contentType: "application/json",
            authenticated: true,
            allowsEmpty: true
        )
    }

    func musicAvailability() async throws -> MusicFeatureAvailability {
        try await send(
            path: "/v1/music/config",
            method: "GET",
            body: Data(),
            contentType: nil,
            authenticated: true
        )
    }

    func reportMusicPlayback(_ event: MusicPlaybackEvent) async throws {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let body = try encoder.encode(event)
        let _: PlaybackStateResponse = try await send(
            path: "/v1/music/playback-state",
            method: "PUT",
            body: body,
            contentType: "application/json",
            authenticated: true,
            allowsEmpty: true
        )
    }

    func resolveSharedMusic(text: String, idempotencyKey: String) async throws -> MusicTrackAttachmentV1 {
        let body = try encoder.encode(ResolveSharedMusicRequest(
            text: text,
            idempotencyKey: idempotencyKey
        ))
        let response: ResolveSharedMusicResponseV1 = try await send(
            path: "/v1/music/resolve-shared",
            method: "POST",
            body: body,
            contentType: "application/json",
            authenticated: true
        )
        return response.track
    }

    func createListenTogetherRoom(
        initialTrack: MusicTrackAttachmentV1,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1 {
        let body = try encoder.encode(CreateListenTogetherRoomRequest(
            initialTrack: initialTrack,
            idempotencyKey: idempotencyKey
        ))
        return try await send(
            path: "/v1/listen-together/rooms",
            method: "POST",
            body: body,
            contentType: "application/json",
            authenticated: true
        )
    }

    func currentListenTogetherRoom() async throws -> ListenTogetherRoomSnapshotV1? {
        do {
            return try await send(
                path: "/v1/listen-together/rooms/current",
                method: "GET",
                body: Data(),
                contentType: nil,
                authenticated: true
            )
        } catch let failure as MurmurFailure where failure.code == "room_not_found" {
            return nil
        }
    }

    func commandListenTogetherRoom(
        handle: String,
        command: ListenTogetherCommand,
        track: MusicTrackAttachmentV1?,
        idempotencyKey: String
    ) async throws -> ListenTogetherCommandResultV1 {
        let body = try encoder.encode(ListenTogetherCommandRequest(
            command: command,
            track: track,
            idempotencyKey: idempotencyKey
        ))
        return try await send(
            path: "/v1/listen-together/rooms/\(pathComponent(handle))/commands",
            method: "POST",
            body: body,
            contentType: "application/json",
            authenticated: true
        )
    }

    func closeListenTogetherRoom(
        handle: String,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1 {
        let body = try encoder.encode(CloseListenTogetherRoomRequest(
            idempotencyKey: idempotencyKey
        ))
        return try await send(
            path: "/v1/listen-together/rooms/\(pathComponent(handle))",
            method: "DELETE",
            body: body,
            contentType: "application/json",
            authenticated: true
        )
    }

    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws {
        let body = try encoder.encode(DeviceRequest(
            apnsToken: apnsToken,
            environment: environment,
            timezone: timezone,
            deviceName: deviceName
        ))
        let _: EmptyResponse = try await send(
            path: "/v1/device",
            method: "PUT",
            body: body,
            contentType: "application/json",
            authenticated: true,
            allowsEmpty: true
        )
    }

    func devices() async throws -> [MurmurDevice] {
        let response: DevicesResponse = try await send(
            path: "/v1/devices",
            method: "GET",
            body: Data(),
            contentType: nil,
            authenticated: true
        )
        return response.devices
    }

    func removeDevice(deviceID: String) async throws {
        let _: EmptyResponse = try await send(
            path: "/v1/devices/\(pathComponent(deviceID))",
            method: "DELETE",
            body: Data(),
            contentType: nil,
            authenticated: true,
            allowsEmpty: true
        )
        if try await authenticator.storedIdentity()?.deviceID == deviceID {
            try await authenticator.clearIdentity()
        }
    }

    func preferences() async throws -> MurmurPreferences {
        try await send(
            path: "/v1/preferences",
            method: "GET",
            body: Data(),
            contentType: nil,
            authenticated: true
        )
    }

    func updatePreferences(_ preferences: MurmurPreferences) async throws {
        let body = try encoder.encode(preferences)
        let _: MurmurPreferences = try await send(
            path: "/v1/preferences",
            method: "PATCH",
            body: body,
            contentType: "application/json",
            authenticated: true
        )
    }

    func resetLocalIdentity() async throws {
        try await authenticator.clearIdentity()
    }

    func deleteAccount() async throws {
        let _: EmptyResponse = try await send(
            path: "/v1/account",
            method: "DELETE",
            body: Data(),
            contentType: nil,
            authenticated: true,
            allowsEmpty: true
        )
        try await authenticator.clearIdentity()
    }

    private func consumeEvents(
        momentID: String,
        lastEventID: String?,
        continuation: AsyncThrowingStream<MurmurStreamEvent, Error>.Continuation
    ) async throws {
        let path = "/v1/moments/\(pathComponent(momentID))/events"
        await protectedRequestGate.acquire()
        let bytes: URLSession.AsyncBytes
        do {
            var request = try await authorizedRequest(path: path, method: "GET", bodyDigest: Self.emptyDigest)
            request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
            request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
            if let lastEventID { request.setValue(lastEventID, forHTTPHeaderField: "Last-Event-ID") }
            let handshake = try await session.bytes(for: request)
            guard let http = handshake.1 as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                throw MurmurFailure(code: "stream_rejected", message: "Murmur 没有接通回应流。", retryable: true)
            }
            bytes = handshake.0
            await protectedRequestGate.release()
        } catch {
            await protectedRequestGate.release()
            throw error
        }

        var eventID: String?
        var eventName = "message"
        var dataLines: [String] = []
        // Split on newlines by hand rather than using AsyncBytes.lines: that
        // sequence drops blank lines, and a blank line is exactly what
        // terminates one SSE event.  With it, every field of every event piles
        // into a single dispatch carrying only the last event's name, so all
        // the bubbles preceding `done` are silently lost.
        var buffer: [UInt8] = []
        buffer.reserveCapacity(1024)
        for try await byte in bytes {
            try Task.checkCancellation()
            guard byte == 0x0A else {
                buffer.append(byte)
                continue
            }
            if buffer.last == 0x0D { buffer.removeLast() }
            let line = String(decoding: buffer, as: UTF8.self)
            buffer.removeAll(keepingCapacity: true)

            if line.isEmpty {
                if let event = try decodeEvent(id: eventID, name: eventName, data: dataLines.joined(separator: "\n")) {
                    continuation.yield(event)
                }
                eventID = nil
                eventName = "message"
                dataLines.removeAll(keepingCapacity: true)
            } else if line.hasPrefix(":") {
                continue                                  // keep-alive comment
            } else if line.hasPrefix("id:") {
                eventID = String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("event:") {
                eventName = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                dataLines.append(String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces))
            }
        }
        if !dataLines.isEmpty, let event = try decodeEvent(id: eventID, name: eventName, data: dataLines.joined(separator: "\n")) {
            continuation.yield(event)
        }
    }

    private func decodeEvent(id: String?, name: String, data: String) throws -> MurmurStreamEvent? {
        let payload = Data(data.utf8)
        switch name {
        case "accepted":
            return .accepted(id: id)
        case "bubble":
            let bubble = try decoder.decode(BubblePayload.self, from: payload)
            return .bubble(id: id, text: bubble.text, musicTrack: bubble.musicTrack)
        case "quiet":
            return .quiet(id: id)
        case "done":
            let done = (try? decoder.decode(DonePayload.self, from: payload)) ?? DonePayload(move: nil, scene: nil)
            return .done(id: id, move: done.move, scene: done.scene)
        case "angles":
            let angles = try decoder.decode(AnglesPayload.self, from: payload)
            return .angles(id: id, texts: angles.angles)
        case "error":
            let failure = try decoder.decode(StreamFailurePayload.self, from: payload)
            return .failure(id: id, .init(code: failure.code, message: failure.message, retryable: failure.retryable))
        default:
            return nil
        }
    }

    private func challenge(purpose: String, keyID: String?) async throws -> AppAttestChallenge {
        let body = try encoder.encode(ChallengeRequest(purpose: purpose, keyID: keyID))
        return try await send(
            path: "/v1/auth/challenges",
            method: "POST",
            body: body,
            contentType: "application/json",
            authenticated: false
        )
    }

    private func recoverEnrollment(keyID: String) async throws -> MurmurIdentity {
        let path = "/v1/enrollments/recover"
        let body = Data("{}".utf8)
        await protectedRequestGate.acquire()
        let identity: MurmurIdentity
        do {
            var request = try await authorizedRequest(
                path: path,
                method: "POST",
                bodyDigest: Data(SHA256.hash(data: body)),
                explicitKeyID: keyID
            )
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            let (data, response) = try await session.data(for: request)
            try validate(response: response, data: data, expected: 200..<300)
            identity = try decoder.decode(MurmurIdentity.self, from: data)
            await protectedRequestGate.release()
        } catch {
            await protectedRequestGate.release()
            throw error
        }
        try await authenticator.completeEnrollment(identity)
        return identity
    }

    private func authorizedRequest(
        path: String,
        method: String,
        bodyDigest: Data,
        explicitKeyID: String? = nil
    ) async throws -> URLRequest {
        let keyID: String
        if let explicitKeyID {
            keyID = explicitKeyID
        } else {
            guard let identity = try await authenticator.storedIdentity() else {
                throw MurmurFailure(code: "not_enrolled", message: "需要先用邀请码连接 Murmur。", retryable: false)
            }
            keyID = identity.keyID
        }
        let challenge = try await challenge(purpose: "request", keyID: keyID)
        let assertion = try await authenticator.assertion(
            for: challenge,
            method: method,
            path: path,
            bodyDigest: bodyDigest,
            keyID: keyID
        )
        var request = URLRequest(url: try url(path: path))
        request.httpMethod = method
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue(keyID, forHTTPHeaderField: "X-Murmur-Key-ID")
        request.setValue(challenge.challengeID, forHTTPHeaderField: "X-Murmur-Challenge-ID")
        request.setValue(assertion, forHTTPHeaderField: "X-Murmur-Assertion")
        for (field, value) in await authenticator.publicHeaders() {
            request.setValue(value, forHTTPHeaderField: field)
        }
        return request
    }

    private func send<Response: Decodable>(
        path: String,
        method: String,
        body: Data,
        contentType: String?,
        authenticated: Bool,
        allowsEmpty: Bool = false
    ) async throws -> Response {
        if authenticated {
            await protectedRequestGate.acquire()
            do {
                var request = try await authorizedRequest(
                    path: path,
                    method: method,
                    bodyDigest: Data(SHA256.hash(data: body))
                )
                request.httpBody = body.isEmpty ? nil : body
                request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
                if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
                request.setValue("application/json", forHTTPHeaderField: "Accept")
                let (data, response) = try await session.data(for: request)
                try validate(response: response, data: data, expected: 200..<300)
                let decoded: Response
                if data.isEmpty, allowsEmpty, let empty = EmptyResponse() as? Response {
                    decoded = empty
                } else {
                    decoded = try decoder.decode(Response.self, from: data)
                }
                await protectedRequestGate.release()
                return decoded
            } catch {
                await protectedRequestGate.release()
                throw error
            }
        }

        var request = URLRequest(url: try url(path: path))
        request.httpMethod = method
        for (field, value) in await authenticator.publicHeaders() {
            request.setValue(value, forHTTPHeaderField: field)
        }
        request.httpBody = body.isEmpty ? nil : body
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data, expected: 200..<300)
        if data.isEmpty, allowsEmpty, let empty = EmptyResponse() as? Response {
            return empty
        }
        return try decoder.decode(Response.self, from: data)
    }

    private func validate(response: URLResponse, data: Data, expected: Range<Int>) throws {
        guard let http = response as? HTTPURLResponse else {
            throw MurmurFailure(code: "invalid_response", message: "服务器没有正确回应。", retryable: true)
        }
        guard expected.contains(http.statusCode) else {
            if let envelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
                throw MurmurFailure(
                    code: envelope.error.code,
                    message: envelope.error.message,
                    retryable: envelope.error.retryable
                )
            }
            throw MurmurFailure.fromHTTPStatus(http.statusCode)
        }
    }

    private func url(path: String) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw MurmurFailure(code: "invalid_base_url", message: "Murmur 服务地址无效。", retryable: false)
        }
        let prefix = components.path.hasSuffix("/") ? String(components.path.dropLast()) : components.path
        components.path = prefix + path
        guard let url = components.url else {
            throw MurmurFailure(code: "invalid_url", message: "Murmur 请求地址无效。", retryable: false)
        }
        return url
    }

    /// Internal rather than private so the encoding itself can be asserted on.
    /// Every field here is one the server parses by name: a rename that only a
    /// client double ever sees is a green test suite over a feature the server
    /// never receives.
    func makeMultipartBody(
        boundary: String,
        note: String?,
        photo: PhotoAttachment?,
        musicTrack: MusicTrackAttachmentV1? = nil,
        idempotencyKey: String,
        intent: MurmurMomentIntent?,
        contextMomentIDs: [String]
    ) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("murmur-multipart-\(UUID().uuidString)")
        guard FileManager.default.createFile(atPath: url.path, contents: nil) else {
            throw MurmurFailure(code: "upload_prepare_failed", message: "无法准备上传。", retryable: true)
        }
        try FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.complete],
            ofItemAtPath: url.path
        )
        let output = try FileHandle(forWritingTo: url)
        defer { try? output.close() }

        func write(_ string: String) throws { try output.write(contentsOf: Data(string.utf8)) }
        func field(_ name: String, _ value: String) throws {
            try write("--\(boundary)\r\n")
            try write("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n")
            try write("\(value)\r\n")
        }
        try field("idempotency_key", idempotencyKey)
        if let intent { try field("intent", intent.rawValue) }
        if !contextMomentIDs.isEmpty {
            let encoded = try JSONEncoder().encode(contextMomentIDs)
            guard let value = String(data: encoded, encoding: .utf8) else {
                throw MurmurFailure(
                    code: "upload_prepare_failed",
                    message: "无法准备历史上下文。",
                    retryable: false
                )
            }
            try field("context_moment_ids", value)
        }
        if let note, !note.isEmpty { try field("note", note) }
        if let musicTrack {
            guard photo == nil else {
                throw MurmurFailure(
                    code: "invalid_music_attachment",
                    message: "歌曲不能和照片一起发送。",
                    retryable: false
                )
            }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            guard let value = String(data: try encoder.encode(musicTrack), encoding: .utf8) else {
                throw MurmurFailure(
                    code: "upload_prepare_failed",
                    message: "无法准备这首歌。",
                    retryable: false
                )
            }
            try field("music_track", value)
        }
        // What the photo says about itself.  It rides as its own field rather
        // than being written back into the JPEG: the bytes going up are a
        // downsampled derivative, and forging EXIF into a derivative so the
        // server can "discover" what this app already knows is one indirection
        // too many.
        if let provenance = photo?.provenance, !provenance.isEmpty {
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            guard let value = String(data: try encoder.encode(provenance), encoding: .utf8) else {
                throw MurmurFailure(
                    code: "upload_prepare_failed",
                    message: "无法准备这张照片的信息。",
                    retryable: false
                )
            }
            try field("provenance", value)
        }
        if let photo {
            try write("--\(boundary)\r\n")
            try write("Content-Disposition: form-data; name=\"image\"; filename=\"\(photo.filename)\"\r\n")
            try write("Content-Type: \(photo.mimeType)\r\n\r\n")
            let input = try FileHandle(forReadingFrom: photo.originalURL)
            defer { try? input.close() }
            while let chunk = try input.read(upToCount: 1_048_576), !chunk.isEmpty {
                try output.write(contentsOf: chunk)
            }
            try write("\r\n")
        }
        try write("--\(boundary)--\r\n")
        return url
    }

    private func sha256(fileURL: URL) throws -> Data {
        let handle = try FileHandle(forReadingFrom: fileURL)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1_048_576), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return Data(hasher.finalize())
    }

    private func pathComponent(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-_"))) ?? value
    }

    private static let emptyDigest = Data(SHA256.hash(data: Data()))
}

extension URLSessionMurmurAPIClient: MusicPlaybackEventTransport {
    func send(_ event: MusicPlaybackEvent) async throws {
        try await reportMusicPlayback(event)
    }
}

actor ProtectedRequestGate {
    private var acquired = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func acquire() async {
        if !acquired {
            acquired = true
            return
        }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func release() {
        if waiters.isEmpty {
            acquired = false
        } else {
            waiters.removeFirst().resume()
        }
    }
}

private struct ChallengeRequest: Encodable {
    let purpose: String
    let keyID: String?
    enum CodingKeys: String, CodingKey { case purpose; case keyID = "key_id" }
}

private struct ResolveSharedMusicRequest: Encodable {
    let text: String
    let idempotencyKey: String

    enum CodingKeys: String, CodingKey {
        case text
        case idempotencyKey = "idempotency_key"
    }
}

private struct CreateListenTogetherRoomRequest: Encodable {
    let initialTrack: MusicTrackAttachmentV1
    let idempotencyKey: String

    enum CodingKeys: String, CodingKey {
        case initialTrack = "initial_track"
        case idempotencyKey = "idempotency_key"
    }
}

private struct ListenTogetherCommandRequest: Encodable {
    let command: ListenTogetherCommand
    let track: MusicTrackAttachmentV1?
    let idempotencyKey: String

    enum CodingKeys: String, CodingKey {
        case command, track
        case idempotencyKey = "idempotency_key"
    }
}

private struct CloseListenTogetherRoomRequest: Encodable {
    let idempotencyKey: String

    enum CodingKeys: String, CodingKey {
        case idempotencyKey = "idempotency_key"
    }
}

private struct EnrollmentRequest: Encodable {
    let challengeID: String
    let inviteCode: String
    let keyID: String
    let attestation: String
    let deviceName: String?
    let environment: String
    enum CodingKeys: String, CodingKey {
        case challengeID = "challenge_id"
        case inviteCode = "invite_code"
        case keyID = "key_id"
        case attestation
        case deviceName = "device_name"
        case environment
    }
}

private struct AcknowledgeRequest: Encodable { let reply: String? }

struct DeviceRequest: Encodable {
    let apnsToken: String?
    let environment: String
    let timezone: String
    let deviceName: String?
    enum CodingKeys: String, CodingKey {
        case apnsToken = "apns_token"
        case environment, timezone
        case deviceName = "device_name"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if let apnsToken {
            try container.encode(apnsToken, forKey: .apnsToken)
        } else {
            try container.encodeNil(forKey: .apnsToken)
        }
        try container.encode(environment, forKey: .environment)
        try container.encode(timezone, forKey: .timezone)
        try container.encodeIfPresent(deviceName, forKey: .deviceName)
    }
}

private struct DevicesResponse: Decodable, Sendable { let devices: [MurmurDevice] }

private struct BubblePayload: Decodable {
    let text: String
    let musicTrack: MusicTrackAttachmentV1?

    enum CodingKeys: String, CodingKey {
        case text
        case musicTrack = "music_track"
    }
}
private struct DonePayload: Decodable { let move: String?; let scene: String? }
private struct AnglesPayload: Decodable { let angles: [String] }
private struct StreamFailurePayload: Decodable { let code: String; let message: String; let retryable: Bool }
private struct ErrorEnvelope: Decodable { let error: StreamFailurePayload }

private struct EmptyResponse: Codable {
    init() {}
}

private struct PlaybackStateResponse: Decodable {
    let accepted: Bool?
    init() { accepted = nil }
}
