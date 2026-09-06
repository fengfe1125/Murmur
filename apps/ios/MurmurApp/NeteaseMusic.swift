import Foundation
import SwiftUI

enum NeteaseSharedTextDetector {
    /// 服务端 `app_music_links.MAX_SHARED_TEXT_BYTES`，两边必须一致。
    static let maxSharedTextBytes = 8 * 1024

    private static let longHosts: Set<String> = ["music.163.com", "y.music.163.com"]
    private static let shortHosts: Set<String> = ["163cn.tv"]

    /// This is only a cheap UI routing check. The server still follows short
    /// links with a strict redirect policy and re-fetches trusted metadata.
    static func containsCandidate(in text: String) -> Bool {
        // 和服务端 MAX_SHARED_TEXT_BYTES 同一个数。客户端放行到比服务端更
        // 宽的地方，只会让 8–16KiB 那一段在设备上被接受、到服务端才 413。
        guard text.utf8.count <= maxSharedTextBytes else { return false }
        return links(in: text).contains { url in
            // https only, exactly like the server: NSDataDetector also reports
            // bare `music.163.com/...` text as a link, and routing something the
            // server is required to reject would only produce a confusing error.
            guard url.scheme?.lowercased() == "https",
                  let host = url.host?.lowercased() else { return false }
            if shortHosts.contains(host) { return true }
            guard longHosts.contains(host) else { return false }
            if url.path.lowercased().contains("/song") {
                return URLComponents(url: url, resolvingAgainstBaseURL: false)?
                    .queryItems?.contains(where: { $0.name == "id" && !($0.value ?? "").isEmpty }) == true
            }
            if let fragment = url.fragment,
               let fragmentURL = URL(string: "https://music.163.com/\(fragment)") {
                return fragmentURL.path.lowercased().contains("/song")
                    && URLComponents(url: fragmentURL, resolvingAgainstBaseURL: false)?
                        .queryItems?.contains(where: { $0.name == "id" && !($0.value ?? "").isEmpty }) == true
            }
            return false
        }
    }

    private static func links(in text: String) -> [URL] {
        guard let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) else {
            return []
        }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        return detector.matches(in: text, range: range).compactMap(\.url)
    }
}

struct ResolveSharedMusicResponseV1: Codable, Equatable, Sendable {
    let track: MusicTrackAttachmentV1
}

enum MusicSearchPurpose: String, Codable, Equatable, Sendable {
    case share
    case listenTogether = "listen_together"
}

enum MusicSearchEmptyReason: String, Codable, Equatable, Sendable {
    case noCommonPlayableTrack = "no_common_playable_track"
}

struct SearchMusicResponseV1: Codable, Equatable, Sendable {
    let tracks: [MusicTrackAttachmentV1]
    let emptyReason: MusicSearchEmptyReason?

    enum CodingKeys: String, CodingKey {
        case tracks
        case emptyReason = "empty_reason"
    }

    init(
        tracks: [MusicTrackAttachmentV1],
        emptyReason: MusicSearchEmptyReason? = nil
    ) {
        self.tracks = tracks
        self.emptyReason = emptyReason
    }
}

enum ListenTogetherRoomState: String, Codable, Sendable {
    case creating
    case waitingForUser = "waiting_for_user"
    case connected
    case syncing
    case ended
    case failed
}

enum ListenTogetherPlaybackState: String, Codable, Sendable {
    case playing
    case paused
    case unknown
}

struct ListenTogetherRoomSnapshotV1: Codable, Equatable, Identifiable, Sendable {
    let version: Int
    let roomHandle: String
    let state: ListenTogetherRoomState
    let currentTrack: MusicTrackAttachmentV1?
    let userJoined: Bool
    let pendingCommand: String?
    let inviteURL: URL?
    let updatedAt: String
    let errorCode: String?
    let playbackState: ListenTogetherPlaybackState

    var id: String { roomHandle }
    var isActive: Bool { state != .ended && state != .failed }

    enum CodingKeys: String, CodingKey {
        case version
        case roomHandle = "room_handle"
        case state
        case currentTrack = "current_track"
        case userJoined = "user_joined"
        case pendingCommand = "pending_command"
        case inviteURL = "invite_url"
        case updatedAt = "updated_at"
        case errorCode = "error_code"
        case playbackState = "playback_state"
    }

    init(
        version: Int = 1,
        roomHandle: String,
        state: ListenTogetherRoomState,
        currentTrack: MusicTrackAttachmentV1? = nil,
        userJoined: Bool = false,
        pendingCommand: String? = nil,
        inviteURL: URL? = nil,
        updatedAt: String,
        errorCode: String? = nil,
        playbackState: ListenTogetherPlaybackState = .unknown
    ) {
        self.version = version
        self.roomHandle = roomHandle
        self.state = state
        self.currentTrack = currentTrack
        self.userJoined = userJoined
        self.pendingCommand = pendingCommand
        self.inviteURL = inviteURL
        self.updatedAt = updatedAt
        self.errorCode = errorCode
        self.playbackState = playbackState
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        version = try values.decodeIfPresent(Int.self, forKey: .version) ?? 1
        roomHandle = try values.decode(String.self, forKey: .roomHandle)
        state = try values.decode(ListenTogetherRoomState.self, forKey: .state)
        currentTrack = try values.decodeIfPresent(MusicTrackAttachmentV1.self, forKey: .currentTrack)
        userJoined = try values.decodeIfPresent(Bool.self, forKey: .userJoined) ?? false
        pendingCommand = try values.decodeIfPresent(String.self, forKey: .pendingCommand)
        inviteURL = try values.decodeIfPresent(URL.self, forKey: .inviteURL)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt) ?? ""
        errorCode = try values.decodeIfPresent(String.self, forKey: .errorCode)
        playbackState = try values.decodeIfPresent(
            ListenTogetherPlaybackState.self,
            forKey: .playbackState
        ) ?? .unknown
    }
}

enum ListenTogetherCommand: String, Codable, CaseIterable, Sendable {
    case pause
    case resume
    case previous
    case next
    case playTrack = "play_track"
}

enum ListenTogetherCommandStatus: String, Codable, Sendable {
    case accepted
    case synchronized
    case failed
}

enum ListenTogetherRetryAction: Equatable, Sendable {
    case create(track: MusicTrackAttachmentV1, idempotencyKey: String)
    case command(
        command: ListenTogetherCommand,
        track: MusicTrackAttachmentV1?,
        roomHandle: String,
        idempotencyKey: String
    )

    var track: MusicTrackAttachmentV1? {
        switch self {
        case .create(let track, _), .command(_, let track?, _, _): track
        case .command(_, nil, _, _): nil
        }
    }

    var command: ListenTogetherCommand? {
        if case .command(let command, _, _, _) = self { return command }
        return nil
    }
}

enum ListenTogetherPresentationState: Equatable, Sendable {
    case inactive
    case creating(MusicTrackAttachmentV1)
    case creationFailed(MusicTrackAttachmentV1, String)
    case waiting
    case playing
    case paused
    case syncing(ListenTogetherCommand?)
    case commandFailed(ListenTogetherCommand?, ListenTogetherPlaybackState)
    case roomFailed(String?)
    case offline

    static func resolve(
        connection: MurmurConnectionState,
        room: ListenTogetherRoomSnapshotV1?,
        commandStatus: ListenTogetherCommandStatus?,
        isChanging: Bool,
        mutation: ListenTogetherRetryAction?,
        failureMessage: String?,
        lastConfirmedPlayback: ListenTogetherPlaybackState
    ) -> Self {
        if let mutation {
            if isChanging {
                if case .create(let track, _) = mutation, room == nil {
                    return .creating(track)
                }
                return .syncing(mutation.command)
            }
            if commandStatus == .failed {
                if case .create(let track, _) = mutation, room == nil {
                    return .creationFailed(track, failureMessage ?? "没有建好一起听，请再试一次。")
                }
                return .commandFailed(mutation.command, lastConfirmedPlayback)
            }
        }
        guard let room else { return .inactive }
        if case .offline = connection { return .offline }
        if room.state == .failed { return .roomFailed(room.errorCode) }
        if room.state == .ended { return .inactive }
        if commandStatus == .failed {
            return .commandFailed(mutation?.command, lastConfirmedPlayback)
        }
        if isChanging || commandStatus == .accepted || room.state == .syncing {
            return .syncing(room.pendingCommand.flatMap(ListenTogetherCommand.init(rawValue:)))
        }
        if room.state == .creating || room.state == .waitingForUser || !room.userJoined {
            return .waiting
        }
        // 网易云参与者选到机器人账号不可播的版本时，服务端会把歌恢复到上一首，
        // 并在这个仍然有效的房间快照上带一个非阻塞错误码。若恢复快照暂时没有
        // playback_state，就沿用最后确认状态，不能因此把控制面永久卡成「同步中」。
        if room.errorCode == ListenTogetherRoomSnapshotV1.counterpartRightsUnavailable,
           room.playbackState == .unknown {
            return switch lastConfirmedPlayback {
            case .paused: .paused
            case .playing, .unknown: .playing
            }
        }
        return switch room.playbackState {
        case .playing: .playing
        case .paused: .paused
        case .unknown: .syncing(nil)
        }
    }
}

/// 一起听的一整套说法，挂在状态本身上而不是某一个界面里。
///
/// 聊天页那张小卡片和「一起听」那一整屏说的是同一件事，只是地方大小不同。
/// 文案留在某个 view 的 private 属性里，第二个界面就只能抄一份——抄完的那天
/// 起，两处就开始各自漂移。
extension ListenTogetherPresentationState {
    var statusTag: String {
        switch self {
        case .creating: "正在创建"
        case .creationFailed: "创建失败"
        case .waiting: "等待加入"
        case .playing: "已连接"
        case .paused: "已暂停"
        case .syncing: "同步中"
        case .commandFailed: "同步失败"
        case .roomFailed: "邀请过期"
        case .offline: "连接异常"
        case .inactive: ""
        }
    }

    var statusLine: String {
        switch self {
        case .creating: "正在创建一起听…"
        case .creationFailed: "没有建好一起听"
        case .waiting: "等待加入 / 点此打开网易云邀请"
        case .playing: "已连接，正在一起听"
        case .paused: "房间仍保持连接"
        case .syncing(let command): Self.syncingLine(for: command)
        case .commandFailed: "这次没有同步成功"
        case .roomFailed: "邀请已过期，点此重试"
        case .offline: "Murmur 连接异常"
        case .inactive: ""
        }
    }

    var primarySymbol: String {
        switch self {
        case .creating: "circle"
        case .creationFailed: "arrow.clockwise"
        case .waiting: "arrow.up.forward.app.fill"
        case .playing: "pause.fill"
        case .paused: "play.fill"
        case .commandFailed, .roomFailed: "arrow.clockwise"
        case .inactive, .syncing, .offline: "circle"
        }
    }

    var primaryAccessibilityLabel: String {
        switch self {
        case .creating: "正在创建一起听"
        case .creationFailed: "重新建房"
        case .waiting: "打开网易云一起听邀请"
        case .playing: "暂停一起听"
        case .paused: "继续一起听"
        case .commandFailed(let command, _):
            command == .playTrack ? "重试换歌" : "重试上一次一起听操作"
        case .roomFailed: "重新创建一起听邀请"
        case .syncing: "正在同步一起听操作"
        case .offline: "Murmur 连接异常，控制暂不可用"
        case .inactive: "Murmur"
        }
    }

    static func syncingLine(for command: ListenTogetherCommand?) -> String {
        switch command {
        case .pause: "暂停同步中…"
        case .resume: "继续同步中…"
        case .previous: "上一首同步中…"
        case .next: "下一首同步中…"
        case .playTrack: "切歌同步中…"
        case nil: "正在确认播放状态…"
        }
    }

    var isSyncing: Bool {
        if case .syncing = self { return true }
        return false
    }

    var isFailure: Bool {
        switch self {
        case .creationFailed, .commandFailed, .roomFailed, .offline: true
        default: false
        }
    }

    var isRoomFailure: Bool {
        if case .roomFailed = self { return true }
        return false
    }

    /// 现在切歌切给谁听？没人加入、或者邀请已经过期的时候，上一首/下一首按下去
    /// 什么都不会发生——一个必然无效的键比没有这个键更糟。聊天页的小卡片和
    /// 「一起听」整屏共用这一条，否则两处迟早各判各的。
    var allowsTrackSkipping: Bool {
        switch self {
        case .playing, .paused, .syncing, .commandFailed, .offline: true
        case .creating, .creationFailed, .waiting, .roomFailed, .inactive: false
        }
    }

    /// 失败一律珊瑚色，其余走次要墨色——两个界面共用同一条规则。
    ///
    /// `@MainActor` 是因为 `MurmurTheme` 是：这个枚举本身是 `Sendable` 的、
    /// 非隔离的，而颜色是视图层的值，只在主线程上取。
    @MainActor
    var statusColor: Color { isFailure ? MurmurTheme.coral : MurmurTheme.secondaryInk }
    @MainActor
    var primaryColor: Color { isFailure ? MurmurTheme.coral : MurmurTheme.ink }
}

extension ListenTogetherRoomSnapshotV1 {
    /// 还没有歌可说的时候说什么。
    static let noTrackLine = "和 Murmur 一起听"
    static let counterpartRightsUnavailable = "counterpart_rights_unavailable"

    var nonblockingNotice: String? {
        guard errorCode == Self.counterpartRightsUnavailable else { return nil }
        return "这首歌双方版权不一致，已退回上一首。"
    }

    var trackLine: String {
        guard let track = currentTrack else { return Self.noTrackLine }
        guard let artist = track.artists.first, !artist.isEmpty else { return track.title }
        return "\(track.title) · \(artist)"
    }
}

struct ListenTogetherCommandResultV1: Codable, Equatable, Sendable {
    let status: ListenTogetherCommandStatus
    let room: ListenTogetherRoomSnapshotV1
}

struct SharedMusicPreview: Equatable, Identifiable, Sendable {
    let draft: SharedMusicDraftV1
    let track: MusicTrackAttachmentV1
    var id: UUID { draft.id }
}

@MainActor
final class NeteaseMusicModel: ObservableObject {
    @Published private(set) var preview: SharedMusicPreview?
    @Published private(set) var room: ListenTogetherRoomSnapshotV1?
    @Published private(set) var commandStatus: ListenTogetherCommandStatus?
    @Published private(set) var isResolvingShare = false
    @Published private(set) var isChangingRoom = false
    @Published private(set) var failureMessage: String?
    @Published private(set) var roomMutation: ListenTogetherRetryAction?
    @Published private(set) var lastConfirmedPlaybackState: ListenTogetherPlaybackState = .unknown

    /// Not private: 一起听 的选歌面板要用同一个已认证的客户端搜歌，而它是一个
    /// 独立的小 model（见 `NeteaseSearchModel`），不该为了拿这一个依赖把整个
    /// 房间 model 传进去。
    let api: any MurmurAPIClient
    private let draftStore: SharedMusicDraftStore
    private var resolvingDraftID: UUID?
    private var roomGeneration = 0
    private var isRefreshingRoom = false
    /// 有人在单飞期间又要了一次刷新。见 `refreshRoom`。
    private var refreshAgainRequested = false

    init(api: any MurmurAPIClient, draftStore: SharedMusicDraftStore = SharedMusicDraftStore()) {
        self.api = api
        self.draftStore = draftStore
    }

    @discardableResult
    func recognizePastedText(_ text: String) -> Bool {
        guard NeteaseSharedTextDetector.containsCandidate(in: text) else { return false }
        let draft = SharedMusicDraftV1(rawText: text)
        resolve(draft)
        return true
    }

    func loadPendingShareDraft() {
        guard let draft = try? draftStore.load(),
              draft.id != preview?.draft.id,
              draft.id != resolvingDraftID
        else { return }
        resolve(draft)
    }

    func retryPreview() {
        guard let draft = preview?.draft ?? (try? draftStore.load()) else { return }
        resolve(draft)
    }

    func confirmPreview() -> MusicTrackAttachmentV1? {
        guard let preview else { return nil }
        try? draftStore.remove(id: preview.draft.id)
        self.preview = nil
        failureMessage = nil
        return preview.track
    }

    func discardPreview() {
        if let id = preview?.draft.id { try? draftStore.remove(id: id) }
        preview = nil
        failureMessage = nil
    }

    func clearFailure() {
        failureMessage = nil
        commandStatus = nil
        roomMutation = nil
    }

#if DEBUG
    func seedUITestSharePreviewIfRequested() {
        guard ProcessInfo.processInfo.arguments.contains("--murmur-stub-netease-share"),
              preview == nil,
              !isResolvingShare
        else { return }
        _ = recognizePastedText("https://music.163.com/song?id=186016")
    }
#endif

    func presentationState(connection: MurmurConnectionState) -> ListenTogetherPresentationState {
        ListenTogetherPresentationState.resolve(
            connection: connection,
            room: room,
            commandStatus: commandStatus,
            isChanging: isChangingRoom,
            mutation: roomMutation,
            failureMessage: failureMessage,
            lastConfirmedPlayback: lastConfirmedPlaybackState
        )
    }

    var displayTrack: MusicTrackAttachmentV1? {
        roomMutation?.track ?? room?.currentTrack
    }

    /// Record the recoverable room action before a picker disappears.
    ///
    /// SwiftUI may not start a newly-created `Task` until after the sheet has
    /// been removed. Keeping this preparation synchronous means the selected
    /// song and retry key are already visible to the underlying screen.
    func prepareRoomAction(
        for track: MusicTrackAttachmentV1
    ) -> ListenTogetherRetryAction? {
        guard track.isNetease else { return nil }
        if let current = room, current.isActive,
           current.currentTrack?.trackID == track.trackID {
            failureMessage = nil
            return nil
        }
        let key = UUID().uuidString.lowercased()
        let action: ListenTogetherRetryAction
        if let current = room, current.isActive {
            action = .command(
                command: .playTrack, track: track,
                roomHandle: current.roomHandle, idempotencyKey: key
            )
        } else {
            action = .create(track: track, idempotencyKey: key)
        }
        begin(action)
        return action
    }

    func performPreparedRoomAction(
        _ action: ListenTogetherRetryAction
    ) async -> URL? {
        guard roomMutation == action, isChangingRoom else { return nil }
        return await perform(action, alreadyPrepared: true)
    }

    func createRoom(for track: MusicTrackAttachmentV1) async -> URL? {
        guard track.isNetease else { return nil }
        if let current = room, current.isActive,
           current.currentTrack?.trackID == track.trackID {
            // The chat turn may have created this room before its card arrived.
            // Opening that invitation is a local action; sending play_track
            // again would mutate a room already playing the requested song.
            failureMessage = nil
            return current.userJoined ? nil : current.inviteURL
        }
        guard let action = prepareRoomAction(for: track) else { return nil }
        return await performPreparedRoomAction(action)
    }

    /// 汇流，不是丢弃。
    ///
    /// 空闲轮询二十秒一次，聊天里收到歌曲卡时会再问一次——问的正是刚刚在服务端
    /// 建好的那个房间。这两次撞上的时候，原来是把后来那次直接扔掉；而在飞的那次
    /// 是**建房之前**发出的，答案必然是「没有房间」，`roomGeneration` 没变所以照样
    /// 落地。于是卡片停在「和 Murmur 一起听」，最多要等满一个空闲间隔。
    ///
    /// 单飞照旧（同一时刻只有一个请求在外面），但撞上的那次记一笔，等在飞的落地
    /// 之后补问一遍。补问那次沿用第一个调用方的 `reportFailure`：轮询不报错，
    /// 而这里唯一的差别只是失败要不要写进 `failureMessage`。
    func refreshRoom(reportFailure: Bool = false) async {
        guard !isRefreshingRoom else {
            refreshAgainRequested = true
            return
        }
        isRefreshingRoom = true
        defer { isRefreshingRoom = false }
        repeat {
            refreshAgainRequested = false
            await performRefresh(reportFailure: reportFailure)
        } while refreshAgainRequested
    }

    private func performRefresh(reportFailure: Bool) async {
        let generation = roomGeneration
        do {
            let snapshot = try await api.currentListenTogetherRoom()
            guard generation == roomGeneration else { return }
            if let snapshot {
                apply(snapshot)
                if commandStatus == .accepted,
                   snapshot.state != .syncing,
                   snapshot.pendingCommand == nil {
                    commandStatus = nil
                    roomMutation = nil
                }
            } else {
                room = nil
                if case .create = roomMutation, commandStatus == .failed {
                    // A failed create is a local mutation state. An idle poll
                    // finding no room must not erase the song or its retry key.
                } else {
                    commandStatus = nil
                    roomMutation = nil
                    failureMessage = nil
                }
                lastConfirmedPlaybackState = .unknown
            }
            if room != nil { failureMessage = nil }
        } catch {
            if reportFailure {
                failureMessage = MurmurFailure.from(error).message
            }
        }
    }

    func command(_ command: ListenTogetherCommand) async {
        guard let room, room.isActive else { return }
        _ = await perform(.command(
            command: command,
            track: nil,
            roomHandle: room.roomHandle,
            idempotencyKey: UUID().uuidString.lowercased()
        ))
    }

    @discardableResult
    private func perform(
        _ action: ListenTogetherRetryAction,
        alreadyPrepared: Bool = false
    ) async -> URL? {
        if !alreadyPrepared { begin(action) }
        defer { isChangingRoom = false }
        do {
            let snapshot: ListenTogetherRoomSnapshotV1
            switch action {
            case .create(let track, let key):
                snapshot = try await api.createListenTogetherRoom(
                    initialTrack: track, idempotencyKey: key
                )
                commandStatus = nil
                roomMutation = nil
            case .command(let command, let track, let handle, let key):
                let result = try await api.commandListenTogetherRoom(
                    handle: handle, command: command, track: track, idempotencyKey: key
                )
                snapshot = result.room
                commandStatus = result.status
                if result.status == .synchronized { roomMutation = nil }
            }
            apply(snapshot)
            failureMessage = nil
            if commandStatus == .accepted {
                await refreshRoom()
            }
            return snapshot.userJoined ? nil : snapshot.inviteURL
        } catch {
            commandStatus = .failed
            if case .create = action {
                failureMessage = MurmurFailure.from(error).message
            }
            return nil
        }
    }

    private func begin(_ action: ListenTogetherRetryAction) {
        // 建房和换歌都会改房间，所以此刻已经在路上的那次轮询讲的都是旧事。
        // 递增放在两条分支之前：换歌那条以前漏了，回来的旧快照会把刚确认
        // 的结果盖掉。
        roomGeneration += 1
        isChangingRoom = true
        roomMutation = action
        commandStatus = action.command == nil ? nil : .accepted
        failureMessage = nil
    }

    @discardableResult
    func retryLastAction() async -> URL? {
        guard let roomMutation else { return nil }
        return await perform(roomMutation)
    }

    func closeRoom() async {
        guard let room else { return }
        roomGeneration += 1
        isChangingRoom = true
        // Closing is a new user decision.  A prior failed pause or track
        // change must not remain available for retry after this point.
        commandStatus = nil
        roomMutation = nil
        failureMessage = nil
        defer { isChangingRoom = false }
        do {
            self.room = try await api.closeListenTogetherRoom(
                handle: room.roomHandle,
                idempotencyKey: UUID().uuidString.lowercased()
            )
            commandStatus = nil
            roomMutation = nil
            lastConfirmedPlaybackState = .unknown
            failureMessage = nil
        } catch {
            failureMessage = MurmurFailure.from(error).message
        }
    }

    private func apply(_ snapshot: ListenTogetherRoomSnapshotV1) {
        room = snapshot
        if snapshot.playbackState != .unknown {
            lastConfirmedPlaybackState = snapshot.playbackState
        }
        if !snapshot.isActive {
            commandStatus = nil
            roomMutation = nil
        }
    }

    private func resolve(_ draft: SharedMusicDraftV1) {
        guard resolvingDraftID != draft.id else { return }
        resolvingDraftID = draft.id
        isResolvingShare = true
        failureMessage = nil
        Task {
            defer {
                if resolvingDraftID == draft.id { resolvingDraftID = nil }
                isResolvingShare = false
            }
            do {
                let track = try await api.resolveSharedMusic(
                    text: draft.rawText,
                    idempotencyKey: draft.id.uuidString.lowercased()
                )
                guard track.isNetease else {
                    throw MurmurFailure(
                        code: "music_provider_mismatch",
                        message: "这个链接没有解析成网易云歌曲。",
                        retryable: false
                    )
                }
                preview = SharedMusicPreview(draft: draft, track: track)
            } catch {
                // Keep the raw draft in the App Group so a transient failure or
                // cold launch never turns an explicit share into data loss.
                try? draftStore.save(draft)
                failureMessage = MurmurFailure.from(error).message
            }
        }
    }
}

/// Confirmation is deliberately a separate sheet.  Share-sheet titles and
/// artwork are untrusted; this view only receives the track the VPS resolved
/// again from its numeric NetEase id.
struct NeteaseSharedMusicPreviewView: View {
    let preview: SharedMusicPreview
    let onConfirm: () -> Void
    let onCancel: () -> Void

    @Environment(\.openURL) private var openURL
    @AccessibilityFocusState private var titleFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("把这首歌发给 Murmur？")
                    .font(MurmurTheme.display(.title3))
                    .foregroundStyle(MurmurTheme.ink)
                    .accessibilityFocused($titleFocused)

                Text("已从网易云重新获取歌曲信息。确认后才会发送，也不会自动开启一起听。")
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .fixedSize(horizontal: false, vertical: true)

                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 12) {
                        CachedArtwork(url: preview.track.artworkURL) {
                            RoundedRectangle(cornerRadius: 11)
                                .fill(MurmurTheme.accent)
                                .overlay {
                                    Image(systemName: "music.note")
                                        .foregroundStyle(MurmurTheme.accentInk)
                                }
                        }
                        .frame(width: 64, height: 64)
                        .clipShape(RoundedRectangle(cornerRadius: 11))

                        VStack(alignment: .leading, spacing: 3) {
                            Text(preview.track.title)
                                .font(MurmurTheme.body(.headline, weight: .semibold))
                                .foregroundStyle(MurmurTheme.ink)
                                .lineLimit(2)
                            Text(preview.track.artists.joined(separator: "、"))
                                .font(MurmurTheme.body(.subheadline))
                                .foregroundStyle(MurmurTheme.secondaryInk)
                                .lineLimit(1)
                            Text(providerLine)
                                .font(MurmurTheme.body(.caption))
                                .foregroundStyle(MurmurTheme.secondaryInk)
                        }
                        Spacer(minLength: 0)
                    }

                    Button {
                        openURL(preview.track.canonicalURL)
                    } label: {
                        Image(systemName: "arrow.up.forward.app.fill")
                            .font(MurmurTheme.body(.caption, weight: .semibold))
                            .foregroundStyle(MurmurTheme.ink)
                            .frame(width: 44, height: 44)
                            .background(MurmurTheme.raisedPaper, in: Circle())
                            .overlay { Circle().stroke(MurmurTheme.rule, lineWidth: 1) }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("在网易云里核对")
                }
                .padding(12)
                .background(MurmurTheme.paper, in: RoundedRectangle(cornerRadius: 17))
                .overlay {
                    RoundedRectangle(cornerRadius: 17).stroke(MurmurTheme.rule, lineWidth: 1)
                }

                Text("请核对歌名和艺人")
                    .font(MurmurTheme.body(.caption2))
                    .foregroundStyle(MurmurTheme.secondaryInk)

                HStack(spacing: 12) {
                    Button("取消", action: onCancel)
                        .font(MurmurTheme.body(.body, weight: .medium))
                        .foregroundStyle(MurmurTheme.ink)
                        .frame(maxWidth: .infinity, minHeight: 48)
                        .background(MurmurTheme.paper, in: Capsule())
                        .overlay { Capsule().stroke(MurmurTheme.rule, lineWidth: 1) }
                        .buttonStyle(.plain)

                    Button("确认发送", action: onConfirm)
                        .font(MurmurTheme.body(.body, weight: .semibold))
                        .foregroundStyle(MurmurTheme.accentInk)
                        .frame(maxWidth: .infinity, minHeight: 48)
                        .background(MurmurTheme.accent, in: Capsule())
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("confirm-netease-share")
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .padding(.bottom, 20)
        }
        .scrollIndicators(.hidden)
        .background(MurmurTheme.paper)
        .onAppear { titleFocused = true }
    }

    private var providerLine: String {
        let duration = preview.track.durationSeconds.map { seconds in
            String(format: "%d:%02d", seconds / 60, seconds % 60)
        }
        return ["网易云音乐", duration].compactMap { $0 }.joined(separator: " · ")
    }
}
