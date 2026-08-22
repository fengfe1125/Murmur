import XCTest
@testable import Murmur

/// The room's contract, away from the screen: the photo goes up once with the
/// reading intent, the guess and its three openers come back, an opener is
/// picked up rather than sent, and the full-resolution original is gone on
/// every path out.
@MainActor
final class PhotoRoomModelTests: XCTestCase {
    private func makeImage() -> UIImage {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 120, height: 160))
        return renderer.image { context in
            UIColor.systemTeal.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 120, height: 160))
        }
    }

    private func makeModel(
        api: RoomAPI
    ) -> PhotoRoomModel {
        PhotoRoomModel(
            image: makeImage(),
            api: api,
            uploadTimeoutSeconds: 5,
            requestTimeoutSeconds: 5,
            bubblePacing: .instant
        )
    }

    private func settle(_ condition: @escaping () -> Bool) async {
        for _ in 0..<400 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    func testTheOpeningUploadCarriesThePhotoAndTheReadingIntent() async {
        let api = RoomAPI()
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .listening }

        let calls = await api.calls
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls[0].intent, .photoReading)
        XCTAssertNil(calls[0].note)
        XCTAssertTrue(calls[0].hasPhoto)
    }

    func testTheGuessAndItsOpenersLandInTheRoom() async {
        let api = RoomAPI()
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .listening }

        XCTAssertEqual(model.lines.map(\.text), ["这是……刚下过雨？"])
        XCTAssertEqual(model.lines.map(\.author), [.murmur])
        XCTAssertEqual(model.openers, ["那天的天气", "右边那个人", "上次说要再来"])
    }

    /// An opener is a door, not a message: tapping one fills the field and
    /// leaves the decision with the person.
    func testPickingAnOpenerFillsTheFieldWithoutSending() async {
        let api = RoomAPI()
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .listening }

        model.pick(opener: "那天的天气")
        XCTAssertEqual(model.draft, "那天的天气")
        let calls = await api.calls
        XCTAssertEqual(calls.count, 1)
        XCTAssertFalse(model.openers.isEmpty)
    }

    /// Everything after the photo is an ordinary moment — no second upload of
    /// the same pixels, and the openers close behind the first line.
    func testSayingSomethingSendsTextOnlyAndClosesTheOpeners() async {
        let api = RoomAPI()
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .listening }

        model.draft = "那天是我搬走前最后一次去"
        model.send()
        await settle { model.phase == .listening && model.lines.count == 3 }

        let calls = await api.calls
        XCTAssertEqual(calls.count, 2)
        XCTAssertNil(calls[1].intent)
        XCTAssertFalse(calls[1].hasPhoto)
        XCTAssertEqual(calls[1].note, "那天是我搬走前最后一次去")
        XCTAssertTrue(model.openers.isEmpty)
        XCTAssertEqual(model.lines.map(\.author), [.murmur, .mine, .murmur])
    }

    /// The original is temporary and the reading is one of its terminal paths:
    /// once the server has answered, the file has nothing left to do.
    func testTheOriginalIsDeletedOnceTheReadingIsOver() async {
        let api = RoomAPI()
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .listening }

        let leftovers = temporaryUploads()
        XCTAssertTrue(leftovers.isEmpty, "left behind: \(leftovers)")
    }

    /// Leaving is a terminal path too, including while the upload is still on
    /// the wire.  A room closed mid-flight must not leave a full-resolution
    /// photo in the temporary directory for the next cold start to find.
    func testClosingMidFlightTakesTheOriginalWithIt() async {
        let api = RoomAPI(mode: .neverAnswers)
        let model = makeModel(api: api)
        model.open()
        await settle { !self.temporaryUploads().isEmpty }
        model.close()
        await settle { self.temporaryUploads().isEmpty }

        let leftovers = temporaryUploads()
        XCTAssertTrue(leftovers.isEmpty, "left behind: \(leftovers)")
    }

    /// A reading that fails on the wire keeps its file, because 再试一次 is an
    /// offer to send that same photo again under the same idempotency key.
    func testARetryableFailureKeepsTheFileAndTheKey() async {
        let api = RoomAPI(mode: .failsFirstUpload)
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .unopened }
        XCTAssertTrue(model.canRetryOpening)
        XCTAssertEqual(model.failure?.retryable, true)

        model.open()
        await settle { model.phase == .listening }
        let calls = await api.calls
        XCTAssertEqual(calls.count, 2)
        XCTAssertEqual(calls[0].idempotencyKey, calls[1].idempotencyKey)
        model.close()
    }

    /// Nothing was said, so nothing stays on screen claiming it was — and the
    /// words come back to the field rather than being retyped.
    func testALineThatDidNotLandComesBackToTheField() async {
        let api = RoomAPI(mode: .failsSecondSend)
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .listening }

        model.draft = "那天是我搬走前最后一次去"
        model.send()
        await settle { model.failure != nil }

        XCTAssertEqual(model.draft, "那天是我搬走前最后一次去")
        XCTAssertEqual(model.lines.map(\.author), [.murmur])
        // The room is still open, and the send button is the retry.
        XCTAssertEqual(model.phase, .listening)
        XCTAssertFalse(model.canRetryOpening)
        XCTAssertTrue(model.canSend)
    }

    /// A room whose photo the server never saw does not take dictation: an
    /// answer about nothing is worse than a closed field.
    func testAnUnopenedRoomDoesNotTakeALine() async {
        let api = RoomAPI(mode: .failsFirstUpload)
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .unopened }

        model.draft = "那天是我搬走前最后一次去"
        XCTAssertFalse(model.canSend)
        model.send()
        let calls = await api.calls
        XCTAssertEqual(calls.count, 1)
        XCTAssertTrue(model.lines.isEmpty)
        model.close()
    }

    private func temporaryUploads() -> [String] {
        let directory = FileManager.default.temporaryDirectory
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return names.filter { $0.hasPrefix("murmur-onthisday-") }
    }
}

private actor RoomAPI: MurmurAPIClient {
    enum Mode { case normal, failsFirstUpload, failsSecondSend, neverAnswers }

    struct Call {
        let note: String?
        let hasPhoto: Bool
        let idempotencyKey: String
        let intent: MurmurMomentIntent?
    }

    private let mode: Mode
    private(set) var calls: [Call] = []
    private var moments = 0

    init(mode: Mode = .normal) { self.mode = mode }

    func storedIdentity() async throws -> MurmurIdentity? {
        .init(userID: "u", deviceID: "d", keyID: "k")
    }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity {
        .init(userID: "u", deviceID: "d", keyID: "k")
    }

    func createMoment(
        note: String?, photo: PhotoAttachment?, idempotencyKey: String,
        intent: MurmurMomentIntent?
    ) async throws -> MomentReceipt {
        calls.append(.init(
            note: note, hasPhoto: photo != nil,
            idempotencyKey: idempotencyKey, intent: intent
        ))
        if mode == .failsFirstUpload, calls.count == 1 {
            throw MurmurFailure(code: "network_error", message: "没连上。", retryable: true)
        }
        if mode == .failsSecondSend, calls.count == 2 {
            throw MurmurFailure(code: "network_error", message: "没连上。", retryable: true)
        }
        if mode == .neverAnswers { try await Task.sleep(for: .seconds(30)) }
        moments += 1
        return .init(momentID: "moment-\(moments)", status: "queued")
    }

    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        let isOpening = momentID == "moment-1"
        return AsyncThrowingStream { continuation in
            continuation.yield(.accepted(id: "\(momentID)-a"))
            if isOpening {
                continuation.yield(.bubble(id: "\(momentID)-b", text: "这是……刚下过雨？"))
                continuation.yield(.angles(
                    id: "\(momentID)-g",
                    texts: ["那天的天气", "右边那个人", "上次说要再来"]
                ))
            } else {
                continuation.yield(.bubble(id: "\(momentID)-b", text: "那后来呢"))
            }
            continuation.yield(.done(id: "\(momentID)-d", move: nil, scene: nil))
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
