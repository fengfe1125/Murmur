import DeviceCheck
import UIKit
import XCTest
@testable import Murmur

@MainActor
final class MurmurSessionModelTests: XCTestCase {
    func testTranscriptSurvivesAReloadAndMarksInterruptedSendsFailed() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = MurmurTranscriptStore(directory: directory)

        await store.save([
            .init(author: .you, text: "在吗", delivery: .answered),
            .init(author: .murmur, text: "在呢"),
            .init(author: .you, text: "这条没发出去", delivery: .sending),
        ])

        let reloaded = await MurmurTranscriptStore(directory: directory).load()
        XCTAssertEqual(reloaded.map(\.text), ["在吗", "在呢", "这条没发出去"])
        XCTAssertEqual(reloaded.map(\.author), [.you, .murmur, .you])
        // A send interrupted by a crash never reached the server, so it must
        // not come back still spinning.
        XCTAssertEqual(reloaded[2].delivery, .failed)
        XCTAssertEqual(reloaded[0].delivery, .answered)

        await store.clear()
        let cleared = await MurmurTranscriptStore(directory: directory).load()
        XCTAssertTrue(cleared.isEmpty)
    }

    /// After a relaunch the app has forgotten why a row failed, but the row
    /// itself still holds everything a send needs.  Before this, a restart
    /// turned every failed message into a mark that could not be pressed.
    func testAFailedRowCanStillBeSentAfterARelaunch() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = MurmurTranscriptStore(directory: directory)
        await store.save([
            .init(author: .you, text: "这条没发出去", delivery: .failed,
                  idempotencyKey: "key-from-last-launch"),
        ])

        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(
            api: api,
            transcriptStore: MurmurTranscriptStore(directory: directory),
            bubblePacing: .instant
        )
        await model.bootstrap()
        await model.loadTranscript()
        let row = try XCTUnwrap(model.messages.first)
        // Nothing in memory explains the failure — this is the state a relaunch
        // leaves behind, and the one that used to be a dead end.
        XCTAssertNil(model.sendFailures[row.id])

        model.resend(row.id)
        try await waitUntil { model.messages.contains { $0.author == .murmur } }

        XCTAssertEqual(model.messages[0].delivery, .answered)
        // The same moment, not a second one: the key came off the row.
        let keys = await api.idempotencyKeys
        XCTAssertEqual(keys, ["key-from-last-launch"])
    }

    func testAdoptedPhotoSurvivesASaveThatDoesNotNameItYet() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = MurmurTranscriptStore(directory: directory)
        let source = try writeTestJPEG(size: CGSize(width: 200, height: 150), name: "adopted")
        defer { try? FileManager.default.removeItem(at: source) }

        let adopted = await store.adoptImage(at: source, id: "m1")
        let name = try XCTUnwrap(adopted)
        // The reply lands first: the transcript is saved while the message
        // still knows nothing about the photo copied in beside it.
        await store.save([.init(id: "m1", author: .you, text: "看这个")])
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.imageURL(for: name).path))

        await store.save([.init(id: "m1", author: .you, text: "看这个", imageFile: name)])
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.imageURL(for: name).path))

        // Once nothing refers to it, it goes.
        await store.save([.init(id: "m2", author: .you, text: "别的")])
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.imageURL(for: name).path))
    }

    func testASentPhotoIsStillReadableAfterTheMomentCompletes() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = MurmurTranscriptStore(directory: directory)
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, transcriptStore: store, bubblePacing: .instant)
        await model.bootstrap()
        let source = try writeTestJPEG(size: CGSize(width: 400, height: 300), name: "sent-photo")

        model.preparePhoto(at: source)
        try await waitUntil { model.phase == .ready }
        model.submit()
        try await waitUntil { model.phase == .complete }
        try await waitUntil { model.messages.first?.imageFile != nil }

        // The whole point of keeping a copy: the picture is still there to be
        // opened after the upload's temporary file has been cleaned up.
        let name = try XCTUnwrap(model.messages.first?.imageFile)
        let url = store.imageURL(for: name)
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
        XCTAssertNotNil(try? PhotoLoader.downsample(url: url, maximumPixels: 400))
    }

    func testTranscriptKeepsOnlyTheMostRecentHistory() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = MurmurTranscriptStore(directory: directory)

        let overflow = MurmurTranscriptStore.historyLimit + 40
        await store.save((0..<overflow).map { .init(author: .you, text: "m\($0)") })

        let reloaded = await store.load()
        XCTAssertEqual(reloaded.count, MurmurTranscriptStore.historyLimit)
        XCTAssertEqual(reloaded.first?.text, "m40")
        XCTAssertEqual(reloaded.last?.text, "m\(overflow - 1)")
    }

    func testColdBootstrapIsEmptyAndDoesNotFetchProactive() async throws {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)

        await model.bootstrap()

        XCTAssertEqual(model.phase, .idle)
        XCTAssertFalse(model.hasCurrentMoment)
        XCTAssertTrue(model.bubbles.isEmpty)
        let proactiveCalls = await api.proactiveCalls
        XCTAssertEqual(proactiveCalls, 0)
    }

    func testKeyboardAndButtonGateCannotCreateTwoMoments() async throws {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "同一刻"

        model.submit()
        model.submit()
        try await waitUntil { model.phase == .complete }

        let keys = await api.idempotencyKeys
        XCTAssertEqual(keys.count, 1)
        XCTAssertEqual(model.bubbles.map(\.text), ["reply-1"])
    }

    func testASecondLineCanBeSaidWhileMurmurIsStillAnsweringTheFirst() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(
            api: api,
            transcriptStore: MurmurTranscriptStore(directory: directory),
            bubblePacing: .instant
        )
        await model.bootstrap()

        model.draftText = "第一句"
        model.submit()
        // The composer stays open while the first moment is still in flight.
        XCTAssertTrue(model.isAwaitingReply)
        model.draftText = "第二句"
        XCTAssertTrue(model.canSubmit)
        model.submit()

        try await waitUntil { model.messages.count == 4 }
        // Both lines are on screen the moment they are said; the replies come
        // after, in the order the moments were queued.
        XCTAssertEqual(model.messages.map(\.text), ["第一句", "第二句", "reply-1", "reply-2"])
        XCTAssertEqual(model.messages[0].delivery, .answered)
        XCTAssertEqual(model.messages[1].delivery, .answered)
        // Queued, not overlapped: the server still sees one moment at a time.
        let keys = await api.idempotencyKeys
        XCTAssertEqual(keys.count, 2)
        XCTAssertEqual(Set(keys).count, 2)
        try await waitUntil { !model.isAwaitingReply }
    }

    func testAPhotoCanBeChosenWhileMurmurIsStillAnswering() async throws {
        let api = FakeMurmurAPIClient(mode: .neverStreams)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "先说一句"
        model.submit()
        try await waitUntil { model.phase == .responding }

        let source = try writeTestJPEG(size: CGSize(width: 640, height: 480), name: "while-answering")
        model.preparePhoto(at: source)
        try await waitUntil { model.draftPhoto != nil }

        // The reply landing must not throw away a photo still being decoded,
        // and the photo must not silently cancel the reply.
        XCTAssertFalse(model.isPreparingPhoto)
        XCTAssertTrue(model.canSubmit)
        XCTAssertTrue(model.isAwaitingReply)

        model.clearCurrent()
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertTrue(murmurTemporaryFiles().isEmpty)
    }

    func testBubblesAreHeldBackToAHumanRhythm() async throws {
        let api = FakeMurmurAPIClient(mode: .orderedBubbles)
        let model = MurmurSessionModel(
            api: api,
            bubblePacing: MurmurBubblePacing(perCharacter: 0.02, minimum: 0.08, maximum: 0.2)
        )
        await model.bootstrap()
        model.draftText = "两句"

        let start = ContinuousClock.now
        model.submit()
        try await waitUntil { model.phase == .complete }
        let elapsed = start.duration(to: ContinuousClock.now)

        // The fake streams both bubbles at once; each still waits its turn.
        XCTAssertGreaterThan(elapsed, .milliseconds(130))
        XCTAssertEqual(model.bubbles.map(\.text), ["先这一句", "再这一句"])
    }

    func testPacingScalesWithLengthAndStaysWithinItsBounds() {
        let pacing = MurmurBubblePacing.human
        XCTAssertEqual(pacing.delay(for: "好"), pacing.minimum, accuracy: 0.0001)
        XCTAssertEqual(
            pacing.delay(for: String(repeating: "字", count: 20)),
            20 * pacing.perCharacter,
            accuracy: 0.0001
        )
        XCTAssertEqual(pacing.delay(for: String(repeating: "字", count: 500)), pacing.maximum, accuracy: 0.0001)
        XCTAssertEqual(MurmurBubblePacing.instant.delay(for: "任何长度"), 0, accuracy: 0.0001)
    }

    func testRetryReusesTheFailedBubbleInsteadOfSayingItTwice() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = FakeMurmurAPIClient(mode: .terminalFailureThenSuccess)
        let model = MurmurSessionModel(
            api: api,
            transcriptStore: MurmurTranscriptStore(directory: directory),
            bubblePacing: .instant
        )
        await model.bootstrap()
        model.draftText = "重发"

        model.submit()
        try await waitUntil { model.phase == .error }
        XCTAssertEqual(model.messages.filter { $0.text == "重发" }.count, 1)
        let failed = try XCTUnwrap(model.messages.first)
        XCTAssertEqual(failed.delivery, .failed)
        // The row itself carries the mark and the offer — nothing global does.
        XCTAssertEqual(model.sendFailures[failed.id]?.canResend, true)

        model.resend(failed.id)
        try await waitUntil { model.phase == .complete }
        XCTAssertEqual(model.messages.filter { $0.text == "重发" }.count, 1)
        XCTAssertNotEqual(model.messages.first?.delivery, .failed)
        XCTAssertNil(model.sendFailures[failed.id])
    }

    func testOnlyTheFailedRowIsMarkedWhenALaterSendSucceeds() async throws {
        let api = FakeMurmurAPIClient(mode: .terminalFailureThenSuccess)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()

        model.draftText = "第一条"
        model.submit()
        try await waitUntil { model.phase == .error }
        let failed = try XCTUnwrap(model.messages.first)

        model.draftText = "第二条"
        model.submit()
        try await waitUntil { model.phase == .complete }

        // The second line succeeded and must not inherit the first one's mark,
        // and the first one must not lose it.
        let succeeded = try XCTUnwrap(model.messages.first { $0.text == "第二条" })
        XCTAssertEqual(model.messages.first { $0.id == failed.id }?.delivery, .failed)
        XCTAssertNotNil(model.sendFailures[failed.id])
        XCTAssertNil(model.sendFailures[succeeded.id])
    }

    func testProactiveMessageJoinsTheTranscript() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = FakeMurmurAPIClient(mode: .proactiveReply)
        let model = MurmurSessionModel(
            api: api,
            transcriptStore: MurmurTranscriptStore(directory: directory),
            bubblePacing: .instant
        )
        await model.bootstrap()

        await model.handleNotification(momentID: "proactive-1")

        // Without this the message only ever existed inside the notification.
        XCTAssertEqual(model.messages.map(\.text), ["想到你了"])
        XCTAssertEqual(model.messages.first?.author, .murmur)
        // Arriving twice must not say it twice.
        await model.handleNotification(momentID: "proactive-1")
        XCTAssertEqual(model.messages.count, 1)
    }

    func testCheckProactivePollsWithoutANotification() async throws {
        let api = FakeMurmurAPIClient(mode: .proactiveReply)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()

        await model.checkProactive()

        let proactiveCalls = await api.proactiveCalls
        XCTAssertEqual(proactiveCalls, 1)
        XCTAssertEqual(model.messages.map(\.text), ["想到你了"])
        let acknowledgements = await api.acknowledgements
        XCTAssertEqual(acknowledgements.map(\.momentID), ["proactive-1"])
    }

    func testCancellingMarksStrandedSendsFailedRatherThanLeavingThemSpinning() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = FakeMurmurAPIClient(mode: .neverStreams)
        let model = MurmurSessionModel(
            api: api,
            transcriptStore: MurmurTranscriptStore(directory: directory),
            bubblePacing: .instant
        )
        await model.bootstrap()
        model.draftText = "在路上"
        model.submit()
        model.draftText = "还在排队"
        model.submit()
        try await waitUntil { model.phase == .responding }

        model.cancelCurrentOperation()

        // The first one reached the server, so it keeps its tick; only the one
        // that never left is called failed.
        XCTAssertEqual(model.messages.map(\.delivery), [.sent, .failed])
        XCTAssertFalse(model.isAwaitingReply)
        // Cancelling is deliberate, so the mark states what happened without
        // offering to undo the person's own decision.
        let stranded = try XCTUnwrap(model.messages.last)
        XCTAssertEqual(model.sendFailures[stranded.id]?.canResend, false)
        XCTAssertEqual(model.sendFailures[stranded.id]?.message, "已取消发送。")
    }

    func testRetryReusesIdempotencyKeyAndDoesNotDuplicateBubbles() async throws {
        let api = FakeMurmurAPIClient(mode: .streamFailsOnce)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "重试"

        model.submit()
        try await waitUntil { model.phase == .error }
        model.resend(try XCTUnwrap(model.messages.first).id)
        try await waitUntil { model.phase == .complete }

        let keys = await api.idempotencyKeys
        XCTAssertEqual(keys.count, 2)
        XCTAssertEqual(Set(keys).count, 1)
        XCTAssertEqual(model.bubbles.map(\.text), ["reply-after-retry"])
    }

    func testAutomaticSSEReconnectCarriesLastEventID() async throws {
        let api = FakeMurmurAPIClient(mode: .disconnectThenResume)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "继续"

        model.submit()
        try await waitUntil { model.phase == .complete }

        let lastIDs = await api.lastEventIDs
        XCTAssertEqual(lastIDs, [nil, "bubble-1"])
        XCTAssertEqual(model.bubbles.map(\.text), ["先说一半"])
    }

    func testProcessingFailureRetryReusesOriginalIdempotencyKey() async throws {
        let api = FakeMurmurAPIClient(mode: .terminalFailureThenSuccess)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "重新处理"

        model.submit()
        try await waitUntil { model.phase == .error }
        model.resend(try XCTUnwrap(model.messages.first).id)
        try await waitUntil { model.phase == .complete }

        let keys = await api.idempotencyKeys
        XCTAssertEqual(keys.count, 2)
        XCTAssertEqual(Set(keys).count, 1)
        XCTAssertEqual(model.bubbles.map(\.text), ["processed-again"])
    }

    func testIdempotencyConflictIsNotRetryable() async throws {
        let api = FakeMurmurAPIClient(mode: .idempotencyConflict)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "不同内容"

        model.submit()
        try await waitUntil { model.phase == .error }
        let failed = try XCTUnwrap(model.messages.first)
        // The row is marked, but the mark is not a button: pressing it must do
        // nothing rather than send the same key with different content again.
        XCTAssertEqual(model.sendFailures[failed.id]?.canResend, false)
        model.resend(failed.id)

        XCTAssertEqual(model.failure?.code, "idempotency_conflict")
        XCTAssertEqual(model.failure?.retryable, false)
        let keys = await api.idempotencyKeys
        XCTAssertEqual(keys.count, 1)
    }

    func testNewMomentReplacesRatherThanAppendsHistory() async throws {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "第一刻"
        model.submit()
        try await waitUntil { model.phase == .complete }

        model.draftText = "第二刻"
        model.submit()
        try await waitUntil { model.phase == .complete && model.currentNote == "第二刻" }

        XCTAssertEqual(model.currentNote, "第二刻")
        XCTAssertEqual(model.bubbles.map(\.text), ["reply-2"])
    }

    func testTimeoutBecomesRetryableError() async throws {
        let api = FakeMurmurAPIClient(mode: .neverCreates)
        let model = MurmurSessionModel(api: api, requestTimeoutSeconds: 0.05, uploadTimeoutSeconds: 0.05, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "超时"

        model.submit()
        try await waitUntil { model.phase == .error }

        XCTAssertEqual(model.failure?.code, "timeout")
        XCTAssertEqual(model.failure?.retryable, true)
    }

    func testSlowUploadUsesDedicatedLongerTimeout() async throws {
        let api = FakeMurmurAPIClient(mode: .slowCreates)
        let model = MurmurSessionModel(
            api: api,
            requestTimeoutSeconds: 0.02,
            uploadTimeoutSeconds: 0.25,
            bubblePacing: .instant
        )
        await model.bootstrap()
        model.draftText = "慢速移动网络"

        model.submit()
        try await waitUntil { model.phase == .complete }

        XCTAssertNil(model.failure)
        XCTAssertEqual(model.bubbles.map(\.text), ["reply-1"])
    }

    func testCancelLeavesCurrentMomentWithoutSubmittingAgain() async throws {
        let api = FakeMurmurAPIClient(mode: .neverStreams)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "取消"
        model.submit()
        try await waitUntil { model.phase == .responding }

        model.cancelCurrentOperation()

        XCTAssertEqual(model.phase, .ready)
        let keys = await api.idempotencyKeys
        XCTAssertEqual(keys.count, 1)
    }

    func testRemovingCurrentDeviceReturnsToEnrollment() async throws {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        await model.refreshDevices()
        let current = try XCTUnwrap(model.devices.first { $0.id == "test-device" })

        await model.removeDevice(current)

        XCTAssertNil(model.identity)
        XCTAssertEqual(model.connection, .needsEnrollment)
        let removed = await api.removedDeviceIDs
        XCTAssertEqual(removed, ["test-device"])
    }

    func testUnknownAttestationKeyOffersExplicitLocalReconnect() async throws {
        let api = FakeMurmurAPIClient(mode: .attestationKeyUnknown)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)

        await model.bootstrap()

        XCTAssertNotNil(model.identity)
        XCTAssertTrue(model.requiresDeviceReconnect)
        XCTAssertEqual(model.connection.label, "连接异常")
        await model.resetLocalDeviceIdentity()
        XCTAssertNil(model.identity)
        XCTAssertEqual(model.connection, .needsEnrollment)
        let resetCalls = await api.resetLocalIdentityCalls
        XCTAssertEqual(resetCalls, 1)
    }

    func testNotificationReplyAcknowledgesProactiveAndCreatesNewMoment() async throws {
        let api = FakeMurmurAPIClient(mode: .proactiveReply)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        await model.handleNotification(momentID: "proactive-1")
        model.draftText = "我看见了"

        model.submit()
        try await waitUntil { model.phase == .complete }

        let acknowledgements = await api.acknowledgements
        XCTAssertEqual(acknowledgements.count, 2)
        XCTAssertEqual(acknowledgements[0].momentID, "proactive-1")
        XCTAssertNil(acknowledgements[0].reply)
        XCTAssertEqual(acknowledgements[1].momentID, "proactive-1")
        XCTAssertEqual(acknowledgements[1].reply, "我看见了")
        XCTAssertEqual(model.bubbles.map(\.text), ["reply-1"])
    }

    func testSuccessfulMomentDeletesOriginalPhotoButKeepsPreview() async throws {
        let source = FileManager.default.temporaryDirectory
            .appendingPathComponent("session-photo-test-\(UUID().uuidString).jpg")
        defer { try? FileManager.default.removeItem(at: source) }
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 32, height: 32))
        let image = renderer.image { context in
            UIColor.systemCoralForTest.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 32, height: 32))
        }
        try XCTUnwrap(image.jpegData(compressionQuality: 0.8)).write(to: source)
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()

        model.preparePhoto(at: source)
        try await waitUntil { model.phase == .ready }
        let managedOriginal = try XCTUnwrap(model.draftPhoto?.originalURL)
        model.submit()
        try await waitUntil { model.phase == .complete }

        XCTAssertFalse(FileManager.default.fileExists(atPath: managedOriginal.path))
        XCTAssertNotNil(model.currentPhoto?.preview)
    }

    func testBootstrapLoadsServerPreferences() async throws {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        XCTAssertEqual(model.preferences.dailyFrequency, 2)
        XCTAssertEqual(model.preferences.quietStart, "21:00")
    }

    func testStateMachineWalksIdleToComplete() async throws {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        XCTAssertEqual(model.phase, .idle)
        model.beginPhotoSelection()
        XCTAssertEqual(model.phase, .preparingPhoto)
        model.removeDraftPhoto()
        XCTAssertEqual(model.phase, .idle)
        model.draftText = "此刻"
        XCTAssertTrue(model.canSubmit)
        model.submit()
        try await waitUntil { model.phase == .complete }
        XCTAssertEqual(model.phase, .complete)
        XCTAssertEqual(model.bubbles.map(\.text), ["reply-1"])
    }

    func testRemovingPhotoDuringPrepareLeavesIdle() async {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.beginPhotoSelection()
        XCTAssertEqual(model.phase, .preparingPhoto)
        XCTAssertTrue(model.phase.isBusy)
        model.removeDraftPhoto()
        XCTAssertEqual(model.phase, .idle)
        XCTAssertFalse(model.phase.isBusy)
    }

    func testPhotoSelectionFailureBelongsToTheDraftNotTheTranscript() async {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.beginPhotoSelection()
        model.failPhotoSelection()
        XCTAssertEqual(model.phase, .error)
        XCTAssertEqual(model.failure?.retryable, false)
        // Nothing was ever sent, so there is no row to mark; the composer says
        // it instead, and the next attempt takes the line away again.
        XCTAssertEqual(model.draftFailure, "没有读取到这张图片。")
        XCTAssertTrue(model.sendFailures.isEmpty)
        model.beginPhotoSelection()
        XCTAssertNil(model.draftFailure)
    }

    func testSSEPreservesBubbleOrder() async throws {
        let api = FakeMurmurAPIClient(mode: .orderedBubbles)
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.draftText = "两句"
        model.submit()
        try await waitUntil { model.phase == .complete }
        XCTAssertEqual(model.bubbles.map(\.text), ["先这一句", "再这一句"])
    }

    func testFailureMappingCoversTimeoutAndAttestation() {
        let timeout = MurmurFailure.from(URLError(.timedOut))
        XCTAssertEqual(timeout.code, "timeout")
        XCTAssertTrue(timeout.retryable)
        let cancelled = MurmurFailure.from(CancellationError())
        XCTAssertEqual(cancelled.code, "cancelled")
        let attest = MurmurFailure.from(
            NSError(domain: DCErrorDomain, code: DCError.invalidKey.rawValue)
        )
        XCTAssertEqual(attest.code, "app_attest_invalid_key")
        XCTAssertTrue(attest.requiresDeviceReconnect)
        let unavailable = MurmurFailure.from(
            NSError(domain: DCErrorDomain, code: DCError.serverUnavailable.rawValue)
        )
        XCTAssertEqual(unavailable.code, "app_attest_server_unavailable")
        XCTAssertTrue(unavailable.retryable)
        for code in [URLError.notConnectedToInternet, .networkConnectionLost, .cannotFindHost, .cannotConnectToHost] {
            let mapped = MurmurFailure.from(URLError(code))
            XCTAssertEqual(mapped.code, "network_error")
            XCTAssertTrue(mapped.retryable)
        }
        XCTAssertEqual(MurmurFailure.fromHTTPStatus(401).code, "http_401")
        XCTAssertFalse(MurmurFailure.fromHTTPStatus(401).retryable)
        XCTAssertTrue(MurmurFailure.fromHTTPStatus(408).retryable)
        XCTAssertTrue(MurmurFailure.fromHTTPStatus(429).retryable)
        XCTAssertTrue(MurmurFailure.fromHTTPStatus(503).retryable)
        XCTAssertFalse(MurmurFailure.fromHTTPStatus(400).retryable)
        XCTAssertEqual(
            MurmurFailure(code: "stream_ended", message: "回应中断了。", retryable: true).retryable,
            true
        )
        XCTAssertFalse(MurmurFailure(code: "image_too_large", message: "图片不能超过 25 MB。", retryable: false).retryable)
        XCTAssertFalse(MurmurFailure(code: "empty_image", message: "这张图片是空的。", retryable: false).retryable)
        XCTAssertFalse(MurmurFailure(code: "invalid_image", message: "无法读取这张图片。", retryable: false).retryable)
        XCTAssertFalse(MurmurFailure(code: "photo_unavailable", message: "没有读取到这张图片。", retryable: false).retryable)
    }

    func testCancelDuringPhotoPrepareDiscardsFileAndIgnoresLateResult() async throws {
        let source = try writeTestJPEG(size: CGSize(width: 1_200, height: 900), name: "cancel-prepare")
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        model.preparePhoto(at: source)
        XCTAssertEqual(model.phase, .preparingPhoto)
        model.cancelCurrentOperation()
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertNil(model.draftPhoto)
        XCTAssertNotEqual(model.phase, .ready)
        XCTAssertFalse(FileManager.default.fileExists(atPath: source.path))
        let leftover = murmurTemporaryFiles()
        XCTAssertTrue(leftover.isEmpty, "leftover photo files: \(leftover)")
    }

    func testPreparingPhotoPhaseIsEnteredWithinOneHundredMilliseconds() async {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        let start = ContinuousClock.now
        model.beginPhotoSelection()
        let elapsed = start.duration(to: ContinuousClock.now)
        XCTAssertEqual(model.phase, .preparingPhoto)
        XCTAssertLessThan(elapsed, .milliseconds(100))
    }

    func testPreparePhotoDoesNotBlockBeforeBackgroundDecodeFinishes() async throws {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        let source = try writeTestJPEG(size: CGSize(width: 2_400, height: 1_800), name: "nonblocking-prepare")
        let start = ContinuousClock.now
        model.preparePhoto(at: source)
        XCTAssertEqual(model.phase, .preparingPhoto)
        XCTAssertLessThan(start.duration(to: ContinuousClock.now), .milliseconds(100))
        try await waitUntil { model.phase == .ready }
        XCTAssertNotNil(model.draftPhoto)
    }

    func testThirtyCompletedMomentsLeaveNoHistoryOrTemporaryFiles() async throws {
        let api = FakeMurmurAPIClient()
        let model = MurmurSessionModel(api: api, bubblePacing: .instant)
        await model.bootstrap()
        for index in 1...30 {
            let source = try writeTestJPEG(size: CGSize(width: 64, height: 64), name: "moment-\(index)")
            model.preparePhoto(at: source)
            try await waitUntil { model.phase == .ready }
            model.draftText = "第\(index)刻"
            model.submit()
            try await waitUntil { model.phase == .complete }
        }
        XCTAssertEqual(model.bubbles.count, 1)
        XCTAssertEqual(model.currentNote, "第30刻")
        XCTAssertFalse(FileManager.default.fileExists(atPath: model.currentPhoto?.originalURL.path ?? ""))
        let leftover = murmurTemporaryFiles()
        XCTAssertTrue(leftover.isEmpty, "leftover photo files: \(leftover)")
    }

    func testPushSyncSkipsAllowedTokenUntilItArrives() {
        XCTAssertFalse(MurmurNotificationBridge.shouldSyncToken(nil, authorization: .unknown))
        XCTAssertFalse(MurmurNotificationBridge.shouldSyncToken(nil, authorization: .allowed))
        XCTAssertTrue(MurmurNotificationBridge.shouldSyncToken("abc", authorization: .allowed))
        XCTAssertTrue(MurmurNotificationBridge.shouldSyncToken(nil, authorization: .denied))
        XCTAssertTrue(MurmurNotificationBridge.shouldSyncToken(nil, authorization: .notDetermined))
    }

    private func writeTestJPEG(size: CGSize, name: String) throws -> URL {
        let renderer = UIGraphicsImageRenderer(size: size)
        let image = renderer.image { context in
            UIColor.systemCoralForTest.setFill()
            context.fill(CGRect(origin: .zero, size: size))
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("murmur-upload-\(name)-\(UUID().uuidString).jpg")
        try XCTUnwrap(image.jpegData(compressionQuality: 0.8)).write(to: url)
        return url
    }

    private func murmurTemporaryFiles() -> [String] {
        let prefixes = ["murmur-picker-", "murmur-upload-", "murmur-camera-", "murmur-multipart-"]
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: FileManager.default.temporaryDirectory,
            includingPropertiesForKeys: nil
        )) ?? []
        return urls
            .map(\.lastPathComponent)
            .filter { name in prefixes.contains(where: name.hasPrefix) }
    }

    private func waitUntil(
        timeout: Duration = .seconds(2),
        condition: @escaping @MainActor () -> Bool
    ) async throws {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: timeout)
        while !condition() {
            if clock.now >= deadline { XCTFail("Timed out waiting for state"); return }
            try await Task.sleep(for: .milliseconds(10))
        }
    }
}

private actor FakeMurmurAPIClient: MurmurAPIClient {
    enum Mode: Sendable {
        case normal, streamFailsOnce, disconnectThenResume, terminalFailureThenSuccess
        case idempotencyConflict, proactiveReply, attestationKeyUnknown, slowCreates, neverCreates, neverStreams
        case orderedBubbles
    }

    private let mode: Mode
    private var createCount = 0
    private var streamCount = 0
    private(set) var idempotencyKeys: [String] = []
    private(set) var lastEventIDs: [String?] = []
    private(set) var proactiveCalls = 0
    private(set) var removedDeviceIDs: [String] = []
    private(set) var acknowledgements: [(momentID: String, reply: String?)] = []
    private(set) var resetLocalIdentityCalls = 0
    private(set) var deviceTokens: [String?] = []
    private var storedPreferences = MurmurPreferences(dailyFrequency: 2, quietStart: "21:00", quietEnd: "09:00")

    init(mode: Mode = .normal) { self.mode = mode }

    func storedIdentity() async throws -> MurmurIdentity? {
        .init(userID: "test-user", deviceID: "test-device", keyID: "test-key")
    }

    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity {
        .init(userID: "test-user", deviceID: "test-device", keyID: "test-key")
    }

    func createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String, intent: MurmurMomentIntent?) async throws -> MomentReceipt {
        idempotencyKeys.append(idempotencyKey)
        createCount += 1
        if mode == .idempotencyConflict {
            throw MurmurFailure(
                code: "idempotency_conflict",
                message: "幂等键与不同内容冲突。",
                retryable: false
            )
        }
        if mode == .neverCreates {
            try await Task.sleep(for: .seconds(10))
        }
        if mode == .slowCreates {
            try await Task.sleep(for: .milliseconds(90))
        }
        return .init(momentID: "moment-\(createCount)", status: "queued")
    }

    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        lastEventIDs.append(lastEventID)
        streamCount += 1
        let call = streamCount
        let currentCreateCount = createCount
        return AsyncThrowingStream { continuation in
            switch mode {
            case .normal, .proactiveReply, .slowCreates:
                continuation.yield(.accepted(id: "accepted-\(currentCreateCount)"))
                continuation.yield(.bubble(id: "bubble-\(currentCreateCount)", text: "reply-\(currentCreateCount)"))
                continuation.yield(.done(id: "done-\(currentCreateCount)", move: nil, scene: nil))
                continuation.finish()
            case .orderedBubbles:
                continuation.yield(.accepted(id: "accepted-1"))
                continuation.yield(.bubble(id: "bubble-a", text: "先这一句"))
                continuation.yield(.bubble(id: "bubble-b", text: "再这一句"))
                continuation.yield(.done(id: "done-1", move: "speak", scene: nil))
                continuation.finish()
            case .streamFailsOnce:
                if call <= 3 {
                    continuation.yield(.bubble(id: "partial", text: "partial"))
                    continuation.finish(throwing: MurmurFailure(code: "stream_lost", message: "lost", retryable: true))
                } else {
                    continuation.yield(.bubble(id: "retry", text: "reply-after-retry"))
                    continuation.yield(.done(id: "done", move: nil, scene: nil))
                    continuation.finish()
                }
            case .disconnectThenResume:
                if call == 1 {
                    continuation.yield(.bubble(id: "bubble-1", text: "先说一半"))
                    continuation.finish(throwing: MurmurFailure(code: "stream_lost", message: "lost", retryable: true))
                } else {
                    continuation.yield(.bubble(id: "bubble-1", text: "先说一半"))
                    continuation.yield(.done(id: "done-1", move: nil, scene: nil))
                    continuation.finish()
                }
            case .terminalFailureThenSuccess:
                if currentCreateCount == 1 {
                    continuation.yield(.failure(
                        id: "failed",
                        .init(code: "processing_failed", message: "failed", retryable: true)
                    ))
                    continuation.finish()
                } else {
                    continuation.yield(.bubble(id: "retry", text: "processed-again"))
                    continuation.yield(.done(id: "done", move: nil, scene: nil))
                    continuation.finish()
                }
            case .neverStreams:
                break
            case .neverCreates, .idempotencyConflict, .attestationKeyUnknown:
                continuation.finish()
            }
        }
    }

    func currentProactive() async throws -> ProactiveMoment? {
        proactiveCalls += 1
        guard mode == .proactiveReply else { return nil }
        return .init(momentID: "proactive-1", bubbles: ["想到你了"], text: nil, move: nil, scene: nil)
    }
    func acknowledge(momentID: String, reply: String?) async throws {
        acknowledgements.append((momentID, reply))
    }
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws {
        deviceTokens.append(apnsToken)
    }
    func devices() async throws -> [MurmurDevice] {
        if mode == .attestationKeyUnknown {
            throw MurmurFailure(
                code: "attestation_key_unknown",
                message: "Device binding is unknown.",
                retryable: false
            )
        }
        return [
            .init(
                id: "test-device",
                keyID: "test-key",
                environment: "development",
                timezone: "Asia/Shanghai",
                deviceName: "iPhone · iOS 18",
                pushEnabled: true,
                lastSeenAt: nil,
                createdAt: nil
            ),
            .init(
                id: "other-device",
                keyID: "other-key",
                environment: "development",
                timezone: "Asia/Shanghai",
                deviceName: "iPad · iOS 18",
                pushEnabled: false,
                lastSeenAt: nil,
                createdAt: nil
            )
        ]
    }
    func removeDevice(deviceID: String) async throws { removedDeviceIDs.append(deviceID) }
    func preferences() async throws -> MurmurPreferences { storedPreferences }
    func updatePreferences(_ preferences: MurmurPreferences) async throws { storedPreferences = preferences }
    func resetLocalIdentity() async throws { resetLocalIdentityCalls += 1 }
    func deleteAccount() async throws {}
}

private extension UIColor {
    static let systemCoralForTest = UIColor(red: 0.95, green: 0.35, blue: 0.30, alpha: 1)
}
