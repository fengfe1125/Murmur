import AVFoundation
import Foundation
import MediaPlayer

/// What the person can see about the one song the app is playing.
///
/// Deliberately smaller than `AVPlayer`'s own vocabulary: the screen only ever
/// needs to know whether to draw a spinner, a play button, a pause button, or
/// an apology.
enum MusicPlaybackState: Equatable, Sendable {
    case idle
    /// Re-checking the track and asking the provider for a stream.
    case loading
    case buffering
    case playing
    case paused
    case ended
    /// The provider says this track cannot be streamed right now — taken down,
    /// made private, or the artist closed API access. Retrying will not help.
    case unavailable
    case failed

    var isActive: Bool {
        switch self {
        case .loading, .buffering, .playing, .paused: true
        case .idle, .ended, .unavailable, .failed: false
        }
    }

    var isPlaying: Bool { self == .playing }
}

/// The part of playback that talks to `AVFoundation`.
///
/// It exists so the controller's rules — one song at a time, report each
/// transition once, never resume by itself — can be tested without a real
/// player, an audio session or a device.
@MainActor
protocol AudioPlayerEngine: AnyObject {
    var onStatus: ((AudioPlayerStatus) -> Void)? { get set }
    var onTime: ((TimeInterval) -> Void)? { get set }
    var duration: TimeInterval? { get }

    func load(url: URL)
    func play()
    func pause()
    func seek(to seconds: TimeInterval)
    func stop()
}

enum AudioPlayerStatus: Equatable, Sendable {
    case buffering
    case playing
    case paused
    case ended
    /// Something that may pass on a second attempt: a dropped connection, a
    /// stalled segment.
    case failed(retryable: Bool)
}

struct ResolvedMusicStream: Equatable, Sendable {
    let track: MusicTrackAttachmentV1
    let url: URL
}

/// Turns a stable track id into something playable, right now.
///
/// Every play goes through this again rather than reusing what a card was
/// showing: metadata drifts, access decisions change, and a stream endpoint is
/// short-lived and must never be written down.
protocol MusicStreamResolving: Sendable {
    func resolveStream(trackID: String) async throws -> ResolvedMusicStream
}

extension AudiusClient: MusicStreamResolving {
    func resolveStream(trackID: String) async throws -> ResolvedMusicStream {
        let stream = try await resolvePlayableStream(trackID: trackID)
        return ResolvedMusicStream(track: stream.track, url: stream.url)
    }
}

/// The one player in the app.
///
/// Everything about a song being audible lives here: which song, what the lock
/// screen says, what a headphone unplug means, and which discrete transitions
/// Murmur is told about. Nothing here knows what a chat message is.
@MainActor
final class MusicPlaybackController: ObservableObject {
    @Published private(set) var state: MusicPlaybackState = .idle
    @Published private(set) var track: MusicTrackAttachmentV1?
    @Published private(set) var elapsed: TimeInterval = 0
    @Published private(set) var duration: TimeInterval?

    /// How long a song may sit buffering before it is called a failure. Past
    /// this the spinner is a lie: something is wrong and saying so beats
    /// spinning forever.
    static let bufferingLimit: TimeInterval = 15

    private let engine: any AudioPlayerEngine
    private let resolver: any MusicStreamResolving
    private let reporter: any MusicPlaybackReporting
    private let session: MusicAudioSessioning

    /// One session per load. Murmur sees a sequence of transitions inside it
    /// and nothing that could be reassembled into a listening history.
    private var sessionID = UUID()
    private var sequence = 0
    private var loadTask: Task<Void, Never>?
    private var bufferingWatchdog: Task<Void, Never>?
    /// A song only gets one automatic second chance, and only for the kind of
    /// failure a second chance can fix.
    private var hasRetried = false
    /// Whether an interruption arrived while sound was actually coming out.
    /// Nothing else may cause playback to resume on its own.
    private var wasPlayingBeforeInterruption = false
    /// Unisolated so `deinit` can hand the tokens back. Every write happens on
    /// the main actor during `init`; `deinit` runs once, after the last other
    /// reference is already gone.
    nonisolated(unsafe) private var observers: [NSObjectProtocol] = []
    /// The lock-screen handlers, kept so they can be taken back off again.
    /// `MPRemoteCommandCenter` is process-wide: targets added and never removed
    /// outlive the player that added them.
    nonisolated(unsafe) private var commandTargets: [(MPRemoteCommand, Any)] = []

    init(
        engine: any AudioPlayerEngine = AVAudioPlayerEngine(),
        resolver: any MusicStreamResolving,
        reporter: any MusicPlaybackReporting,
        session: MusicAudioSessioning = SystemMusicAudioSession()
    ) {
        self.engine = engine
        self.resolver = resolver
        self.reporter = reporter
        self.session = session
        self.engine.onStatus = { [weak self] status in
            self?.handle(status)
        }
        self.engine.onTime = { [weak self] time in
            self?.elapsed = time
        }
        observeSystemEvents()
        configureRemoteCommands()
        // A process restart never resumes audio. If the last thing written down
        // says it was playing, close that session before anything else.
        Task { await reporter.recoverInterruptedSession() }
    }

    deinit {
        for observer in observers {
            NotificationCenter.default.removeObserver(observer)
        }
        for (command, target) in commandTargets {
            command.removeTarget(target)
        }
    }

    // MARK: - What the screen asks for

    /// Start this song. Only ever called because somebody tapped it.
    func play(_ requested: MusicTrackAttachmentV1) {
        if track?.id == requested.id, state == .paused {
            resume()
            return
        }
        // One song at a time: whatever is playing stops and is reported closed
        // before the new one is even resolved.
        stopCurrent(reporting: .stopped)
        track = requested
        elapsed = 0
        duration = requested.durationSeconds.map(TimeInterval.init)
        sessionID = UUID()
        sequence = 0
        hasRetried = false
        state = .loading
        beginLoad(requested)
    }

    /// What a tap on a card means.
    ///
    /// Start this song — unless it is already the one loaded, in which case the
    /// button under the finger was a pause button and pressing it must not
    /// throw the song away and fetch it again.
    func tap(_ requested: MusicTrackAttachmentV1) {
        if track?.id == requested.id, state.isActive {
            togglePlayPause()
        } else {
            play(requested)
        }
    }

    func togglePlayPause() {
        switch state {
        case .playing: pause()
        case .paused: resume()
        case .ended, .idle, .failed:
            if let track { play(track) }
        case .loading, .buffering, .unavailable:
            break
        }
    }

    func pause() {
        guard state == .playing || state == .buffering else { return }
        engine.pause()
        // `engine.pause()` may already have called back and done this. Landing
        // on the same state twice is normal; reporting it twice is not, which
        // is what `move(to:reporting:)` is for.
        move(to: .paused, reporting: .paused)
    }

    func resume() {
        guard state == .paused else { return }
        engine.play()
        move(to: .playing, reporting: .resumed)
    }

    /// Change state, and tell Murmur only if the state really changed.
    ///
    /// Both the caller and the engine's own callback arrive at the same
    /// transition — `AVPlayer` reports the pause that `pause()` just asked for
    /// — so without this every pause and resume would be reported twice.
    private func move(
        to next: MusicPlaybackState, reporting reportState: MusicPlaybackReportState?
    ) {
        guard state != next else { return }
        state = next
        if let reportState { report(reportState) }
        updateNowPlaying()
    }

    func seek(to seconds: TimeInterval) {
        guard state.isActive else { return }
        let bounded = max(0, min(seconds, duration ?? seconds))
        elapsed = bounded
        engine.seek(to: bounded)
        updateNowPlaying()
    }

    /// Put the player away entirely. The card stays on screen; the sound does
    /// not.
    func stop() {
        stopCurrent(reporting: .stopped)
        track = nil
        elapsed = 0
        duration = nil
        state = .idle
    }

    // MARK: - Getting a song to the point of sound

    private func beginLoad(_ requested: MusicTrackAttachmentV1) {
        loadTask?.cancel()
        loadTask = Task { [weak self] in
            guard let self else { return }
            do {
                let stream = try await resolver.resolveStream(trackID: requested.trackID)
                guard !Task.isCancelled, track?.id == requested.id else { return }
                // The provider's copy wins over the snapshot the card was
                // drawn from: a title can be corrected and a length can be
                // re-encoded after a card was sent.
                track = stream.track
                duration = stream.track.durationSeconds.map(TimeInterval.init)
                try session.activate()
                state = .buffering
                startBufferingWatchdog()
                engine.load(url: stream.url)
                engine.play()
            } catch is CancellationError {
                return
            } catch {
                guard track?.id == requested.id else { return }
                fail(with: error)
            }
        }
    }

    private func startBufferingWatchdog() {
        bufferingWatchdog?.cancel()
        bufferingWatchdog = Task { [weak self] in
            try? await Task.sleep(for: .seconds(Self.bufferingLimit))
            guard let self, !Task.isCancelled else { return }
            guard state == .buffering || state == .loading else { return }
            enter(.failed)
        }
    }

    private func handle(_ status: AudioPlayerStatus) {
        switch status {
        case .buffering:
            guard state != .paused else { return }
            state = .buffering
            startBufferingWatchdog()
        case .playing:
            bufferingWatchdog?.cancel()
            duration = duration ?? engine.duration
            // The first sound this session ever made is a start; every one
            // after it is a resume.
            move(to: .playing, reporting: sequence == 0 ? .started : .resumed)
        case .paused:
            guard state == .playing else { return }
            move(to: .paused, reporting: .paused)
        case .ended:
            bufferingWatchdog?.cancel()
            move(to: .ended, reporting: .completed)
            session.deactivate()
            clearNowPlaying()
        case let .failed(retryable):
            bufferingWatchdog?.cancel()
            if retryable, !hasRetried, let track {
                hasRetried = true
                state = .loading
                beginLoad(track)
                return
            }
            enter(.failed)
        }
    }

    private func fail(with error: Error) {
        bufferingWatchdog?.cancel()
        // "Cannot be streamed" is a different sentence from "that did not
        // work": one is about the song, the other about the moment.
        if case AudiusClientError.unavailable = error {
            enter(.unavailable)
            return
        }
        if case AudiusClientError.notConfigured = error {
            enter(.unavailable)
            return
        }
        if !hasRetried, let track, Self.isWorthRetrying(error) {
            hasRetried = true
            beginLoad(track)
            return
        }
        enter(.failed)
    }

    private static func isWorthRetrying(_ error: Error) -> Bool {
        if case AudiusClientError.rateLimited = error { return false }
        if case AudiusClientError.notAuthenticated = error { return false }
        return (error as? URLError) != nil
    }

    private func enter(_ terminal: MusicPlaybackState) {
        engine.stop()
        move(to: terminal, reporting: .stopped)
        session.deactivate()
        clearNowPlaying()
    }

    private func stopCurrent(reporting state: MusicPlaybackReportState) {
        loadTask?.cancel()
        loadTask = nil
        bufferingWatchdog?.cancel()
        bufferingWatchdog = nil
        guard self.state.isActive else { return }
        engine.stop()
        report(state)
        session.deactivate()
        clearNowPlaying()
    }

    // MARK: - What Murmur is told

    /// One transition, once. Sequence is per session and only ever goes up, so
    /// a report that arrives late cannot overwrite a newer one.
    private func report(_ reportState: MusicPlaybackReportState) {
        guard let track else { return }
        sequence += 1
        let event = MusicPlaybackEvent(
            version: 1,
            sessionID: sessionID,
            sequence: sequence,
            state: reportState,
            track: MusicPlaybackEventTrackV1(
                provider: track.provider,
                trackID: track.trackID,
                title: track.title,
                artists: track.artists,
                durationSeconds: track.durationSeconds
            ),
            occurredAt: Date()
        )
        Task { [reporter] in await reporter.record(event) }
    }

    // MARK: - Lock screen and the rest of the system

    private func configureRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()
        // The thread these handlers arrive on is not documented, so the work
        // hops to the main actor rather than asserting it is already there —
        // `assumeIsolated` would trap, and trapping on a lock-screen button is
        // a crash in the one place nobody can see a log.
        func handle(_ command: MPRemoteCommand, _ body: @escaping @MainActor () -> Void) {
            let target = command.addTarget { [weak self] _ in
                guard self != nil else { return .commandFailed }
                Task { @MainActor in body() }
                return .success
            }
            commandTargets.append((command, target))
        }
        handle(center.playCommand) { [weak self] in self?.resume() }
        handle(center.pauseCommand) { [weak self] in self?.pause() }
        handle(center.togglePlayPauseCommand) { [weak self] in self?.togglePlayPause() }
        let seekTarget = center.changePlaybackPositionCommand.addTarget { [weak self] event in
            // The event type is checked here, where the answer still means
            // something; the seek itself hops like the rest.
            guard self != nil,
                  let positional = event as? MPChangePlaybackPositionCommandEvent
            else { return .commandFailed }
            let position = positional.positionTime
            Task { @MainActor [weak self] in self?.seek(to: position) }
            return .success
        }
        commandTargets.append((center.changePlaybackPositionCommand, seekTarget))
        // There is no queue in this version, so the lock screen must not offer
        // a control that would do nothing.
        for command in [center.nextTrackCommand, center.previousTrackCommand,
                        center.skipForwardCommand, center.skipBackwardCommand] {
            command.isEnabled = false
        }
    }

    private func observeSystemEvents() {
        let center = NotificationCenter.default
        // The notification itself cannot cross into the actor, so each observer
        // reads what it needs on the delivery queue and passes those values on.
        observers.append(center.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil, queue: .main
        ) { [weak self] note in
            let type = (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
                .flatMap(AVAudioSession.InterruptionType.init(rawValue:))
            let options = AVAudioSession.InterruptionOptions(
                rawValue: note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            )
            MainActor.assumeIsolated {
                self?.handleInterruption(type, options: options)
            }
        })
        observers.append(center.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil, queue: .main
        ) { [weak self] note in
            let reason = (note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt)
                .flatMap(AVAudioSession.RouteChangeReason.init(rawValue:))
            MainActor.assumeIsolated { self?.handleRouteChange(reason) }
        })
    }

    private func handleInterruption(
        _ type: AVAudioSession.InterruptionType?,
        options: AVAudioSession.InterruptionOptions
    ) {
        guard let type else { return }
        switch type {
        case .began:
            wasPlayingBeforeInterruption = state == .playing
            pause()
        case .ended:
            // Two conditions, both required: the system has to say resuming is
            // appropriate, and sound had to actually be coming out when the
            // call arrived. A song that was already paused stays paused.
            if options.contains(.shouldResume), wasPlayingBeforeInterruption {
                resume()
            }
            wasPlayingBeforeInterruption = false
        @unknown default:
            break
        }
    }

    private func handleRouteChange(_ reason: AVAudioSession.RouteChangeReason?) {
        // Headphones pulled out: stop immediately rather than let the song
        // continue out of the speaker.
        if reason == .oldDeviceUnavailable { pause() }
    }

    private func updateNowPlaying() {
        guard let track else { return }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: track.title,
            MPMediaItemPropertyArtist: track.artists.joined(separator: ", "),
            MPNowPlayingInfoPropertyElapsedPlaybackTime: elapsed,
            MPNowPlayingInfoPropertyPlaybackRate: state.isPlaying ? 1.0 : 0.0,
        ]
        if let duration {
            info[MPMediaItemPropertyPlaybackDuration] = duration
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func clearNowPlaying() {
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }
}

/// The audio session, behind a seam so the controller can be tested off-device.
@MainActor
protocol MusicAudioSessioning {
    func activate() throws
    func deactivate()
}

struct SystemMusicAudioSession: MusicAudioSessioning {
    func activate() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playback, mode: .default)
        try session.setActive(true)
    }

    func deactivate() {
        // Failing to hand the session back is not worth surfacing: the song is
        // already over either way.
        try? AVAudioSession.sharedInstance().setActive(
            false, options: .notifyOthersOnDeactivation
        )
    }
}

/// `AVPlayer`, reduced to the six things the controller actually needs.
@MainActor
final class AVAudioPlayerEngine: AudioPlayerEngine {
    var onStatus: ((AudioPlayerStatus) -> Void)?
    var onTime: ((TimeInterval) -> Void)?

    // Unisolated so `deinit` can detach the observers it attached. Everything
    // else touches these on the main actor; `deinit` runs once, when nothing
    // else holds a reference.
    nonisolated(unsafe) private let player = AVPlayer()
    nonisolated(unsafe) private var timeObserver: Any?
    private var itemObservers: [NSKeyValueObservation] = []
    nonisolated(unsafe) private var endObserver: NSObjectProtocol?

    var duration: TimeInterval? {
        guard let seconds = player.currentItem?.duration.seconds,
              seconds.isFinite, seconds > 0
        else { return nil }
        return seconds
    }

    init() {
        player.actionAtItemEnd = .pause
        timeObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.5, preferredTimescale: 600), queue: .main
        ) { [weak self] time in
            MainActor.assumeIsolated { self?.onTime?(time.seconds) }
        }
    }

    deinit {
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
    }

    func load(url: URL) {
        detachItem()
        let item = AVPlayerItem(url: url)
        // `AVPlayerItem` delivers KVO on whatever thread changed the property,
        // which is not promised to be the main one. Each observation reads what
        // it needs where it lands and then hops; `assumeIsolated` here would be
        // a trap waiting for the one stall that arrives off-thread.
        itemObservers = [
            item.observe(\.status, options: [.new]) { [weak self] item, _ in
                guard item.status == .failed else { return }
                let retryable = Self.isRetryable(item.error)
                Task { @MainActor [weak self] in
                    self?.onStatus?(.failed(retryable: retryable))
                }
            },
            item.observe(\.isPlaybackBufferEmpty, options: [.new]) { [weak self] item, _ in
                guard item.isPlaybackBufferEmpty else { return }
                Task { @MainActor [weak self] in self?.onStatus?(.buffering) }
            },
            item.observe(\.isPlaybackLikelyToKeepUp, options: [.new]) { [weak self] item, _ in
                guard item.isPlaybackLikelyToKeepUp else { return }
                Task { @MainActor [weak self] in
                    guard let self, player.rate > 0 else { return }
                    onStatus?(.playing)
                }
            },
        ]
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.onStatus?(.ended) }
        }
        player.replaceCurrentItem(with: item)
    }

    func play() {
        player.play()
        if player.currentItem?.isPlaybackLikelyToKeepUp == true {
            onStatus?(.playing)
        } else {
            onStatus?(.buffering)
        }
    }

    func pause() {
        player.pause()
        onStatus?(.paused)
    }

    func seek(to seconds: TimeInterval) {
        player.seek(to: CMTime(seconds: seconds, preferredTimescale: 600))
    }

    func stop() {
        player.pause()
        detachItem()
        player.replaceCurrentItem(with: nil)
    }

    private func detachItem() {
        itemObservers.forEach { $0.invalidate() }
        itemObservers = []
        if let endObserver {
            NotificationCenter.default.removeObserver(endObserver)
            self.endObserver = nil
        }
    }

    private static func isRetryable(_ error: Error?) -> Bool {
        guard let error = error as NSError? else { return false }
        return error.domain == NSURLErrorDomain
    }
}
