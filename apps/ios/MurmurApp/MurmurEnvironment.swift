import Foundation
enum MurmurEnvironment {
    @MainActor
    static func makeAPIClient() -> any MurmurAPIClient {
#if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--murmur-ui-testing") {
            return UITestMurmurAPIClient()
        }
#endif
        let environment = ProcessInfo.processInfo.environment
        let bundleURL = Bundle.main.object(forInfoDictionaryKey: "MurmurAPIBaseURL") as? String
        let rawURL = environment["MURMUR_API_BASE_URL"] ?? bundleURL ?? defaultBaseURL
        guard let baseURL = URL(string: rawURL), !rawURL.isEmpty else {
            return UnavailableMurmurAPIClient(message: "尚未配置 Murmur 的 HTTPS 服务地址。")
        }
#if !DEBUG
        let bundleToken = Bundle.main.object(forInfoDictionaryKey: "MurmurDevelopmentToken") as? String
        let envToken = environment["MURMUR_DEV_BYPASS_TOKEN"]
        if [bundleToken, envToken].contains(where: { token in
            guard let token else { return false }
            return !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }) {
            fatalError("Release builds cannot include a Murmur development bypass.")
        }
        guard baseURL.scheme?.lowercased() == "https" else {
            return UnavailableMurmurAPIClient(message: "正式版只允许连接 HTTPS 服务。")
        }
#endif

        let authenticator: any MurmurAuthenticator
#if DEBUG
        // The bypass covers debug builds on a real device too, not just the
        // Simulator.  App Attest can only be verified against a paid team's
        // Team ID, so without one a device could not enrol at all.  The blast
        // radius stays at debug builds: the `#if !DEBUG` block above refuses
        // to launch a release build that carries a token, and release still
        // demands HTTPS.
        let bundleToken = Bundle.main.object(forInfoDictionaryKey: "MurmurDevelopmentToken") as? String
        if let token = environment["MURMUR_DEV_BYPASS_TOKEN"] ?? bundleToken, !token.isEmpty {
            authenticator = DevelopmentAuthenticator(token: token)
        } else {
            authenticator = AppAttestAuthenticator(environment: "development")
        }
#else
        authenticator = AppAttestAuthenticator(environment: "production")
#endif
        return URLSessionMurmurAPIClient(baseURL: baseURL, authenticator: authenticator)
    }

    /// UI tests run against a persisted transcript, so without somewhere to put
    /// it they inherit whatever the last test said.  Under the UI-testing flag
    /// the history goes to a directory of its own that a second flag empties,
    /// which lets one test still check that a moment survives a cold launch.
    @MainActor
    static func makeTranscriptStore() -> MurmurTranscriptStore {
#if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--murmur-ui-testing") {
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("murmur-ui-transcript", isDirectory: true)
            if arguments.contains("--murmur-reset-transcript") {
                try? FileManager.default.removeItem(at: directory)
            }
            if arguments.contains("--murmur-seed-diary") {
                let rows = [
                    MurmurMessage(id:"diary-yesterday",author:.you,text:"昨天整理了旅行照片。",sentAt:Calendar.current.date(byAdding:.day,value:-1,to:Date())!),
                    MurmurMessage(id:"diary-today",author:.you,text:"想恢复周末散步的习惯。",sentAt:Date(),delivery:.answered),
                    MurmurMessage(id:"diary-answer",author:.murmur,text:"开始的时间还没有确定。",sentAt:Date().addingTimeInterval(1))
                ]
                let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
                try? FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
                if let data = try? encoder.encode(rows) { try? data.write(to:directory.appendingPathComponent("transcript.json"),options:.atomic) }
            }
            if arguments.contains("--murmur-seed-long-transcript") {
                seedLongUITestTranscript(in: directory)
            }
            return MurmurTranscriptStore(directory: directory)
        }
#endif
        return MurmurTranscriptStore()
    }

#if DEBUG
    /// A deterministic scrollback longer than an iPhone viewport. UI tests use
    /// it to exercise tab teardown/recreation without spending a minute sending
    /// enough moments through the stub server first.
    private static func seedLongUITestTranscript(in directory: URL) {
        let rows = (0..<40).map { index in
            MurmurMessage(
                id: "ui-long-transcript-\(index)",
                author: index.isMultiple(of: 2) ? .you : .murmur,
                text: index == 39
                    ? "回到聊天应该立刻看见我"
                    : "预置聊天记录第 \(index + 1) 条",
                sentAt: Date(timeIntervalSinceReferenceDate: 780_000_000 + Double(index)),
                delivery: index.isMultiple(of: 2) ? .answered : .sent,
                momentID: "ui-long-moment-\(index / 2)"
            )
        }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(rows) else { return }
        try? FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.complete]
        )
        try? data.write(
            to: directory.appendingPathComponent("transcript.json"),
            options: [.atomic, .completeFileProtection]
        )
    }
#endif

    /// 当年今日's archive, redirected the same way and under the same flags —
    /// a UI test that opened a room yesterday must not leave a mark on today's
    /// calendar for the next one to trip over.
    @MainActor
    static func makeArchive() -> MurmurArchive {
#if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--murmur-ui-testing") {
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("murmur-ui-archive", isDirectory: true)
            if arguments.contains("--murmur-reset-transcript") {
                try? FileManager.default.removeItem(at: directory)
            }
            let seedDaysAgo: Int?
            if arguments.contains("--murmur-seed-historical-archive") {
                seedDaysAgo = 2
            } else if arguments.contains("--murmur-seed-today-archive") {
                seedDaysAgo = 0
            } else {
                seedDaysAgo = nil
            }
            if let seedDaysAgo {
                let calendar = Calendar.murmur
                let day = calendar.date(byAdding: .day, value: -seedDaysAgo, to: Date()) ?? Date()
                return MurmurArchive(
                    store: MurmurTranscriptStore(directory: directory),
                    initialRows: [
                        MurmurMessage(
                            author: .murmur,
                            text: "这是一条旧日期里的回答",
                            sentAt: day,
                            momentID: "ui-historical-seed"
                        )
                    ]
                )
            }
            return MurmurArchive(store: MurmurTranscriptStore(directory: directory))
        }
#endif
        return MurmurArchive()
    }

    private static var defaultBaseURL: String {
#if DEBUG
        "http://127.0.0.1:8766"
#else
        ""
#endif
    }
}

private actor UnavailableMurmurAPIClient: MurmurAPIClient {
    let message: String

    init(message: String) {
        self.message = message
    }

    func storedIdentity() async throws -> MurmurIdentity? { nil }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity { throw unavailable }
    func createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String, intent: MurmurMomentIntent?, contextMomentIDs: [String]) async throws -> MomentReceipt { throw unavailable }
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        AsyncThrowingStream { $0.finish(throwing: unavailable) }
    }
    func currentProactive() async throws -> ProactiveMoment? { throw unavailable }
    func acknowledge(momentID: String, reply: String?) async throws { throw unavailable }
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws { throw unavailable }
    func devices() async throws -> [MurmurDevice] { throw unavailable }
    func removeDevice(deviceID: String) async throws { throw unavailable }
    func preferences() async throws -> MurmurPreferences { throw unavailable }
    func updatePreferences(_ preferences: MurmurPreferences) async throws { throw unavailable }
    func musicAvailability() async throws -> MusicFeatureAvailability { throw unavailable }
    func reportMusicPlayback(_ event: MusicPlaybackEvent) async throws { throw unavailable }
    func resetLocalIdentity() async throws {}
    func deleteAccount() async throws { throw unavailable }

    private var unavailable: MurmurFailure {
        .init(code: "not_configured", message: message, retryable: false)
    }
}

#if DEBUG
private actor UITestMurmurAPIClient: MurmurAPIClient {
    private let identity = MurmurIdentity(userID: "ui-user", deviceID: "ui-device", keyID: "ui-key")
    /// Every moment needs its own id.  Handing the same one back twice gives
    /// two replies the same message id, and the transcript's `ForEach` then
    /// draws only the first of them.
    private var moments = 0
    /// Under `--murmur-fail-first-send` the first send of the run does not
    /// land and every one after it does.  That is what makes the failure mark
    /// reachable on screen, and testable as an offer rather than a dead end:
    /// press it and the same row goes through.
    private let failsFirstSend = ProcessInfo.processInfo.arguments.contains("--murmur-fail-first-send")
    private var sends = 0
    /// Which moments came in as 当年今日 readings, so their event stream can
    /// answer with a guess and three openers instead of an ordinary reply.
    private var readings: Set<String> = []
    private var notesByMoment: [String: String] = [:]
    /// Under `--murmur-stub-reading-fails` the room's opening upload never
    /// lands.  That is what makes the room's own failure state reachable.
    private let failsReading = ProcessInfo.processInfo.arguments.contains("--murmur-stub-reading-fails")
    private let arguments = ProcessInfo.processInfo.arguments
    private var listenTogetherRoom: ListenTogetherRoomSnapshotV1?

    private var diaryText = "想恢复周末散步的习惯。"
    private var diaryForgotten = false
    private var diaryEdited = false
    private var answeredQuestionMoments: Set<String> = []
    func dailyReviews(_ request: MurmurReviewRequest) async throws -> Data {
        guard arguments.contains("--murmur-stub-diary") else { return Data("{\"available\":false}".utf8) }
        let day = MurmurDay.key(Date())
        let preferences: [String:Any] = ["enabled":true,"local_time":"22:30","first_day":day]
        let stamp = ISO8601DateFormatter().string(from:Date())
        let result: [String:Any]
        switch request.operation {
        case .configuration: result = ["available":true,"preferences":preferences]
        case .preferences: result = preferences
        case .dates: result = ["dates":[["day":day,"status":"ready","version":diaryEdited ? 2 : 1,"updated_at":stamp]]]
        case .edit: diaryText = (try? JSONSerialization.jsonObject(with:request.body) as? [String:String])?["text"] ?? diaryText; diaryEdited = true; result = ["id":"memory-walk","text":diaryText,"forgotten":false]
        case .forget: diaryForgotten = true; result = ["id":"memory-walk","forgotten":true]
        case .detail, .refresh:
            let memories: [[String:Any]] = diaryForgotten ? [] : [["id":"memory-walk","text":diaryText,"evidence":"user_stated","source_ids":["diary-today"],"edited":diaryEdited,"updated_at":stamp]]
            result = ["day":day,"timezone":TimeZone.current.identifier,"status":"ready","summary":diaryForgotten ? "相关记忆已撤销。" : "今天你回忆了前年的一次旅行，并提到想恢复周末散步。开始时间还没有确定。", "question":"如果周末留出一段散步时间，你最需要先安排好什么？","version":diaryEdited ? 2 : 1,"updated_at":stamp,"memories":memories,"sources":[["id":"diary-today","occurred_at":stamp,"channel":"home","user_text":"想恢复周末散步的习惯。","observation":""]]]
        }
        return try JSONSerialization.data(withJSONObject:result)
    }

    func createMoment(note: String?, photo: PhotoAttachment?, musicTrack: MusicTrackAttachmentV1?, idempotencyKey: String, intent: MurmurMomentIntent?, contextMomentIDs: [String], dailyQuestionDay: String?) async throws -> MomentReceipt {
        if let day = dailyQuestionDay, day != MurmurDay.key(Date()) {
            throw MurmurFailure(code: "not_found", message: "问题日期不匹配。", retryable: false)
        }
        let receipt = try await createMoment(note: note, photo: photo, idempotencyKey: idempotencyKey, intent: intent, contextMomentIDs: contextMomentIDs)
        if dailyQuestionDay != nil { answeredQuestionMoments.insert(receipt.momentID) }
        return receipt
    }
    func storedIdentity() async throws -> MurmurIdentity? {
        identity
    }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity { identity }
    func createMoment(
        note: String?, photo: PhotoAttachment?, idempotencyKey: String,
        intent: MurmurMomentIntent?, contextMomentIDs: [String]
    ) async throws -> MomentReceipt {
        sends += 1
        if failsFirstSend, sends == 1 {
            throw MurmurFailure(code: "network_error", message: "暂时没有连上 Murmur。", retryable: true)
        }
        if intent == .photoReading, failsReading {
            throw MurmurFailure(code: "network_error", message: "暂时没有连上 Murmur。", retryable: true)
        }
        moments += 1
        let momentID = "ui-moment-\(moments)"
        if intent == .photoReading { readings.insert(momentID) }
        if let note { notesByMoment[momentID] = note }
        return .init(momentID: momentID, status: "queued")
    }
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> {
        let isReading = readings.contains(momentID)
        let note = notesByMoment[momentID]
        let answeredQuestion = answeredQuestionMoments.contains(momentID)
        return AsyncThrowingStream { continuation in
            // Production sequence numbers restart for every moment.  Keeping
            // that wire shape in UI tests guards the room-wide SwiftUI IDs.
            continuation.yield(.accepted(id: "1"))
            if isReading {
                continuation.yield(.bubble(id: "2", text: "这是……刚下过雨？"))
                continuation.yield(.angles(
                    id: "3",
                    texts: ["那天的天气", "右边那个人", "上次说要再来"]
                ))
            } else {
                let answer: String
                switch note {
                case "日期续聊第一句": answer = "接住第一句"
                case "日期续聊第二句": answer = "接住第二句"
                default: answer = "这一刻，我收到了。"
                }
                continuation.yield(.bubble(id: "2", text: answeredQuestion ? "已接上这一天的问题。" : answer))
            }
            continuation.yield(.done(id: "4", move: nil, scene: nil))
            continuation.finish()
        }
    }
    func currentProactive() async throws -> ProactiveMoment? { nil }
    func acknowledge(momentID: String, reply: String?) async throws {}
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws {
        if arguments.contains("--murmur-stub-netease-offline") { throw URLError(.notConnectedToInternet) }
    }
    func devices() async throws -> [MurmurDevice] { [] }
    func removeDevice(deviceID: String) async throws {}
    func preferences() async throws -> MurmurPreferences { MurmurPreferences() }
    func updatePreferences(_ preferences: MurmurPreferences) async throws {}
    func musicAvailability() async throws -> MusicFeatureAvailability {
        guard arguments.contains(where: { $0.hasPrefix("--murmur-stub-netease-") }) else {
            return MusicFeatureAvailability(
                enabled: false, provider: "audius", playbackReporting: false
            )
        }
        return MusicFeatureAvailability(
            enabled: false,
            provider: "audius",
            playbackReporting: false,
            providers: [
                MusicProviderCapabilityV1(
                    id: MusicProvider.netease.rawValue,
                    capabilities: arguments.contains("--murmur-stub-netease-no-search")
                        ? ["resolve_shared"] : ["search", "resolve_shared"]
                )
            ],
            listenTogether: ListenTogetherCapabilityV1(
                enabled: true,
                provider: MusicProvider.netease.rawValue,
                commands: ListenTogetherCommand.allCases.map(\.rawValue)
            )
        )
    }
    func resolveSharedMusic(
        text: String,
        idempotencyKey: String
    ) async throws -> MusicTrackAttachmentV1 {
        uiNeteaseTrack
    }
    func searchMusic(query: String, limit: Int) async throws -> [MusicTrackAttachmentV1] {
        // 一页固定三首，够一个列表看出行与行之间的差别；空查询走空结果那条路。
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return [] }
        if arguments.contains("--murmur-stub-netease-search-empty") { return [] }
        return [
            uiNeteaseTrack,
            MusicTrackAttachmentV1(
                provider: MusicProvider.netease.rawValue,
                trackID: "186017",
                title: "晴天",
                artists: ["周杰伦"],
                artworkURL: nil,
                canonicalURL: URL(string: "https://music.163.com/song?id=186017")!,
                durationSeconds: 269,
                explicit: false
            ),
            MusicTrackAttachmentV1(
                provider: MusicProvider.netease.rawValue,
                trackID: "186018",
                title: "夜曲",
                artists: ["周杰伦"],
                artworkURL: nil,
                canonicalURL: URL(string: "https://music.163.com/song?id=186018")!,
                durationSeconds: 226,
                explicit: false
            ),
        ]
    }
    func searchMusic(
        query: String,
        limit: Int,
        purpose: MusicSearchPurpose
    ) async throws -> SearchMusicResponseV1 {
        if arguments.contains("--murmur-stub-netease-search-no-common") {
            return SearchMusicResponseV1(
                tracks: [], emptyReason: .noCommonPlayableTrack
            )
        }
        return SearchMusicResponseV1(
            tracks: try await searchMusic(query: query, limit: limit)
        )
    }
    func createListenTogetherRoom(
        initialTrack: MusicTrackAttachmentV1,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1 {
        if arguments.contains("--murmur-stub-netease-create-fails") {
            throw MurmurFailure(
                code: "room_create_failed",
                message: "没有建好一起听，请再试一次。",
                retryable: true
            )
        }
        let room = uiRoom(state: .waitingForUser, playback: .unknown, joined: false)
        listenTogetherRoom = room
        return room
    }
    func currentListenTogetherRoom() async throws -> ListenTogetherRoomSnapshotV1? {
        if let listenTogetherRoom { return listenTogetherRoom }
        let room: ListenTogetherRoomSnapshotV1?
        if arguments.contains("--murmur-stub-netease-waiting") {
            room = uiRoom(state: .waitingForUser, playback: .unknown, joined: false)
        } else if arguments.contains("--murmur-stub-netease-paused") {
            room = uiRoom(state: .connected, playback: .paused, joined: true)
        } else if arguments.contains("--murmur-stub-netease-syncing") {
            room = uiRoom(
                state: .syncing,
                playback: .unknown,
                joined: true,
                pendingCommand: .pause
            )
        } else if arguments.contains("--murmur-stub-netease-room-failed") {
            room = uiRoom(
                state: .failed,
                playback: .unknown,
                joined: false,
                errorCode: "invite_expired"
            )
        } else if arguments.contains("--murmur-stub-netease-rights") {
            room = uiRoom(
                state: .connected,
                playback: .playing,
                joined: true,
                errorCode: "counterpart_rights_unavailable"
            )
        } else if arguments.contains("--murmur-stub-netease-playing")
                    || arguments.contains("--murmur-stub-netease-command-fails")
                    || arguments.contains("--murmur-stub-netease-offline") {
            room = uiRoom(state: .connected, playback: .playing, joined: true)
        } else {
            room = nil
        }
        listenTogetherRoom = room
        return room
    }
    func commandListenTogetherRoom(
        handle: String,
        command: ListenTogetherCommand,
        track: MusicTrackAttachmentV1?,
        idempotencyKey: String
    ) async throws -> ListenTogetherCommandResultV1 {
        let failed = arguments.contains("--murmur-stub-netease-command-fails")
        let playback: ListenTogetherPlaybackState = switch command {
        case .pause: failed ? .playing : .paused
        case .resume: .playing
        case .previous, .next, .playTrack: .playing
        }
        let room = uiRoom(
            state: .connected,
            playback: playback,
            joined: true,
            errorCode: failed ? "command_rejected" : nil,
            track: track
        )
        listenTogetherRoom = room
        return .init(status: failed ? .failed : .synchronized, room: room)
    }
    func closeListenTogetherRoom(
        handle: String,
        idempotencyKey: String
    ) async throws -> ListenTogetherRoomSnapshotV1 {
        listenTogetherRoom = nil
        return uiRoom(state: .ended, playback: .unknown, joined: false)
    }
    func resetLocalIdentity() async throws {}
    func deleteAccount() async throws {}

    private var uiNeteaseTrack: MusicTrackAttachmentV1 {
        MusicTrackAttachmentV1(
            provider: MusicProvider.netease.rawValue,
            trackID: "186016",
            title: "花海",
            artists: ["周杰伦"],
            artworkURL: nil,
            canonicalURL: URL(string: "https://music.163.com/song?id=186016")!,
            durationSeconds: 264,
            explicit: false
        )
    }

    private func uiRoom(
        state: ListenTogetherRoomState,
        playback: ListenTogetherPlaybackState,
        joined: Bool,
        pendingCommand: ListenTogetherCommand? = nil,
        errorCode: String? = nil,
        track: MusicTrackAttachmentV1? = nil
    ) -> ListenTogetherRoomSnapshotV1 {
        ListenTogetherRoomSnapshotV1(
            roomHandle: "ui-room",
            state: state,
            currentTrack: track ?? uiNeteaseTrack,
            userJoined: joined,
            pendingCommand: pendingCommand?.rawValue,
            inviteURL: URL(string: "https://music.163.com/listen-together/invite/ui-room"),
            updatedAt: "2026-09-01T00:00:00Z",
            errorCode: errorCode,
            playbackState: playback
        )
    }
}
#endif
