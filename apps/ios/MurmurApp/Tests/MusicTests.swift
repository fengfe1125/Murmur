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

    static let netease = MusicTrackAttachmentV1(
        provider: "netease",
        trackID: "186016",
        title: "夜曲",
        artists: ["周杰伦"],
        artworkURL: nil,
        canonicalURL: URL(string: "https://music.163.com/song?id=186016")!,
        durationSeconds: 226,
        explicit: false
    )

    static let neteaseOther = MusicTrackAttachmentV1(
        provider: "netease",
        trackID: "186017",
        title: "反方向的钟",
        artists: ["周杰伦"],
        artworkURL: nil,
        canonicalURL: URL(string: "https://music.163.com/song?id=186017")!,
        durationSeconds: 268,
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

    func testNeteaseResolveAndSearchCapabilitiesStayIndependent() {
        let module = makeModule()
        module.apply(.init(
            enabled: true,
            provider: "netease",
            playbackReporting: false,
            providers: [.init(id: "netease", capabilities: ["resolve_shared"])]
        ))
        XCTAssertTrue(module.isNeteaseCatalogAvailable)
        XCTAssertFalse(module.isNeteaseSearchAvailable)

        module.apply(.init(
            enabled: true,
            provider: "netease",
            playbackReporting: false,
            providers: [.init(id: "netease", capabilities: ["search"])]
        ))
        XCTAssertFalse(module.isNeteaseCatalogAvailable)
        XCTAssertTrue(module.isNeteaseSearchAvailable)
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

// MARK: - NetEase: shares, drafts and rooms

final class NeteaseSharedTextDetectorTests: XCTestCase {
    func testTheSupportedShareShapesAreRecognised() {
        let shares = [
            "https://music.163.com/song?id=186016",
            "https://music.163.com/#/song?id=186016",
            "https://music.163.com/m/song?id=186016&userid=1",
            "分享周杰伦的单曲《夜曲》 https://163cn.tv/abcdef (来自网易云音乐)",
        ]
        for share in shares {
            XCTAssertTrue(
                NeteaseSharedTextDetector.containsCandidate(in: share), share
            )
        }
    }

    func testOrdinaryTalkAndOtherPagesAreNotShares() {
        let notShares = [
            "今天下雨了，想听点安静的",
            "https://music.163.com/user/home?id=1",
            "https://example.com/song?id=186016",
            "music.163.com/song?id=186016",
        ]
        for text in notShares {
            XCTAssertFalse(
                NeteaseSharedTextDetector.containsCandidate(in: text), text
            )
        }
    }

    func testAnAbsurdlyLongPasteIsNotScanned() {
        let huge = String(repeating: "x", count: 20_000)
            + " https://music.163.com/song?id=186016"
        XCTAssertFalse(NeteaseSharedTextDetector.containsCandidate(in: huge))
    }

    /// 客户端的上限必须就是服务端的上限。放宽一个字节，8KiB 之外的分享就会
    /// 在设备上被收下、送到服务端换回一个 413——用户看到的是发送失败，而不是
    /// 「这段太长了」。服务端的数字在 app_music_links.MAX_SHARED_TEXT_BYTES。
    func testTheClientStopsExactlyWhereTheServerDoes() {
        XCTAssertEqual(NeteaseSharedTextDetector.maxSharedTextBytes, 8 * 1024)

        let link = " https://music.163.com/song?id=186016"
        let padding = NeteaseSharedTextDetector.maxSharedTextBytes - link.utf8.count
        let atLimit = String(repeating: "x", count: padding) + link
        XCTAssertEqual(atLimit.utf8.count, NeteaseSharedTextDetector.maxSharedTextBytes)
        XCTAssertTrue(NeteaseSharedTextDetector.containsCandidate(in: atLimit))

        let overLimit = "x" + atLimit
        XCTAssertFalse(NeteaseSharedTextDetector.containsCandidate(in: overLimit))
    }
}

final class SharedMusicDraftStoreTests: XCTestCase {
    private var directory: URL!

    override func setUpWithError() throws {
        directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    func testADraftSurvivesTheHandoffToTheApp() throws {
        let store = SharedMusicDraftStore(directory: directory)
        let draft = SharedMusicDraftV1(rawText: "https://music.163.com/song?id=186016")
        try store.save(draft)
        let loaded = try store.load()
        XCTAssertEqual(loaded?.id, draft.id)
        XCTAssertEqual(loaded?.rawText, draft.rawText)
        XCTAssertEqual(loaded?.version, SharedMusicDraftV1.currentVersion)
        // ISO-8601 keeps whole seconds, so the two Dates are close rather than
        // identical. Nothing about a draft depends on finer than that.
        XCTAssertEqual(
            loaded?.createdAt.timeIntervalSince1970 ?? 0,
            draft.createdAt.timeIntervalSince1970,
            accuracy: 1
        )
    }

    func testANewerShareReplacesAnUnconfirmedOne() throws {
        let store = SharedMusicDraftStore(directory: directory)
        try store.save(SharedMusicDraftV1(rawText: "first"))
        let second = SharedMusicDraftV1(rawText: "second")
        try store.save(second)
        XCTAssertEqual(try store.load()?.id, second.id)
    }

    func testRemovingByIdOnlyRemovesThatDraft() throws {
        let store = SharedMusicDraftStore(directory: directory)
        let kept = SharedMusicDraftV1(rawText: "kept")
        try store.save(kept)
        try store.remove(id: UUID())
        XCTAssertEqual(try store.load()?.id, kept.id, "别人的 id 不该删掉这一份")
        try store.remove(id: kept.id)
        XCTAssertNil(try store.load())
    }

    func testNothingStoredReadsAsNothingRatherThanAFailure() throws {
        XCTAssertNil(try SharedMusicDraftStore(directory: directory).load())
    }
}

final class ListenTogetherContractTests: XCTestCase {
    func testPlaybackStateDecodesWhenTheServerSendsIt() throws {
        let room = try decodeRoom(playbackState: "playing")
        XCTAssertEqual(room.playbackState, .playing)
    }

    func testOldServerWithoutPlaybackStateSafelyDecodesAsUnknown() throws {
        let room = try decodeRoom(playbackState: nil)
        XCTAssertEqual(room.playbackState, .unknown)
    }

    func testInvalidPlaybackStateIsRejectedInsteadOfBeingGuessed() throws {
        XCTAssertThrowsError(try decodeRoom(playbackState: "buffering_forever"))
    }

    func testPresentationPriorityAndPlaybackMapping() {
        let playing = room(state: .connected, joined: true, playback: .playing)
        XCTAssertEqual(resolve(playing), .playing)
        XCTAssertEqual(resolve(room(state: .connected, joined: true, playback: .paused)), .paused)
        XCTAssertEqual(resolve(room(state: .waitingForUser)), .waiting)
        XCTAssertEqual(resolve(room(state: .connected, joined: true)), .syncing(nil))
        XCTAssertEqual(
            resolve(playing, connection: .offline("down"), status: .failed),
            .offline,
            "连接异常优先于房间或命令状态"
        )
        XCTAssertEqual(
            resolve(playing, status: .failed, failedCommand: .pause, confirmed: .playing),
            .commandFailed(.pause, .playing)
        )
        XCTAssertEqual(
            resolve(room(state: .failed, error: "invite_expired")),
            .roomFailed("invite_expired")
        )
    }

    func testSongCardRoomRelationshipsHaveUnambiguousActions() {
        XCTAssertEqual(ListenTogetherTrackRelationship.inactive.actionLabel, "和 Murmur 一起听")
        XCTAssertEqual(
            ListenTogetherTrackRelationship.joinableCurrentTrack.actionLabel,
            "进入网易云一起听"
        )
        XCTAssertEqual(ListenTogetherTrackRelationship.currentTrack.actionLabel, "正在一起听")
        XCTAssertEqual(ListenTogetherTrackRelationship.otherTrack.actionLabel, "换成这首")
        XCTAssertFalse(ListenTogetherTrackRelationship.currentTrack.actionEnabled)
        XCTAssertTrue(ListenTogetherTrackRelationship.joinableCurrentTrack.actionEnabled)
    }

    func testSongCardRelationshipUsesTheRoomTrackAndJoinState() {
        XCTAssertEqual(
            ListenTogetherTrackRelationship.resolve(
                track: MusicFixtures.netease,
                room: room(
                    state: .waitingForUser,
                    track: MusicFixtures.netease,
                    invite: URL(string: "https://music.163.com/listen-together/invite/1")
                )
            ),
            .joinableCurrentTrack
        )
        XCTAssertEqual(
            ListenTogetherTrackRelationship.resolve(
                track: MusicFixtures.netease,
                room: room(
                    state: .connected,
                    joined: true,
                    track: MusicFixtures.netease
                )
            ),
            .currentTrack
        )
        XCTAssertEqual(
            ListenTogetherTrackRelationship.resolve(
                track: MusicFixtures.neteaseOther,
                room: room(
                    state: .connected,
                    joined: true,
                    track: MusicFixtures.netease
                )
            ),
            .otherTrack
        )
        XCTAssertEqual(
            ListenTogetherTrackRelationship.resolve(
                track: MusicFixtures.track,
                room: room(
                    state: .connected,
                    joined: true,
                    track: MusicFixtures.netease
                )
            ),
            .inactive
        )
    }

    func testOnlyANewIncomingNeteaseCardRequestsAnImmediateRoomRefresh() {
        let incoming = MurmurMessage(
            author: .murmur, text: "", musicTrack: MusicFixtures.netease
        )
        XCTAssertTrue(
            NeteaseIncomingCardRefresh.shouldRefresh(
                phase: .responding, message: incoming
            )
        )
        XCTAssertFalse(
            NeteaseIncomingCardRefresh.shouldRefresh(
                phase: .complete, message: incoming
            )
        )
        XCTAssertFalse(
            NeteaseIncomingCardRefresh.shouldRefresh(
                phase: .responding,
                message: MurmurMessage(
                    author: .you, text: "", musicTrack: MusicFixtures.netease
                )
            )
        )
        XCTAssertFalse(
            NeteaseIncomingCardRefresh.shouldRefresh(
                phase: .responding,
                message: MurmurMessage(
                    author: .murmur, text: "", musicTrack: MusicFixtures.track
                )
            )
        )
    }

    private func decodeRoom(playbackState: String?) throws -> ListenTogetherRoomSnapshotV1 {
        var object: [String: Any] = [
            "room_handle": "room-1",
            "state": "connected",
            "user_joined": true,
            "updated_at": "2026-09-01T00:00:00Z",
        ]
        if let playbackState { object["playback_state"] = playbackState }
        return try JSONDecoder().decode(
            ListenTogetherRoomSnapshotV1.self,
            from: JSONSerialization.data(withJSONObject: object)
        )
    }

    private func room(
        state: ListenTogetherRoomState,
        joined: Bool = false,
        track: MusicTrackAttachmentV1? = nil,
        invite: URL? = nil,
        playback: ListenTogetherPlaybackState = .unknown,
        error: String? = nil
    ) -> ListenTogetherRoomSnapshotV1 {
        .init(
            roomHandle: "room-1",
            state: state,
            currentTrack: track,
            userJoined: joined,
            inviteURL: invite,
            updatedAt: "2026-09-01T00:00:00Z",
            errorCode: error,
            playbackState: playback
        )
    }

    private func resolve(
        _ room: ListenTogetherRoomSnapshotV1,
        connection: MurmurConnectionState = .connected,
        status: ListenTogetherCommandStatus? = nil,
        failedCommand: ListenTogetherCommand? = nil,
        confirmed: ListenTogetherPlaybackState = .unknown
    ) -> ListenTogetherPresentationState {
        .resolve(
            connection: connection,
            room: room,
            commandStatus: status,
            isChanging: false,
            mutation: failedCommand.map {
                .command(
                    command: $0, track: nil, roomHandle: room.roomHandle,
                    idempotencyKey: "test-key"
                )
            },
            failureMessage: nil,
            lastConfirmedPlayback: confirmed
        )
    }
}

@MainActor
final class NeteaseMusicModelTests: XCTestCase {
    // XCTest's setup/teardown overrides are nonisolated even though the test
    // body is MainActor-isolated. The runner mutates this only serially.
    nonisolated(unsafe) private var directory: URL!

    override func setUpWithError() throws {
        directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    private func model(_ api: ScriptedMurmurAPIClient) -> NeteaseMusicModel {
        NeteaseMusicModel(
            api: api, draftStore: SharedMusicDraftStore(directory: directory)
        )
    }

    func testOrdinaryTypingIsNeverSentAnywhere() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        XCTAssertFalse(model.recognizePastedText("今天走了很久"))
        let calls = await api.calls
        XCTAssertEqual(calls, [])
    }

    func testAPastedLinkBecomesAPreviewTheServerResolved() async throws {
        let api = ScriptedMurmurAPIClient()
        await api.setSharedTrack(MusicFixtures.netease)
        let model = model(api)
        XCTAssertTrue(model.recognizePastedText(
            "《随便什么名字》https://music.163.com/song?id=186016"
        ))
        try await waitFor { model.preview != nil }
        XCTAssertEqual(model.preview?.track.title, MusicFixtures.netease.title)
    }

    func testConfirmingHandsTheSongOverAndClearsTheDraft() async throws {
        let api = ScriptedMurmurAPIClient()
        await api.setSharedTrack(MusicFixtures.netease)
        let model = model(api)
        model.recognizePastedText("https://music.163.com/song?id=186016")
        try await waitFor { model.preview != nil }
        let confirmed = model.confirmPreview()
        XCTAssertEqual(confirmed?.trackID, MusicFixtures.netease.trackID)
        XCTAssertNil(model.preview)
        XCTAssertNil(try SharedMusicDraftStore(directory: directory).load())
    }

    func testAResolutionThatIsNotNeteaseIsRefusedAndTheDraftIsKept() async throws {
        let api = ScriptedMurmurAPIClient()
        await api.setSharedTrack(MusicFixtures.track)
        let model = model(api)
        model.recognizePastedText("https://music.163.com/song?id=186016")
        try await waitFor { model.failureMessage != nil }
        XCTAssertNil(model.preview)
        XCTAssertNotNil(
            try SharedMusicDraftStore(directory: directory).load(),
            "解析失败不该把他明确分享的这一条弄丢"
        )
    }

    func testAnAudiusCardNeverAsksForARoom() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        let invite = await model.createRoom(for: MusicFixtures.track)
        XCTAssertNil(invite)
        let calls = await api.calls
        XCTAssertEqual(calls, [])
    }

    func testAFirstRoomIsCreatedAndASecondSongSwitchesInsideIt() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        XCTAssertEqual(model.room?.state, .waitingForUser)
        let invite = await model.createRoom(for: MusicFixtures.neteaseOther)
        let calls = await api.calls
        XCTAssertEqual(calls, ["create", "command:play_track"], "已经有房间就换歌，不再开一个")
        XCTAssertNotNil(invite, "还没加入时，换歌后仍应打开同一个邀请")
    }

    func testAServerCreatedWaitingRoomOpensItsInviteWithoutAnotherCommand() async throws {
        let api = ScriptedMurmurAPIClient()
        let waiting = ListenTogetherRoomSnapshotV1(
            roomHandle: "server-room", state: .waitingForUser,
            currentTrack: MusicFixtures.netease,
            inviteURL: URL(string: "https://music.163.com/listen-together/invite/server"),
            updatedAt: "2026-09-04T00:00:00Z"
        )
        await api.setRoom(waiting)
        let model = model(api)
        await model.refreshRoom()

        let invite = await model.createRoom(for: MusicFixtures.netease)

        XCTAssertEqual(invite, waiting.inviteURL)
        let calls = await api.calls
        XCTAssertEqual(calls, ["current"], "已有同曲房间只开邀请，不重复建房或切歌")
    }

    func testACommandThatFailedIsNeverShownAsSynchronised() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        await api.setFailing(true)
        await model.command(.pause)
        XCTAssertEqual(model.commandStatus, .failed)
        XCTAssertEqual(model.roomMutation?.command, .pause)
        XCTAssertNotNil(model.room, "单次命令失败不能清掉活动房间")
        XCTAssertNil(model.failureMessage, "命令错误应该留在 Header 内，不弹全局错误")
        XCTAssertEqual(
            model.presentationState(connection: .connected),
            .commandFailed(.pause, .unknown)
        )
    }

    func testAcceptedWaitsForARemoteSnapshotInsteadOfClaimingSuccess() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        await api.setNextCommand(status: .accepted, playback: .unknown)
        await model.command(.pause)
        XCTAssertEqual(model.commandStatus, .accepted)
        XCTAssertEqual(model.presentationState(connection: .connected), .syncing(.pause))
    }

    func testFailedCommandCanRetryAndOnlyThenShowsConfirmedPlayback() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        await api.setNextCommand(status: .failed, playback: .playing)
        await model.command(.pause)
        XCTAssertEqual(
            model.presentationState(connection: .connected),
            .commandFailed(.pause, .playing)
        )
        let first = await api.commandRequests.last
        XCTAssertNil(first?.track, "暂停等普通命令不能夹带歌曲")

        await api.setNextCommand(status: .synchronized, playback: .paused)
        _ = await model.retryLastAction()
        let retried = await api.commandRequests.last
        XCTAssertNil(retried?.track)
        XCTAssertEqual(retried?.roomHandle, first?.roomHandle)
        XCTAssertEqual(retried?.idempotencyKey, first?.idempotencyKey)
        XCTAssertEqual(model.commandStatus, .synchronized)
        XCTAssertNil(model.roomMutation)
        XCTAssertEqual(model.presentationState(connection: .connected), .paused)
    }

    func testFailedCreateKeepsTrackAndReusesItsIdempotencyKey() async throws {
        let api = ScriptedMurmurAPIClient()
        await api.setFailing(true)
        let model = model(api)

        _ = await model.createRoom(for: MusicFixtures.netease)

        guard case .creationFailed(let track, let message) =
                model.presentationState(connection: .connected) else {
            return XCTFail("建房失败必须留在可见的恢复状态")
        }
        XCTAssertEqual(track, MusicFixtures.netease)
        XCTAssertFalse(message.isEmpty)
        XCTAssertEqual(model.displayTrack, MusicFixtures.netease)
        let first = await api.createRequests

        await api.setFailing(false)
        let invite = await model.retryLastAction()
        let retried = await api.createRequests

        XCTAssertEqual(retried.map(\.track), [MusicFixtures.netease, MusicFixtures.netease])
        XCTAssertEqual(retried.map(\.idempotencyKey), [first[0].idempotencyKey, first[0].idempotencyKey])
        XCTAssertNotNil(invite, "建房重试成功后调用方仍需拿到网易云邀请")
        XCTAssertNil(model.roomMutation)
        XCTAssertNotNil(model.room)
    }

    func testPreparedCreateKeepsTheSongBeforeTheRequestStarts() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)

        guard let action = model.prepareRoomAction(for: MusicFixtures.netease) else {
            return XCTFail("网易云歌曲应生成建房动作")
        }

        XCTAssertEqual(
            model.presentationState(connection: .connected),
            .creating(MusicFixtures.netease)
        )
        XCTAssertEqual(model.displayTrack, MusicFixtures.netease)
        let requestsBeforeExecution = await api.createRequests
        XCTAssertEqual(requestsBeforeExecution.count, 0)

        _ = await model.performPreparedRoomAction(action)
        let requestsAfterExecution = await api.createRequests
        XCTAssertEqual(requestsAfterExecution.count, 1)
    }

    func testFailedTrackChangeRetriesTheSameTrackRoomAndKey() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        await api.setFailing(true)

        _ = await model.createRoom(for: MusicFixtures.neteaseOther)
        XCTAssertEqual(
            model.presentationState(connection: .connected),
            .commandFailed(.playTrack, .unknown)
        )
        XCTAssertEqual(model.presentationState(connection: .connected).primaryAccessibilityLabel, "重试换歌")
        let first = await api.commandRequests.last

        await api.setFailing(false)
        _ = await model.retryLastAction()
        let second = await api.commandRequests.last

        XCTAssertEqual(second?.command, .playTrack)
        XCTAssertEqual(second?.track, MusicFixtures.neteaseOther)
        XCTAssertEqual(second?.roomHandle, first?.roomHandle)
        XCTAssertEqual(second?.idempotencyKey, first?.idempotencyKey)
        XCTAssertNil(model.roomMutation)
    }

    func testClearingACreationFailureAlsoClearsItsRetryAction() async throws {
        let api = ScriptedMurmurAPIClient()
        await api.setFailing(true)
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)

        model.clearFailure()

        XCTAssertNil(model.roomMutation)
        XCTAssertNil(model.failureMessage)
        XCTAssertEqual(model.presentationState(connection: .connected), .inactive)
    }

    func testClosingTheRoomClearsAPendingRetryAction() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        await api.setFailing(true)
        await model.command(.pause)
        XCTAssertNotNil(model.roomMutation)

        await api.setFailing(false)
        await model.closeRoom()

        XCTAssertNil(model.roomMutation)
        XCTAssertNil(model.failureMessage)
        XCTAssertEqual(model.room?.state, .ended)
    }

    func testWithoutARoomThereIsNothingToCommand() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        await model.command(.next)
        let calls = await api.calls
        XCTAssertEqual(calls, [])
    }

    func testRoomRefreshesNeverOverlap() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        await api.setCurrentDelay(.milliseconds(120))

        let first = Task { await model.refreshRoom() }
        try await Task.sleep(for: .milliseconds(20))
        await model.refreshRoom()
        await first.value

        let overlap = await api.maxCurrentInFlight
        XCTAssertEqual(overlap, 1, "同一时刻只许有一个 current 在外面")
    }

    func testARefreshAskedForDuringAPollIsDeferredRatherThanDropped() async throws {
        // 空闲轮询正在路上时，聊天里到了一张歌曲卡。那次轮询是建房之前发出的，
        // 答案必然是「没有房间」；卡片这次要是被丢掉，界面就得等满一个空闲
        // 间隔才知道房间已经开好了。
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        await api.setCurrentDelay(.milliseconds(120))

        let idlePoll = Task { await model.refreshRoom() }
        try await Task.sleep(for: .milliseconds(20))
        await api.setRoom(ListenTogetherRoomSnapshotV1(
            roomHandle: "server-room", state: .waitingForUser,
            currentTrack: MusicFixtures.netease,
            inviteURL: URL(string: "https://music.163.com/listen-together/invite/server"),
            updatedAt: "2026-09-04T00:00:00Z"
        ))
        await model.refreshRoom()
        await idlePoll.value

        let currentCalls = await api.calls.filter { $0 == "current" }
        XCTAssertEqual(currentCalls.count, 2, "第二次刷新不能被丢掉")
        XCTAssertEqual(model.room?.roomHandle, "server-room")
        let overlap = await api.maxCurrentInFlight
        XCTAssertEqual(overlap, 1, "补问要等在飞的那次落地，不是并排再发一个")
    }

    func testAnOldRefreshCannotRestoreARoomThatWasClosedWhileItWasInFlight() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        await api.setCurrentDelay(.milliseconds(120))

        let staleRefresh = Task { await model.refreshRoom() }
        try await Task.sleep(for: .milliseconds(20))
        await model.closeRoom()
        await staleRefresh.value

        XCTAssertEqual(model.room?.state, .ended)
        XCTAssertFalse(model.room?.isActive ?? true)
    }

    func testAnOldRefreshCannotUndoACommandConfirmedWhileItWasInFlight() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        await api.setCurrentDelay(.milliseconds(120))

        let staleRefresh = Task { await model.refreshRoom() }
        try await Task.sleep(for: .milliseconds(20))
        await api.setNextCommand(status: .synchronized, playback: .paused)
        await model.command(.pause)
        await staleRefresh.value

        XCTAssertEqual(model.room?.state, .connected)
        XCTAssertEqual(model.lastConfirmedPlaybackState, .paused)
    }

    func testAnOldRefreshCannotUndoATrackChangeInsideAnExistingRoom() async throws {
        let api = ScriptedMurmurAPIClient()
        let model = model(api)
        _ = await model.createRoom(for: MusicFixtures.netease)
        await api.setCurrentDelay(.milliseconds(120))

        let staleRefresh = Task { await model.refreshRoom() }
        try await Task.sleep(for: .milliseconds(20))
        _ = await model.createRoom(for: MusicFixtures.neteaseOther)
        await staleRefresh.value

        XCTAssertEqual(
            model.room?.currentTrack?.trackID, MusicFixtures.neteaseOther.trackID
        )
    }
}

/// The player is an Audius stream player; a NetEase card is an external
/// control surface and must not reach it.
@MainActor
final class NeteasePlayerGuardTests: XCTestCase {
    func testANeteaseCardIsNeverLoadedIntoThePlayer() async throws {
        let engine = FakeAudioPlayerEngine()
        let controller = MusicPlaybackController(
            engine: engine, resolver: StubStreamResolver(),
            reporter: RecordingPlaybackReporter(), session: StubAudioSession()
        )
        controller.play(MusicFixtures.netease)
        controller.tap(MusicFixtures.netease)
        XCTAssertEqual(engine.loadedURLs, [])
        XCTAssertEqual(controller.state, .idle)
    }
}

@MainActor
final class NeteaseSearchModelTests: XCTestCase {
    func testAnEmptyQueryAsksForNothing() async {
        let api = ScriptedMurmurAPIClient()
        let model = NeteaseSearchModel(api: api)
        model.query = "   "
        model.search()
        await settle()
        XCTAssertEqual(model.phase, .idle)
        let queries = await api.searchQueries
        XCTAssertTrue(queries.isEmpty, "空查询不该打到服务端")
    }

    func testASearchTrimsTheQueryAndKeepsWhatCameBack() async {
        let api = ScriptedMurmurAPIClient()
        await api.setSearch(results: [MusicFixtures.netease])
        let model = NeteaseSearchModel(api: api)
        model.query = "  夜曲  "
        model.search()
        await settle()
        XCTAssertEqual(model.phase, .results([MusicFixtures.netease]))
        let queries = await api.searchQueries
        XCTAssertEqual(queries, ["夜曲"])
    }

    func testNoResultsIsAnAnswerNotAFailure() async {
        let api = ScriptedMurmurAPIClient()
        await api.setSearch(results: [])
        let model = NeteaseSearchModel(api: api)
        model.query = "没有这首"
        model.search()
        await settle()
        XCTAssertEqual(model.phase, .results([]))
    }

    func testAFailureSaysSomethingAndCanBeAskedAgain() async {
        let api = ScriptedMurmurAPIClient()
        await api.setSearch(results: [], fails: true)
        let model = NeteaseSearchModel(api: api)
        model.query = "夜曲"
        model.search()
        await settle()
        guard case .failed(let message) = model.phase else {
            return XCTFail("expected a failure, got \(model.phase)")
        }
        XCTAssertFalse(message.isEmpty)

        await api.setSearch(results: [MusicFixtures.netease])
        model.search()
        await settle()
        XCTAssertEqual(model.phase, .results([MusicFixtures.netease]))
        let calls = await api.calls
        XCTAssertEqual(calls.filter { $0 == "search" }.count, 2, "重试要真的再问一次")
    }

    private func settle() async {
        for _ in 0..<10 { await Task.yield() }
        try? await Task.sleep(for: .milliseconds(50))
        for _ in 0..<10 { await Task.yield() }
    }
}

private actor ScriptedMurmurAPIClient: MurmurAPIClient {
    struct CreateRequest: Equatable {
        let track: MusicTrackAttachmentV1
        let idempotencyKey: String
    }

    struct CommandRequest: Equatable {
        let command: ListenTogetherCommand
        let track: MusicTrackAttachmentV1?
        let roomHandle: String
        let idempotencyKey: String
    }

    private(set) var calls: [String] = []
    private(set) var createRequests: [CreateRequest] = []
    private(set) var commandRequests: [CommandRequest] = []
    private var sharedTrack: MusicTrackAttachmentV1?
    private var failing = false
    private var room: ListenTogetherRoomSnapshotV1?
    private var nextCommandStatus: ListenTogetherCommandStatus = .synchronized
    private var nextPlaybackState: ListenTogetherPlaybackState = .playing
    private var currentDelay: Duration = .zero
    private var currentInFlight = 0
    /// 单飞的证据：这个数字大于 1 就说明两个请求同时在外面。
    private(set) var maxCurrentInFlight = 0

    struct Unused: Error {}
    struct Refused: Error {}

    private var searchResults: [MusicTrackAttachmentV1] = []
    private var searchFails = false
    private(set) var searchQueries: [String] = []

    func setSharedTrack(_ track: MusicTrackAttachmentV1?) { sharedTrack = track }
    func setSearch(results: [MusicTrackAttachmentV1], fails: Bool = false) {
        searchResults = results
        searchFails = fails
    }

    func searchMusic(query: String, limit: Int) async throws -> [MusicTrackAttachmentV1] {
        calls.append("search")
        searchQueries.append(query)
        guard !searchFails else { throw Refused() }
        return searchResults
    }
    func setFailing(_ value: Bool) { failing = value }
    func setCurrentDelay(_ delay: Duration) { currentDelay = delay }
    func setRoom(_ value: ListenTogetherRoomSnapshotV1?) { room = value }
    func setNextCommand(
        status: ListenTogetherCommandStatus,
        playback: ListenTogetherPlaybackState
    ) {
        nextCommandStatus = status
        nextPlaybackState = playback
    }

    func resolveSharedMusic(
        text: String, idempotencyKey: String
    ) async throws -> MusicTrackAttachmentV1 {
        calls.append("resolve")
        guard !failing, let sharedTrack else { throw Refused() }
        return sharedTrack
    }

    func createListenTogetherRoom(
        initialTrack: MusicTrackAttachmentV1, idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1 {
        calls.append("create")
        createRequests.append(.init(track: initialTrack, idempotencyKey: idempotencyKey))
        if failing { throw Refused() }
        let snapshot = ListenTogetherRoomSnapshotV1(
            roomHandle: "handle-1", state: .waitingForUser, currentTrack: initialTrack,
            inviteURL: URL(string: "https://music.163.com/listen-together/invite/1"),
            updatedAt: "2026-08-31T00:00:00Z"
        )
        room = snapshot
        return snapshot
    }

    func currentListenTogetherRoom() async throws -> ListenTogetherRoomSnapshotV1? {
        calls.append("current")
        if failing { throw Refused() }
        let snapshot = room
        currentInFlight += 1
        maxCurrentInFlight = max(maxCurrentInFlight, currentInFlight)
        defer { currentInFlight -= 1 }
        if currentDelay > .zero { try await Task.sleep(for: currentDelay) }
        return snapshot
    }

    func commandListenTogetherRoom(
        handle: String, command: ListenTogetherCommand,
        track: MusicTrackAttachmentV1?, idempotencyKey: String
    ) async throws -> ListenTogetherCommandResultV1 {
        calls.append("command:\(command.rawValue)")
        commandRequests.append(.init(
            command: command, track: track, roomHandle: handle,
            idempotencyKey: idempotencyKey
        ))
        if failing { throw Refused() }
        let status = nextCommandStatus
        let playback = nextPlaybackState
        let state: ListenTogetherRoomState = status == .accepted ? .syncing : .connected
        let prior = room
        let snapshot = ListenTogetherRoomSnapshotV1(
            roomHandle: handle, state: state,
            currentTrack: track ?? prior?.currentTrack,
            userJoined: command == .playTrack ? (prior?.userJoined ?? false) : true,
            pendingCommand: status == .accepted ? command.rawValue : nil,
            inviteURL: prior?.inviteURL,
            updatedAt: "2026-08-31T00:00:01Z",
            errorCode: status == .failed ? "command_rejected" : nil,
            playbackState: playback
        )
        room = snapshot
        return ListenTogetherCommandResultV1(status: status, room: snapshot)
    }

    func closeListenTogetherRoom(
        handle: String,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1 {
        calls.append("close")
        if failing { throw Refused() }
        let snapshot = ListenTogetherRoomSnapshotV1(
            roomHandle: handle, state: .ended, updatedAt: "2026-08-31T00:00:02Z"
        )
        room = nil
        return snapshot
    }

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
