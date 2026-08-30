import AVFoundation
import XCTest
@testable import Murmur

// MARK: - Doubles

@MainActor
final class FakeAudioPlayerEngine: AudioPlayerEngine {
    var onStatus: ((AudioPlayerStatus) -> Void)?
    var onTime: ((TimeInterval) -> Void)?
    var duration: TimeInterval?

    private(set) var loadedURLs: [URL] = []
    private(set) var commands: [String] = []

    func load(url: URL) {
        loadedURLs.append(url)
        commands.append("load")
    }

    // `AVPlayer` calls back from inside these, so the double does too: without
    // it the controller's own report and the engine's callback would both land
    // and nothing would notice the transition being reported twice.
    func play() {
        commands.append("play")
        onStatus?(.playing)
    }

    func pause() {
        commands.append("pause")
        onStatus?(.paused)
    }
    func seek(to seconds: TimeInterval) { commands.append("seek(\(Int(seconds)))") }
    func stop() { commands.append("stop") }

    /// Drive the controller the way the real player would.
    func emit(_ status: AudioPlayerStatus) { onStatus?(status) }
}

actor StubStreamResolver: MusicStreamResolving {
    private(set) var resolvedIDs: [String] = []
    private var error: Error?
    private var track: MusicTrackAttachmentV1?
    /// Fails this many times before succeeding, so a retry can be observed.
    private var failuresRemaining = 0

    init(track: MusicTrackAttachmentV1? = nil, error: Error? = nil, failuresRemaining: Int = 0) {
        self.track = track
        self.error = error
        self.failuresRemaining = failuresRemaining
    }

    func resolveStream(trackID: String) async throws -> ResolvedMusicStream {
        resolvedIDs.append(trackID)
        if failuresRemaining > 0 {
            failuresRemaining -= 1
            throw error ?? URLError(.timedOut)
        }
        if let error, failuresRemaining == 0, track == nil { throw error }
        // Answer for the id that was asked for. Handing back one fixed track
        // would quietly make "play a different song" untestable.
        let resolved = track ?? MusicFixtures.catalogued(trackID)
        return ResolvedMusicStream(
            track: resolved,
            url: URL(string: "https://api.audius.co/v1/tracks/\(trackID)/stream")!
        )
    }
}

actor RecordingPlaybackReporter: MusicPlaybackReporting {
    private(set) var events: [MusicPlaybackEvent] = []
    private(set) var recoveries = 0

    func record(_ event: MusicPlaybackEvent) async { events.append(event) }
    func recoverInterruptedSession() async { recoveries += 1 }
    func flush() async {}

    var states: [MusicPlaybackReportState] { events.map(\.state) }
    var sequences: [Int] { events.map(\.sequence) }
    var sessions: [UUID] { events.map(\.sessionID) }
}

@MainActor
final class StubAudioSession: MusicAudioSessioning {
    private(set) var activations = 0
    private(set) var deactivations = 0
    var activationError: Error?

    func activate() throws {
        if let activationError { throw activationError }
        activations += 1
    }

    func deactivate() { deactivations += 1 }
}

enum MusicFixtures {
    static let track = MusicTrackAttachmentV1(
        trackID: "night-sail",
        title: "夜航",
        artists: ["林一"],
        artworkURL: URL(string: "https://images.audius.co/night-sail.jpg"),
        canonicalURL: URL(string: "https://audius.co/lin/night-sail")!,
        durationSeconds: 201,
        explicit: false
    )

    static let other = MusicTrackAttachmentV1(
        trackID: "harbour",
        title: "港",
        artists: ["小野"],
        artworkURL: nil,
        canonicalURL: URL(string: "https://audius.co/ono/harbour")!,
        durationSeconds: 150,
        explicit: false
    )

    /// What the provider would hand back for a given id.
    static func catalogued(_ trackID: String) -> MusicTrackAttachmentV1 {
        [track, other].first { $0.trackID == trackID } ?? track
    }
}

// MARK: - The player

@MainActor
final class MusicPlaybackControllerTests: XCTestCase {
    private var engine: FakeAudioPlayerEngine!
    private var reporter: RecordingPlaybackReporter!
    private var session: StubAudioSession!

    override func setUp() async throws {
        engine = FakeAudioPlayerEngine()
        reporter = RecordingPlaybackReporter()
        session = StubAudioSession()
    }

    private func makeController(
        resolver: StubStreamResolver = StubStreamResolver()
    ) -> MusicPlaybackController {
        MusicPlaybackController(
            engine: engine, resolver: resolver, reporter: reporter, session: session
        )
    }

    /// Wait for the resolve to hand a *new* URL to the engine.
    ///
    /// Counting rather than checking for emptiness matters on the second play:
    /// a URL from the first song is already recorded, so "not empty" would
    /// return before the song under test had been resolved at all.
    private func awaitLoad(after previous: Int) async {
        for _ in 0..<40 {
            await Task.yield()
            if engine.loadedURLs.count > previous { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
    }

    private func startPlaying(
        _ track: MusicTrackAttachmentV1 = MusicFixtures.track,
        on controller: MusicPlaybackController
    ) async {
        let loadsBefore = engine.loadedURLs.count
        controller.play(track)
        await awaitLoad(after: loadsBefore)
        engine.emit(.playing)
        await Task.yield()
    }

    /// Reports are handed to the reporter from a task of their own, so reading
    /// them straight after the call that caused them is a race. Wait for the
    /// expected number to land — then keep waiting a moment longer, so a report
    /// that should not exist gets its chance to appear and fail the assertion.
    private func reportedStates(
        _ expected: Int, file: StaticString = #filePath, line: UInt = #line
    ) async throws -> [MusicPlaybackReportState] {
        try await waitFor(file: file, line: line) {
            await self.reporter.events.count >= expected
        }
        try await Task.sleep(for: .milliseconds(30))
        return await reporter.states
    }

    // ---- nothing happens on its own ------------------------------------

    func testNothingPlaysUntilSomebodyAsks() async throws {
        let controller = makeController()
        XCTAssertEqual(controller.state, .idle)
        XCTAssertNil(controller.track)
        XCTAssertTrue(engine.commands.isEmpty)
        let events = await reporter.events
        XCTAssertTrue(events.isEmpty)
    }

    func testAProcessRestartClosesTheLastSessionRatherThanResuming() async throws {
        _ = makeController()
        try await waitFor { await self.reporter.recoveries == 1 }
        XCTAssertTrue(engine.commands.isEmpty, "启动不许自己开始放")
    }

    // ---- one transition, one report ------------------------------------

    func testStartingReportsStartedExactlyOnce() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        XCTAssertEqual(controller.state, .playing)
        let states = try await reportedStates(1)
        XCTAssertEqual(states, [.started])
    }

    func testPauseAndResumeEachReportOnce() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.pause()
        controller.resume()
        let states = try await reportedStates(3)
        XCTAssertEqual(states, [.started, .paused, .resumed])
    }

    func testARepeatedPauseIsNotReportedTwice() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.pause()
        controller.pause()
        let states = try await reportedStates(2)
        XCTAssertEqual(states, [.started, .paused])
    }

    func testFinishingReportsCompleted() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        engine.emit(.ended)
        XCTAssertEqual(controller.state, .ended)
        let states = try await reportedStates(2)
        XCTAssertEqual(states, [.started, .completed])
    }

    func testStoppingReportsStoppedAndReleasesTheAudioSession() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.stop()
        XCTAssertEqual(controller.state, .idle)
        XCTAssertNil(controller.track)
        let states = try await reportedStates(2)
        XCTAssertEqual(states, [.started, .stopped])
        XCTAssertEqual(session.deactivations, 1)
    }

    // ---- sequence and session ------------------------------------------

    func testSequenceOnlyEverGoesUpInsideOneSession() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.pause()
        controller.resume()
        controller.pause()
        _ = try await reportedStates(4)
        let sequences = await reporter.sequences
        XCTAssertEqual(sequences, [1, 2, 3, 4])
        let sessions = await reporter.sessions
        XCTAssertEqual(Set(sessions).count, 1)
    }

    func testEachLoadIsItsOwnSession() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        await startPlaying(MusicFixtures.other, on: controller)
        _ = try await reportedStates(3)
        let sessions = await reporter.sessions
        XCTAssertEqual(sessions.count, 3, "旧的要先关掉，新的才开始")
        XCTAssertEqual(sessions[0], sessions[1], "stopped 属于旧的那次")
        XCTAssertNotEqual(sessions[1], sessions[2])
    }

    // ---- one song at a time ---------------------------------------------

    func testStartingAnotherSongStopsTheFirst() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        await startPlaying(MusicFixtures.other, on: controller)
        let states = try await reportedStates(3)
        XCTAssertEqual(states, [.started, .stopped, .started])
        XCTAssertEqual(controller.track?.trackID, MusicFixtures.other.trackID)
    }

    func testTappingTheSongThatIsPlayingPausesIt() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.tap(MusicFixtures.track)
        XCTAssertEqual(controller.state, .paused)
        XCTAssertEqual(engine.loadedURLs.count, 1, "暂停不该把歌重新取一遍")
        let states = try await reportedStates(2)
        XCTAssertEqual(states, [.started, .paused])
    }

    func testTappingThePausedSongResumesIt() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.tap(MusicFixtures.track)
        controller.tap(MusicFixtures.track)
        XCTAssertEqual(controller.state, .playing)
        XCTAssertEqual(engine.loadedURLs.count, 1)
    }

    func testTappingADifferentSongStartsIt() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.tap(MusicFixtures.other)
        await awaitLoad(after: 1)
        XCTAssertEqual(controller.track?.trackID, MusicFixtures.other.trackID)
    }

    func testTappingThePausedSongAgainResumesItRatherThanReloading() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.pause()
        controller.play(MusicFixtures.track)
        XCTAssertEqual(controller.state, .playing)
        XCTAssertEqual(engine.loadedURLs.count, 1, "同一首不该重新解析")
        let states = try await reportedStates(3)
        XCTAssertEqual(states, [.started, .paused, .resumed])
    }

    // ---- every play re-resolves -----------------------------------------

    func testEveryPlayAsksTheProviderAgain() async throws {
        let resolver = StubStreamResolver()
        let controller = makeController(resolver: resolver)
        await startPlaying(on: controller)
        controller.stop()
        await startPlaying(on: controller)
        let ids = await resolver.resolvedIDs
        XCTAssertEqual(ids, ["night-sail", "night-sail"])
    }

    func testTheProvidersMetadataWinsOverTheCardsSnapshot() async throws {
        let corrected = MusicTrackAttachmentV1(
            trackID: "night-sail", title: "夜航（重制）", artists: ["林一"],
            artworkURL: nil,
            canonicalURL: URL(string: "https://audius.co/lin/night-sail")!,
            durationSeconds: 210, explicit: false
        )
        let controller = makeController(resolver: StubStreamResolver(track: corrected))
        let stale = MusicTrackAttachmentV1(
            trackID: "night-sail", title: "旧标题", artists: ["谁"],
            artworkURL: nil,
            canonicalURL: URL(string: "https://audius.co/lin/night-sail")!,
            durationSeconds: 201, explicit: false
        )
        await startPlaying(stale, on: controller)
        XCTAssertEqual(controller.track?.title, "夜航（重制）")
        XCTAssertEqual(controller.duration, 210)
    }

    // ---- failure ---------------------------------------------------------

    func testATakenDownTrackReadsAsUnavailableRatherThanBroken() async throws {
        let resolver = StubStreamResolver(error: AudiusClientError.unavailable(statusCode: 403))
        let controller = makeController(resolver: resolver)
        controller.play(MusicFixtures.track)
        try await waitFor { controller.state == .unavailable }
        let ids = await resolver.resolvedIDs
        XCTAssertEqual(ids.count, 1, "不可播不是暂时的，不该重试")
    }

    func testAnOrdinaryNetworkFailureIsRetriedExactlyOnce() async throws {
        let resolver = StubStreamResolver(failuresRemaining: 1)
        let controller = makeController(resolver: resolver)
        controller.play(MusicFixtures.track)
        try await waitFor { !self.engine.loadedURLs.isEmpty }
        let ids = await resolver.resolvedIDs
        XCTAssertEqual(ids.count, 2)
    }

    func testARepeatedNetworkFailureGivesUp() async throws {
        let resolver = StubStreamResolver(
            error: URLError(.notConnectedToInternet), failuresRemaining: 5
        )
        let controller = makeController(resolver: resolver)
        controller.play(MusicFixtures.track)
        try await waitFor { controller.state == .failed }
        let ids = await resolver.resolvedIDs
        XCTAssertEqual(ids.count, 2, "只给一次第二次机会")
    }

    func testRateLimitingIsNotRetried() async throws {
        let resolver = StubStreamResolver(error: AudiusClientError.rateLimited(retryAfter: 30))
        let controller = makeController(resolver: resolver)
        controller.play(MusicFixtures.track)
        try await waitFor { controller.state == .failed }
        let ids = await resolver.resolvedIDs
        XCTAssertEqual(ids.count, 1)
    }

    // ---- the system interrupting ----------------------------------------

    func testACallPausesAndThenResumesOnlyIfItHadBeenPlaying() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        NotificationCenter.default.post(interruption: .began)
        try await waitFor { controller.state == .paused }
        NotificationCenter.default.post(interruption: .ended, shouldResume: true)
        try await waitFor { controller.state == .playing }
        let states = try await reportedStates(3)
        XCTAssertEqual(states, [.started, .paused, .resumed])
    }

    func testACallDoesNotStartASongThatWasAlreadyPaused() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.pause()
        NotificationCenter.default.post(interruption: .began)
        NotificationCenter.default.post(interruption: .ended, shouldResume: true)
        try await Task.sleep(for: .milliseconds(40))
        XCTAssertEqual(controller.state, .paused)
    }

    func testTheSystemDecliningToResumeIsRespected() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        NotificationCenter.default.post(interruption: .began)
        try await waitFor { controller.state == .paused }
        NotificationCenter.default.post(interruption: .ended, shouldResume: false)
        try await Task.sleep(for: .milliseconds(40))
        XCTAssertEqual(controller.state, .paused)
    }

    func testUnpluggingHeadphonesPausesImmediately() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        NotificationCenter.default.post(
            name: AVAudioSession.routeChangeNotification, object: nil,
            userInfo: [AVAudioSessionRouteChangeReasonKey:
                AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue]
        )
        try await waitFor { controller.state == .paused }
    }

    func testAnUnrelatedRouteChangeDoesNotStopTheMusic() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        NotificationCenter.default.post(
            name: AVAudioSession.routeChangeNotification, object: nil,
            userInfo: [AVAudioSessionRouteChangeReasonKey:
                AVAudioSession.RouteChangeReason.categoryChange.rawValue]
        )
        try await Task.sleep(for: .milliseconds(40))
        XCTAssertEqual(controller.state, .playing)
    }

    // ---- seeking ---------------------------------------------------------

    func testSeekingIsBoundedByTheTrackLength() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.seek(to: -30)
        controller.seek(to: 9_000)
        XCTAssertEqual(engine.commands.suffix(2), ["seek(0)", "seek(201)"])
    }

    func testSeekingReportsNothing() async throws {
        let controller = makeController()
        await startPlaying(on: controller)
        controller.seek(to: 60)
        let states = try await reportedStates(1)
        XCTAssertEqual(states, [.started], "进度不是要上报的东西")
    }
}

// MARK: - What gets written down between launches

final class MusicPlaybackOutboxTests: XCTestCase {
    private func makeOutbox() -> (MusicPlaybackOutbox, URL) {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        return (MusicPlaybackOutbox(directory: directory), directory)
    }

    private func event(
        _ state: MusicPlaybackReportState, sequence: Int, session: UUID = UUID()
    ) -> MusicPlaybackEvent {
        MusicPlaybackEvent(
            version: 1, sessionID: session, sequence: sequence, state: state,
            track: MusicPlaybackEventTrackV1(
                provider: "audius", trackID: "night-sail", title: "夜航",
                artists: ["林一"], durationSeconds: 201
            ),
            occurredAt: Date()
        )
    }

    func testOnlyTheLatestStateIsKept() async throws {
        let (outbox, directory) = makeOutbox()
        defer { try? FileManager.default.removeItem(at: directory) }
        let session = UUID()
        await outbox.replace(with: event(.started, sequence: 1, session: session))
        await outbox.replace(with: event(.paused, sequence: 2, session: session))
        let pending = await outbox.pending()
        XCTAssertEqual(pending?.state, .paused)
    }

    func testTheStateSurvivesARestart() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        await MusicPlaybackOutbox(directory: directory)
            .replace(with: event(.started, sequence: 1))
        let restored = await MusicPlaybackOutbox(directory: directory).pending()
        XCTAssertEqual(restored?.state, .started)
    }

    func testANewerStateIsNotClearedByAnOlderSend() async throws {
        let (outbox, directory) = makeOutbox()
        defer { try? FileManager.default.removeItem(at: directory) }
        let session = UUID()
        let first = event(.started, sequence: 1, session: session)
        await outbox.replace(with: first)
        await outbox.replace(with: event(.paused, sequence: 2, session: session))
        await outbox.clear(ifMatching: first)
        let pending = await outbox.pending()
        XCTAssertEqual(pending?.state, .paused)
    }
}

@MainActor
final class MusicPlaybackReporterTests: XCTestCase {
    actor FailingTransport: MusicPlaybackEventTransport {
        private(set) var sent: [MusicPlaybackEvent] = []
        private var failing = true

        func send(_ event: MusicPlaybackEvent) async throws {
            if failing { throw URLError(.notConnectedToInternet) }
            sent.append(event)
        }

        func recover() { failing = false }
    }

    private func event(
        _ state: MusicPlaybackReportState, sequence: Int, session: UUID
    ) -> MusicPlaybackEvent {
        MusicPlaybackEvent(
            version: 1, sessionID: session, sequence: sequence, state: state,
            track: MusicPlaybackEventTrackV1(
                provider: "audius", trackID: "night-sail", title: "夜航",
                artists: ["林一"], durationSeconds: 201
            ),
            occurredAt: Date()
        )
    }

    func testOfflineTransitionsCollapseIntoTheLatestOne() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let transport = FailingTransport()
        let reporter = MusicPlaybackEventReporter(
            transport: transport, outbox: MusicPlaybackOutbox(directory: directory)
        )
        let session = UUID()
        await reporter.record(event(.started, sequence: 1, session: session))
        await reporter.record(event(.paused, sequence: 2, session: session))
        await reporter.record(event(.resumed, sequence: 3, session: session))

        await transport.recover()
        await reporter.flush()

        let sent = await transport.sent
        XCTAssertEqual(sent.map(\.state), [.resumed],
                       "恢复网络之后补发的是现在的状态，不是一份收听记录")
    }

    func testAnInterruptedPlayingSessionIsClosedBeforeAnythingIsSent() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let session = UUID()
        await MusicPlaybackOutbox(directory: directory)
            .replace(with: event(.started, sequence: 4, session: session))

        let transport = FailingTransport()
        await transport.recover()
        let reporter = MusicPlaybackEventReporter(
            transport: transport, outbox: MusicPlaybackOutbox(directory: directory)
        )
        await reporter.recoverInterruptedSession()

        let sent = await transport.sent
        XCTAssertEqual(sent.map(\.state), [.stopped])
        XCTAssertEqual(sent.first?.sequence, 5, "补的这条要比它取代的那条新")
        XCTAssertEqual(sent.first?.sessionID, session)
    }

    func testAnAlreadyPausedSessionIsSentAsItStands() async throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        await MusicPlaybackOutbox(directory: directory)
            .replace(with: event(.paused, sequence: 2, session: UUID()))
        let transport = FailingTransport()
        await transport.recover()
        let reporter = MusicPlaybackEventReporter(
            transport: transport, outbox: MusicPlaybackOutbox(directory: directory)
        )
        await reporter.recoverInterruptedSession()
        let sent = await transport.sent
        XCTAssertEqual(sent.map(\.state), [.paused])
    }
}

// MARK: - Picking a song

/// A catalogue that answers from a script, so paging and failure can be driven
/// exactly rather than waited for.
actor StubLibrary: MusicLibraryBrowsing {
    private(set) var searchCalls: [(String, Int)] = []
    private(set) var favoriteOffsets: [Int] = []
    private(set) var playlistTrackCalls: [(String, Int)] = []
    private var tracksByOffset: [Int: [MusicTrackAttachmentV1]]
    private var playlistPages: [AudiusPlaylist]
    private var error: Error?

    init(
        tracksByOffset: [Int: [MusicTrackAttachmentV1]] = [:],
        playlists: [AudiusPlaylist] = [],
        error: Error? = nil
    ) {
        self.tracksByOffset = tracksByOffset
        self.playlistPages = playlists
        self.error = error
    }

    private func page(at offset: Int) throws -> [MusicTrackAttachmentV1] {
        if let error { throw error }
        return tracksByOffset[offset] ?? []
    }

    func searchTracks(query: String, limit: Int, offset: Int) async throws -> [MusicTrackAttachmentV1] {
        searchCalls.append((query, offset))
        return try page(at: offset)
    }

    func favoriteTracks(limit: Int, offset: Int) async throws -> [MusicTrackAttachmentV1] {
        favoriteOffsets.append(offset)
        return try page(at: offset)
    }

    func playlists(limit: Int, offset: Int) async throws -> [AudiusPlaylist] {
        if let error { throw error }
        return offset == 0 ? playlistPages : []
    }

    func playlistTracks(
        playlistID: String, limit: Int, offset: Int
    ) async throws -> [MusicTrackAttachmentV1] {
        playlistTrackCalls.append((playlistID, offset))
        return try page(at: offset)
    }
}

@MainActor
final class MusicPickerModelTests: XCTestCase {
    private func tracks(_ count: Int, prefix: String) -> [MusicTrackAttachmentV1] {
        (0..<count).map { index in
            MusicTrackAttachmentV1(
                trackID: "\(prefix)-\(index)", title: "\(prefix) \(index)",
                artists: ["Nova"], artworkURL: nil,
                canonicalURL: URL(string: "https://audius.co/nova/\(prefix)-\(index)")!,
                durationSeconds: 180, explicit: false
            )
        }
    }

    private func ready(_ model: MusicPickerModel) async throws {
        try await waitFor { model.phase != .loading }
    }

    func testSearchAsksForNothingUntilThereIsSomethingToAskFor() async throws {
        let library = StubLibrary()
        let model = MusicPickerModel(client: library, pageSize: 2)
        model.reload()
        try await ready(model)
        let calls = await library.searchCalls
        XCTAssertTrue(calls.isEmpty)
        XCTAssertEqual(model.emptyMessage, "搜索 Audius 上的公开曲库。")
    }

    func testAFullPageOffersMoreAndAShortPageDoesNot() async throws {
        let library = StubLibrary(tracksByOffset: [0: tracks(2, prefix: "a")])
        let model = MusicPickerModel(client: library, pageSize: 2)
        model.query = "rainy"
        model.reload()
        try await ready(model)
        XCTAssertTrue(model.canLoadMore)

        let short = StubLibrary(tracksByOffset: [0: tracks(1, prefix: "a")])
        let other = MusicPickerModel(client: short, pageSize: 2)
        other.query = "rainy"
        other.reload()
        try await ready(other)
        XCTAssertFalse(other.canLoadMore)
    }

    func testASecondPageIsAppendedRatherThanReplacing() async throws {
        let library = StubLibrary(tracksByOffset: [
            0: tracks(2, prefix: "a"), 2: tracks(1, prefix: "b"),
        ])
        let model = MusicPickerModel(client: library, pageSize: 2)
        model.query = "rainy"
        model.reload()
        try await ready(model)
        model.loadMore()
        try await ready(model)
        XCTAssertEqual(model.tracks.map(\.trackID), ["a-0", "a-1", "b-0"])
        XCTAssertFalse(model.canLoadMore)
        let calls = await library.searchCalls
        XCTAssertEqual(calls.map(\.1), [0, 2])
    }

    func testChangingSourceStartsTheListOver() async throws {
        let library = StubLibrary(tracksByOffset: [0: tracks(2, prefix: "a")])
        let model = MusicPickerModel(client: library, pageSize: 2)
        model.query = "rainy"
        model.reload()
        try await ready(model)
        model.loadMore()
        try await ready(model)

        model.source = .favorites
        try await ready(model)
        let offsets = await library.favoriteOffsets
        XCTAssertEqual(offsets, [0], "换来源要从头开始，不能接着上一个的分页")
    }

    func testAnEmptyLibraryIsEmptyRatherThanBroken() async throws {
        let model = MusicPickerModel(client: StubLibrary(), pageSize: 2)
        model.source = .favorites
        try await ready(model)
        XCTAssertTrue(model.isEmpty)
        XCTAssertEqual(model.phase, .ready)
        XCTAssertEqual(model.emptyMessage, "你在 Audius 上还没有收藏。")
    }

    func testOpeningAPlaylistReadsItAndGoingBackShowsTheListAgain() async throws {
        let playlist = AudiusPlaylist(
            id: "pl-1", name: "夜路", artworkURL: nil, trackCount: 3
        )
        let library = StubLibrary(
            tracksByOffset: [0: tracks(1, prefix: "p")], playlists: [playlist]
        )
        let model = MusicPickerModel(client: library, pageSize: 2)
        model.source = .playlists
        try await ready(model)
        XCTAssertEqual(model.playlists.map(\.id), ["pl-1"])

        model.open(playlist)
        try await ready(model)
        let calls = await library.playlistTrackCalls
        XCTAssertEqual(calls.map(\.0), ["pl-1"])
        XCTAssertEqual(model.tracks.map(\.trackID), ["p-0"])

        model.closePlaylist()
        try await ready(model)
        XCTAssertNil(model.openPlaylist)
        XCTAssertEqual(model.playlists.map(\.id), ["pl-1"])
        XCTAssertTrue(model.tracks.isEmpty)
    }

    func testEachFailureSaysSomethingItsOwn() async throws {
        let cases: [(Error, String)] = [
            (AudiusClientError.rateLimited(retryAfter: 30), "Audius 请求太频繁，稍等一下再试。"),
            (AudiusClientError.unauthorized, "需要重新连接 Audius。"),
            (AudiusClientError.notConfigured, "这个版本还没有配置 Audius。"),
            (URLError(.notConnectedToInternet), "暂时没有连上 Audius。"),
        ]
        for (error, message) in cases {
            let model = MusicPickerModel(client: StubLibrary(error: error), pageSize: 2)
            model.source = .favorites
            try await ready(model)
            XCTAssertEqual(model.phase, .failed(message))
            XCTAssertFalse(model.isEmpty, "失败不是空，两种状态说的不是一件事")
        }
    }

    func testRetryingAfterAFailureAsksAgain() async throws {
        let library = StubLibrary(error: URLError(.timedOut))
        let model = MusicPickerModel(client: library, pageSize: 2)
        model.source = .favorites
        try await ready(model)
        model.reload()
        try await ready(model)
        let offsets = await library.favoriteOffsets
        XCTAssertEqual(offsets.count, 2)
    }
}

// MARK: - The reporting switch

@MainActor
final class MusicModuleTests: XCTestCase {
    private func makeModule() -> MusicModule {
        // No Audius registration: this is about the reporting gate, which is
        // the same either way.
        MusicModule(api: SilentMurmurAPIClient(), configuration: nil)
    }

    func testAnUnregisteredBuildIsNeverAvailable() {
        let module = makeModule()
        XCTAssertFalse(module.isConfigured)
        module.apply(.init(enabled: true, provider: "audius", playbackReporting: true))
        XCTAssertFalse(module.isAvailable, "服务端说开也不行——这个包没有 Audius 注册")
    }

    func testAForeignProviderIsNotAccepted() {
        let module = makeModule()
        module.apply(.init(enabled: true, provider: "spotify", playbackReporting: true))
        XCTAssertFalse(module.isAvailable)
    }

    func testTurningMusicOffStopsWhateverWasPlaying() {
        let module = makeModule()
        module.apply(.init(enabled: false, provider: "audius", playbackReporting: false))
        XCTAssertEqual(module.player.state, .idle)
    }
}

final class PlaybackTransportGateTests: XCTestCase {
    private func event() -> MusicPlaybackEvent {
        MusicPlaybackEvent(
            version: 1, sessionID: UUID(), sequence: 1, state: .stopped,
            track: MusicPlaybackEventTrackV1(
                provider: "audius", trackID: "night-sail", title: "夜航",
                artists: ["林一"], durationSeconds: 201
            ),
            occurredAt: Date()
        )
    }

    func testATransitionIsHeldUntilTheServerHasAnswered() async throws {
        let api = RecordingMurmurAPIClient()
        let transport = MurmurPlaybackTransport(api: api)
        do {
            try await transport.send(event())
            XCTFail("还没问过服务端就把状态丢了")
        } catch is MusicReportingUndecided {
            // Kept, so the outbox can send it once the answer arrives.
        }
        let sent = await api.reported
        XCTAssertEqual(sent, 0)
    }

    func testReportingOffDropsTheTransitionWithoutAnError() async throws {
        let api = RecordingMurmurAPIClient()
        let transport = MurmurPlaybackTransport(api: api)
        await transport.setEnabled(false)
        try await transport.send(event())
        let sent = await api.reported
        XCTAssertEqual(sent, 0, "关掉之后不该发，也不该留着重试")
    }

    func testReportingOnSendsIt() async throws {
        let api = RecordingMurmurAPIClient()
        let transport = MurmurPlaybackTransport(api: api)
        await transport.setEnabled(true)
        try await transport.send(event())
        let sent = await api.reported
        XCTAssertEqual(sent, 1)
    }
}

/// A client that answers nothing. Only the two music calls are ever made.
private actor SilentMurmurAPIClient: MurmurAPIClient {
    struct Unused: Error {}
    func storedIdentity() async throws -> MurmurIdentity? { nil }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity {
        throw Unused()
    }
    func createMoment(
        note: String?, photo: PhotoAttachment?, idempotencyKey: String,
        intent: MurmurMomentIntent?, contextMomentIDs: [String]
    ) async throws -> MomentReceipt { throw Unused() }
    func events(momentID: String, lastEventID: String?)
        async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }
    func currentProactive() async throws -> ProactiveMoment? { nil }
    func acknowledge(momentID: String, reply: String?) async throws {}
    func updateDevice(
        apnsToken: String?, environment: String, timezone: String, deviceName: String
    ) async throws {}
    func devices() async throws -> [MurmurDevice] { [] }
    func removeDevice(deviceID: String) async throws {}
    func preferences() async throws -> MurmurPreferences { throw Unused() }
    func updatePreferences(_ preferences: MurmurPreferences) async throws {}
    func resetLocalIdentity() async throws {}
    func deleteAccount() async throws {}
}

private actor RecordingMurmurAPIClient: MurmurAPIClient {
    private(set) var reported = 0

    struct Unused: Error {}
    func reportMusicPlayback(_ event: MusicPlaybackEvent) async throws { reported += 1 }
    func storedIdentity() async throws -> MurmurIdentity? { nil }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity {
        throw Unused()
    }
    func createMoment(
        note: String?, photo: PhotoAttachment?, idempotencyKey: String,
        intent: MurmurMomentIntent?, contextMomentIDs: [String]
    ) async throws -> MomentReceipt { throw Unused() }
    func events(momentID: String, lastEventID: String?)
        async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }
    func currentProactive() async throws -> ProactiveMoment? { nil }
    func acknowledge(momentID: String, reply: String?) async throws {}
    func updateDevice(
        apnsToken: String?, environment: String, timezone: String, deviceName: String
    ) async throws {}
    func devices() async throws -> [MurmurDevice] { [] }
    func removeDevice(deviceID: String) async throws {}
    func preferences() async throws -> MurmurPreferences { throw Unused() }
    func updatePreferences(_ preferences: MurmurPreferences) async throws {}
    func resetLocalIdentity() async throws {}
    func deleteAccount() async throws {}
}

// MARK: - Helpers

extension NotificationCenter {
    func post(
        interruption type: AVAudioSession.InterruptionType,
        shouldResume: Bool = false
    ) {
        var info: [AnyHashable: Any] = [
            AVAudioSessionInterruptionTypeKey: type.rawValue
        ]
        if type == .ended {
            info[AVAudioSessionInterruptionOptionKey] = shouldResume
                ? AVAudioSession.InterruptionOptions.shouldResume.rawValue
                : UInt(0)
        }
        post(name: AVAudioSession.interruptionNotification, object: nil, userInfo: info)
    }
}

@MainActor
extension XCTestCase {
    /// Poll until a condition holds, so a test never depends on a fixed sleep.
    func waitFor(
        timeout: Duration = .seconds(2),
        file: StaticString = #filePath,
        line: UInt = #line,
        _ condition: @escaping @MainActor () async -> Bool
    ) async throws {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: timeout)
        while clock.now < deadline {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("condition never became true", file: file, line: line)
    }
}
