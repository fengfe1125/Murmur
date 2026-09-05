import Combine
import Foundation

/// Not yet told whether Murmur wants playback reports.
struct MusicReportingUndecided: Error {}

/// Sends one discrete transition to Murmur, unless the server has said it does
/// not want them.
///
/// Three states, not two, and the third is the point. Before the server has
/// answered, a transition is *kept*: a cold start whose last written-down state
/// says "playing" has to be able to close that session once reporting turns on,
/// and a client that dropped it would leave the model believing someone is
/// still listening until the server's own TTL expired.
actor MurmurPlaybackTransport: MusicPlaybackEventTransport {
    private let api: any MurmurAPIClient
    private var isEnabled: Bool?

    init(api: any MurmurAPIClient) {
        self.api = api
    }

    func setEnabled(_ value: Bool) {
        isEnabled = value
    }

    func send(_ event: MusicPlaybackEvent) async throws {
        switch isEnabled {
        case true:
            try await api.reportMusicPlayback(event)
        case false:
            // Returning rather than throwing drops the state instead of
            // queueing it. A transition Murmur has said it does not want is
            // not something to deliver later.
            return
        case nil:
            throw MusicReportingUndecided()
        }
    }
}

/// The player's state, flattened to what a transcript row needs.
///
/// Rows must not each observe the player: there can be hundreds of them, and a
/// row only ever asks one question — is this my song, and what is it doing?
struct MusicNowPlaying: Equatable, Sendable {
    var trackID: String?
    var state: MusicPlaybackState = .idle

    static let none = MusicNowPlaying()

    func playback(for track: MusicTrackAttachmentV1) -> MusicCardPlayback {
        MusicCardPlayback(playerState: state, isCurrent: trackID == track.trackID)
    }
}

/// Everything the music feature is, assembled once.
///
/// It is built whether or not music is on, so that no screen has to hold an
/// optional object — SwiftUI cannot observe one, and the alternative was a
/// wrapper view around half the app. What is conditional is `isAvailable`, and
/// two independent gates have to agree on it: this build must carry an Audius
/// registration, and Murmur's own server must say this account may use the
/// wire. The second can change while the app is running, which is why it
/// arrives through `apply(_:)` rather than being decided in `init`.
@MainActor
final class MusicModule: ObservableObject {
    /// Whether Murmur's server currently allows this account any music at all.
    /// Everything on screen hangs off this: with it false there is no entry
    /// point, no search and no new playback — only the cards already in the
    /// transcript, which keep their metadata and their Audius link.
    @Published private(set) var isAvailable = false
    /// Mirrored off the player so one observer covers both: a screen that holds
    /// the module sees playback change without also subscribing to the player.
    @Published private(set) var nowPlaying = MusicNowPlaying.none
    @Published private(set) var isNeteaseCatalogAvailable = false
    @Published private(set) var isNeteaseSearchAvailable = false
    @Published private(set) var isListenTogetherAvailable = false
    /// Whether the server has answered about this account at all yet.
    ///
    /// `musicAvailability` starts hard-coded to off and is only replaced once
    /// `bootstrap()` lands, so "off" and "not asked yet" are the same value.
    /// That window used to be invisible; a permanently visible 一起听 tab can
    /// be tapped inside it, and telling somebody the feature is not open to
    /// them and then flipping is worse than a moment of nothing.
    @Published private(set) var hasAppliedAvailability = false

    let netease: NeteaseMusicModel

    let account: AudiusAccountModel
    let player: MusicPlaybackController
    /// The catalogue side: search, favourites, playlists.
    let library: AudiusClient

    /// Whether this build carries an Audius registration at all. Without one
    /// no server switch can turn music on.
    let isConfigured: Bool

    private let transport: MurmurPlaybackTransport
    private let reporter: MusicPlaybackEventReporter

    init(
        api: any MurmurAPIClient,
        configuration: MusicFeatureConfiguration? = .from()
    ) {
        self.isConfigured = configuration != nil
        self.netease = NeteaseMusicModel(api: api)
        let client = AudiusClient(configuration: configuration ?? .unconfigured)
        let transport = MurmurPlaybackTransport(api: api)
        self.library = client
        self.transport = transport
        self.account = AudiusAccountModel(client: client)
        let reporter = MusicPlaybackEventReporter(transport: transport)
        self.reporter = reporter
        let player = MusicPlaybackController(resolver: client, reporter: reporter)
        self.player = player
        player.$state
            .combineLatest(player.$track)
            .map { MusicNowPlaying(trackID: $1?.trackID, state: $0) }
            .removeDuplicates()
            .assign(to: &$nowPlaying)
    }

    /// Take what the server said about this account.
    func apply(_ availability: MusicFeatureAvailability) {
        let allowed = isConfigured && availability.enabled
            && availability.provider == "audius"
        isAvailable = allowed
        isNeteaseCatalogAvailable = availability.providers.contains {
            $0.id == MusicProvider.netease.rawValue
                && $0.capabilities.contains("resolve_shared")
        }
        isNeteaseSearchAvailable = availability.providers.contains {
            $0.id == MusicProvider.netease.rawValue
                && $0.capabilities.contains("search")
        }
        isListenTogetherAvailable = availability.listenTogether?.enabled == true
            && availability.listenTogether?.provider == MusicProvider.netease.rawValue
        hasAppliedAvailability = true
        let reporting = allowed && availability.playbackReporting
        // Ordered inside one task: whatever the player has been holding since
        // launch is only now allowed to go — or to be dropped.
        Task { [transport, reporter] in
            await transport.setEnabled(reporting)
            await reporter.flush()
        }
        if !allowed {
            // Turning the feature off mid-session stops the sound too. History
            // stays on screen; the speaker does not stay on.
            player.stop()
        }
    }

    /// Murmur's identity changed or went away. Audius credentials are
    /// device-local, and must not carry from one invited person to the next.
    func forgetAudiusAccount() async {
        player.stop()
        await account.clearForMurmurIdentityChange()
    }
}
