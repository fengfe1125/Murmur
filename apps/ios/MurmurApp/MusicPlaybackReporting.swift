import Foundation

protocol MusicPlaybackReporting: Sendable {
    func record(_ event: MusicPlaybackEvent) async
    func recoverInterruptedSession() async
    func flush() async
}

protocol MusicPlaybackEventTransport: Sendable {
    func send(_ event: MusicPlaybackEvent) async throws
}

/// A privacy-minimal outbox: only the latest transition is useful context.
/// New offline state replaces old state, so reconnecting cannot replay a
/// detailed listening history.
actor MusicPlaybackOutbox {
    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder
    private var loaded = false
    private var event: MusicPlaybackEvent?

    init(directory: URL? = nil) {
        let base = directory ?? FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Murmur", isDirectory: true)
        self.fileURL = base.appendingPathComponent("music-playback-outbox.json")
        self.encoder = JSONEncoder()
        self.decoder = JSONDecoder()
        self.encoder.dateEncodingStrategy = .iso8601
        self.decoder.dateDecodingStrategy = .iso8601
    }

    func replace(with event: MusicPlaybackEvent) {
        loadIfNeeded()
        self.event = event
        save()
    }

    func pending() -> MusicPlaybackEvent? {
        loadIfNeeded()
        return event
    }

    func clear(ifMatching sent: MusicPlaybackEvent) {
        loadIfNeeded()
        guard event == sent else { return }
        event = nil
        save()
    }

    func clear() {
        loaded = true
        event = nil
        try? FileManager.default.removeItem(at: fileURL)
    }

    private func loadIfNeeded() {
        guard !loaded else { return }
        loaded = true
        guard let data = try? Data(contentsOf: fileURL) else { return }
        event = try? decoder.decode(MusicPlaybackEvent.self, from: data)
    }

    private func save() {
        guard let event else {
            try? FileManager.default.removeItem(at: fileURL)
            return
        }
        do {
            let directory = fileURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]
            )
            let data = try encoder.encode(event)
            try data.write(to: fileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        } catch {
            // Playback is never blocked by telemetry persistence. The next
            // transition will try this one-slot write again.
        }
    }
}

actor MusicPlaybackEventReporter: MusicPlaybackReporting {
    private let transport: any MusicPlaybackEventTransport
    private let outbox: MusicPlaybackOutbox
    private var isFlushing = false

    init(
        transport: any MusicPlaybackEventTransport,
        outbox: MusicPlaybackOutbox = MusicPlaybackOutbox()
    ) {
        self.transport = transport
        self.outbox = outbox
    }

    func record(_ event: MusicPlaybackEvent) async {
        await outbox.replace(with: event)
        await flush()
    }

    /// A process restart never resumes audio. If the one persisted transition
    /// says it had been playing, close that session with a newer `stopped`
    /// state before trying to send anything.
    func recoverInterruptedSession() async {
        guard let pending = await outbox.pending() else { return }
        if pending.state == .started || pending.state == .resumed {
            await outbox.replace(with: MusicPlaybackEvent(
                version: pending.version,
                sessionID: pending.sessionID,
                sequence: pending.sequence + 1,
                state: .stopped,
                track: pending.track,
                occurredAt: Date()
            ))
        }
        await flush()
    }

    func flush() async {
        guard !isFlushing else { return }
        isFlushing = true
        defer { isFlushing = false }
        while let event = await outbox.pending() {
            do {
                try await transport.send(event)
                // Recording can interleave while transport is awaiting. Only
                // clear the exact state just sent; a newer replacement stays.
                await outbox.clear(ifMatching: event)
            } catch {
                return
            }
        }
    }
}

actor DisabledMusicPlaybackReporter: MusicPlaybackReporting {
    func record(_ event: MusicPlaybackEvent) async {}
    func recoverInterruptedSession() async {}
    func flush() async {}
}
