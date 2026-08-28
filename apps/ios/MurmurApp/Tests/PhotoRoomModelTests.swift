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

    /// SSE sequence numbers belong to one moment and restart on the next one.
    /// A room spans several moments, so using that bare sequence as a SwiftUI
    /// identity makes later replies reuse and overwrite the first bubble.
    func testRepliesKeepDistinctIdentitiesWhenEveryMomentReusesEventSequence() async {
        let api = RoomAPI()
        let model = makeModel(api: api)
        model.open()
        await settle { model.phase == .listening }

        model.draft = "第一句"
        model.send()
        await settle { model.phase == .listening && model.lines.count == 3 }
        model.draft = "第二句"
        model.send()
        await settle { model.phase == .listening && model.lines.count == 5 }

        XCTAssertEqual(
            model.lines.map(\.text),
            ["这是……刚下过雨？", "第一句", "那后来呢", "第二句", "后来真的去了"]
        )
        XCTAssertEqual(Set(model.lines.map(\.id)).count, model.lines.count)
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

    // MARK: - The room writes into the conversation's history

    private func makeSession(api: RoomAPI, directory: URL) -> MurmurSessionModel {
        MurmurSessionModel(
            api: api,
            transcriptStore: MurmurTranscriptStore(directory: directory),
            archive: MurmurArchive(store: MurmurTranscriptStore(
                directory: directory.appendingPathComponent("archive", isDirectory: true)
            )),
            bubblePacing: .instant
        )
    }

    private func scratchDirectory() -> URL {
        URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
    }

    /// Kept, but kept apart: the photo, the reading of it and every line after
    /// land in 当年今日's archive and survive the room closing — and the
    /// conversation never sees a word of it.
    func testTheRoomsExchangeGoesToTheArchiveAndNotTheConversation() async throws {
        let directory = scratchDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = RoomAPI()
        let session = makeSession(api: api, directory: directory)
        let model = session.makePhotoRoom(image: makeImage())

        let archive = session.archive
        model.open()
        await settle { model.phase == .listening }
        await settle { archive.rows.count == 2 }

        XCTAssertEqual(archive.rows.map(\.author), [.you, .murmur])
        XCTAssertEqual(archive.rows[1].text, "这是……刚下过雨？")
        // The picture itself, not just the words about it.
        XCTAssertEqual(archive.rows[0].text, "")
        XCTAssertNotNil(archive.rows[0].imageFile)

        model.draft = "那天是我搬走前最后一次去"
        model.send()
        await settle { archive.rows.count == 4 }
        model.close()

        XCTAssertEqual(
            archive.rows.map(\.text),
            ["", "这是……刚下过雨？", "那天是我搬走前最后一次去", "那后来呢"]
        )
        XCTAssertEqual(archive.rows[2].delivery, .answered)
        // The picture got its second tick when the reading landed.
        XCTAssertEqual(archive.rows[0].delivery, .answered)
        // The whole point of the archive: none of this is in the chat.
        XCTAssertTrue(session.messages.isEmpty)
        // And it is filed under the day it happened on.
        XCTAssertEqual(archive.daysWithRooms.count, 1)
        XCTAssertEqual(archive.photoCount(on: Date()), 1)
        XCTAssertEqual(archive.rows(on: Date()).count, 4)

        let store = MurmurTranscriptStore(
            directory: directory.appendingPathComponent("archive", isDirectory: true)
        )
        let reloaded = await store.load()
        XCTAssertEqual(reloaded.map(\.text), archive.rows.map(\.text))
        let name = try XCTUnwrap(reloaded[0].imageFile)
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.imageURL(for: name).path))
    }

    /// Nothing was said, so the archive must not claim it was.  The room puts
    /// the words back in the field; the archive puts the row back too.
    func testALineThatDidNotLandLeavesNoTraceInTheArchive() async {
        let directory = scratchDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = RoomAPI(mode: .failsSecondSend)
        let session = makeSession(api: api, directory: directory)
        let model = session.makePhotoRoom(image: makeImage())

        model.open()
        await settle { model.phase == .listening }
        model.draft = "那天是我搬走前最后一次去"
        model.send()
        await settle { model.failure != nil }
        await settle { session.archive.rows.count == 2 }

        XCTAssertEqual(session.archive.rows.map(\.author), [.you, .murmur])
        XCTAssertFalse(session.archive.rows.contains { $0.text.contains("搬走前") })
        model.close()
    }

    func testAReceivedLineSurvivesAStreamFailureWithoutReturningToTheDraft() async {
        let directory = scratchDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = RoomAPI(mode: .failsSecondStream)
        let session = makeSession(api: api, directory: directory)
        let model = session.makePhotoRoom(image: makeImage())

        model.open()
        await settle { model.phase == .listening }
        model.draft = "服务端已经收到了"
        model.send()
        await settle { model.failure != nil }

        XCTAssertEqual(model.lines.map(\.author), [.murmur, .mine])
        XCTAssertEqual(model.draft, "")
        XCTAssertEqual(session.archive.rows.last?.text, "服务端已经收到了")
        XCTAssertEqual(session.archive.rows.last?.momentID, "moment-2")
        XCTAssertEqual(session.archive.rows.last?.delivery, .sent)
        model.close()
    }

    /// 再试一次 re-sends the same moment under the same key.  One photo was
    /// sent, so the archive shows one photo.
    func testRetryingTheOpeningDoesNotWriteThePhotoTwice() async {
        let directory = scratchDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = RoomAPI(mode: .failsFirstUpload)
        let session = makeSession(api: api, directory: directory)
        let model = session.makePhotoRoom(image: makeImage())

        model.open()
        await settle { model.phase == .unopened }
        // The server never saw it, so there is nothing to write down yet.
        XCTAssertTrue(session.archive.rows.isEmpty)

        model.open()
        await settle { model.phase == .listening }
        await settle { session.archive.rows.count == 2 }
        XCTAssertEqual(session.archive.rows.filter { $0.author == .you }.count, 1)
        model.close()
    }

    /// Leaving while a line is still on the wire: this device never saw a
    /// receipt, so the row says failed rather than spinning for ever.
    func testLeavingMidSendDoesNotLeaveTheArchiveSpinning() async {
        let directory = scratchDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = RoomAPI(mode: .hangsOnSecondSend)
        let session = makeSession(api: api, directory: directory)
        let model = session.makePhotoRoom(image: makeImage())

        model.open()
        await settle { model.phase == .listening }
        model.draft = "那天是我搬走前最后一次去"
        model.send()
        await settle { session.archive.rows.count == 3 }
        XCTAssertEqual(session.archive.rows[2].delivery, .sending)

        model.close()
        XCTAssertEqual(session.archive.rows[2].delivery, .failed)
    }

    func testHistoricalDayCanContinueWithItsLatestRoomContext() async throws {
        let directory = scratchDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = RoomAPI()
        let archive = MurmurArchive(store: MurmurTranscriptStore(
            directory: directory.appendingPathComponent("archive", isDirectory: true)
        ))
        let selectedDay = Date(timeIntervalSince1970: 1_711_411_200)
        let source = FileManager.default.temporaryDirectory
            .appendingPathComponent("archive-day-model-\(UUID().uuidString).jpg")
        defer { try? FileManager.default.removeItem(at: source) }
        try XCTUnwrap(makeImage().jpegData(compressionQuality: 0.8)).write(to: source)
        await archive.record(
            .init(
                author: .you,
                text: "",
                sentAt: selectedDay,
                momentID: "archive-opening"
            ),
            photoURL: source
        )
        await archive.record(
            .init(
                author: .murmur,
                text: "那天风很大",
                sentAt: selectedDay.addingTimeInterval(1),
                momentID: "archive-opening"
            ),
            photoURL: nil
        )
        let model = ArchiveDayModel(
            day: selectedDay,
            archive: archive,
            api: api,
            requestTimeoutSeconds: 5,
            bubblePacing: .instant
        )
        let beforeSend = Date()

        model.draft = "后来我又去了"
        model.send()
        await settle { model.phase == .listening && archive.rows.count == 4 }

        let calls = await api.calls
        let call = try XCTUnwrap(calls.first)
        XCTAssertEqual(call.contextMomentIDs, ["archive-opening"])
        let continued = Array(archive.rows.suffix(2))
        XCTAssertEqual(continued.map(\.text), ["后来我又去了", "后来真的去了"])
        XCTAssertTrue(continued.allSatisfy {
            Calendar.murmur.isDate($0.archiveDay ?? .distantFuture, inSameDayAs: selectedDay)
        })
        XCTAssertTrue(continued.allSatisfy { $0.sentAt >= beforeSend })
        XCTAssertEqual(continued[0].momentID, "moment-1")
        XCTAssertEqual(continued[0].delivery, .answered)

        model.draft = "再后来呢"
        model.send()
        await settle { model.phase == .listening && archive.rows.count == 6 }

        let continuedCalls = await api.calls
        XCTAssertEqual(
            continuedCalls[1].contextMomentIDs,
            ["archive-opening", "moment-1"]
        )
        XCTAssertEqual(Set(archive.rows.map(\.id)).count, archive.rows.count)
    }

    /// The same contract the photo room keeps: leaving while a line is still
    /// on the wire marks it failed.  Deleting it would take back words that
    /// were typed on purpose, into no composer that still exists to hold them.
    func testLeavingAnArchiveDayMidSendMarksTheRowRatherThanDeletingIt() async {
        let directory = scratchDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = RoomAPI(mode: .hangsOnSecondSend)
        let archive = MurmurArchive(store: MurmurTranscriptStore(
            directory: directory.appendingPathComponent("archive", isDirectory: true)
        ))
        let selectedDay = Date(timeIntervalSince1970: 1_711_411_200)
        let model = ArchiveDayModel(
            day: selectedDay,
            archive: archive,
            api: api,
            requestTimeoutSeconds: 5,
            bubblePacing: .instant
        )

        model.draft = "第一句"
        model.send()
        await settle { model.phase == .listening && archive.rows.count == 2 }

        model.draft = "还在路上的那句"
        model.send()
        await settle { archive.rows.count == 3 }
        XCTAssertEqual(archive.rows[2].delivery, .sending)

        model.close()
        // The cancellation lands a hop later.  Waiting for it is what makes
        // this about the row that survives rather than the instant before the
        // old code deleted it.
        await settle { archive.rows.last?.delivery != .sending }

        XCTAssertEqual(archive.rows.count, 3)
        XCTAssertEqual(archive.rows.last?.text, "还在路上的那句")
        XCTAssertEqual(archive.rows.last?.delivery, .failed)
    }

    func testHistoricalDayFailureWithdrawsTheUnsentRowAndRestoresTheDraft() async {
        let directory = scratchDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = RoomAPI(mode: .failsFirstUpload)
        let archive = MurmurArchive(store: MurmurTranscriptStore(
            directory: directory.appendingPathComponent("archive", isDirectory: true)
        ))
        let selectedDay = Date(timeIntervalSince1970: 1_711_411_200)
        let model = ArchiveDayModel(
            day: selectedDay,
            archive: archive,
            api: api,
            requestTimeoutSeconds: 5,
            bubblePacing: .instant
        )

        model.draft = "这句先别丢"
        model.send()
        await settle { model.failure != nil }

        XCTAssertEqual(model.phase, .listening)
        XCTAssertEqual(model.draft, "这句先别丢")
        XCTAssertTrue(archive.rows.isEmpty)
    }

    private func temporaryUploads() -> [String] {
        let directory = FileManager.default.temporaryDirectory
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return names.filter { $0.hasPrefix("murmur-onthisday-") }
    }
}

private actor RoomAPI: MurmurAPIClient {
    enum Mode {
        case normal, failsFirstUpload, failsSecondSend, failsSecondStream
        case neverAnswers, hangsOnSecondSend
    }

    struct Call {
        let note: String?
        let hasPhoto: Bool
        let idempotencyKey: String
        let intent: MurmurMomentIntent?
        let contextMomentIDs: [String]
    }

    private let mode: Mode
    private(set) var calls: [Call] = []
    private var moments = 0
    private var readingMoments: Set<String> = []

    init(mode: Mode = .normal) { self.mode = mode }

    func storedIdentity() async throws -> MurmurIdentity? {
        .init(userID: "u", deviceID: "d", keyID: "k")
    }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity {
        .init(userID: "u", deviceID: "d", keyID: "k")
    }

    func createMoment(
        note: String?, photo: PhotoAttachment?, idempotencyKey: String,
        intent: MurmurMomentIntent?, contextMomentIDs: [String]
    ) async throws -> MomentReceipt {
        calls.append(.init(
            note: note, hasPhoto: photo != nil,
            idempotencyKey: idempotencyKey, intent: intent,
            contextMomentIDs: contextMomentIDs
        ))
        if mode == .failsFirstUpload, calls.count == 1 {
            throw MurmurFailure(code: "network_error", message: "没连上。", retryable: true)
        }
        if mode == .failsSecondSend, calls.count == 2 {
            throw MurmurFailure(code: "network_error", message: "没连上。", retryable: true)
        }
        if mode == .neverAnswers { try await Task.sleep(for: .seconds(30)) }
        if mode == .hangsOnSecondSend, calls.count == 2 {
            try await Task.sleep(for: .seconds(30))
        }
        moments += 1
        let momentID = "moment-\(moments)"
        if intent == .photoReading { readingMoments.insert(momentID) }
        return .init(momentID: momentID, status: "queued")
    }

    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        let isOpening = readingMoments.contains(momentID)
        let failsStream = mode == .failsSecondStream
        return AsyncThrowingStream { continuation in
            // Production SSE IDs are per-moment integer sequences.  They
            // deliberately repeat here so room tests exercise the real wire.
            continuation.yield(.accepted(id: "1"))
            if isOpening {
                continuation.yield(.bubble(id: "2", text: "这是……刚下过雨？"))
                continuation.yield(.angles(
                    id: "3",
                    texts: ["那天的天气", "右边那个人", "上次说要再来"]
                ))
            } else {
                if failsStream {
                    continuation.yield(.failure(
                        id: "2",
                        MurmurFailure(
                            code: "stream_ended",
                            message: "回应中断了。",
                            retryable: true
                        )
                    ))
                    continuation.finish()
                    return
                }
                continuation.yield(.bubble(
                    id: "2",
                    text: momentID == "moment-2" ? "那后来呢" : "后来真的去了"
                ))
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
