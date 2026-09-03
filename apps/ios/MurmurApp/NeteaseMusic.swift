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

enum ListenTogetherPresentationState: Equatable, Sendable {
    case inactive
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
        failedCommand: ListenTogetherCommand?,
        lastConfirmedPlayback: ListenTogetherPlaybackState
    ) -> Self {
        guard let room else { return .inactive }
        if case .offline = connection { return .offline }
        if room.state == .failed { return .roomFailed(room.errorCode) }
        if room.state == .ended { return .inactive }
        if commandStatus == .failed {
            return .commandFailed(failedCommand, lastConfirmedPlayback)
        }
        if isChanging || commandStatus == .accepted || room.state == .syncing {
            return .syncing(room.pendingCommand.flatMap(ListenTogetherCommand.init(rawValue:)))
        }
        if room.state == .creating || room.state == .waitingForUser || !room.userJoined {
            return .waiting
        }
        return switch room.playbackState {
        case .playing: .playing
        case .paused: .paused
        case .unknown: .syncing(nil)
        }
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
    @Published private(set) var lastFailedCommand: ListenTogetherCommand?
    @Published private(set) var lastConfirmedPlaybackState: ListenTogetherPlaybackState = .unknown

    private let api: any MurmurAPIClient
    private let draftStore: SharedMusicDraftStore
    private var resolvingDraftID: UUID?
    private var roomGeneration = 0
    private var isRefreshingRoom = false

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

    func clearFailure() { failureMessage = nil }

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
            failedCommand: lastFailedCommand,
            lastConfirmedPlayback: lastConfirmedPlaybackState
        )
    }

    func createRoom(for track: MusicTrackAttachmentV1) async -> URL? {
        guard track.isNetease else { return nil }
        // 建房和换歌都会改房间，所以此刻已经在路上的那次轮询讲的都是旧事。
        // 递增放在两条分支之前：换歌那条以前漏了，回来的旧快照会把刚确认
        // 的结果盖掉。
        roomGeneration += 1
        isChangingRoom = true
        lastFailedCommand = nil
        commandStatus = room?.isActive == true ? .accepted : nil
        defer { isChangingRoom = false }
        do {
            let snapshot: ListenTogetherRoomSnapshotV1
            if let current = room, current.isActive {
                let result = try await api.commandListenTogetherRoom(
                    handle: current.roomHandle,
                    command: .playTrack,
                    track: track,
                    idempotencyKey: UUID().uuidString.lowercased()
                )
                snapshot = result.room
                commandStatus = result.status
                lastFailedCommand = result.status == .failed ? .playTrack : nil
            } else {
                snapshot = try await api.createListenTogetherRoom(
                    initialTrack: track,
                    idempotencyKey: UUID().uuidString.lowercased()
                )
                commandStatus = nil
            }
            apply(snapshot)
            failureMessage = nil
            return snapshot.inviteURL
        } catch {
            failureMessage = MurmurFailure.from(error).message
            commandStatus = .failed
            lastFailedCommand = room?.isActive == true ? .playTrack : nil
            return nil
        }
    }

    func refreshRoom(reportFailure: Bool = false) async {
        guard !isRefreshingRoom else { return }
        isRefreshingRoom = true
        let generation = roomGeneration
        defer { isRefreshingRoom = false }
        do {
            let snapshot = try await api.currentListenTogetherRoom()
            guard generation == roomGeneration else { return }
            if let snapshot {
                apply(snapshot)
                if commandStatus == .accepted,
                   snapshot.state != .syncing,
                   snapshot.pendingCommand == nil {
                    commandStatus = nil
                }
            } else {
                room = nil
                commandStatus = nil
                lastFailedCommand = nil
                lastConfirmedPlaybackState = .unknown
            }
            failureMessage = nil
        } catch {
            if reportFailure {
                failureMessage = MurmurFailure.from(error).message
            }
        }
    }

    func command(_ command: ListenTogetherCommand) async {
        guard let room, room.isActive else { return }
        roomGeneration += 1
        isChangingRoom = true
        commandStatus = .accepted
        lastFailedCommand = nil
        defer { isChangingRoom = false }
        do {
            let result = try await api.commandListenTogetherRoom(
                handle: room.roomHandle,
                command: command,
                track: nil,
                idempotencyKey: UUID().uuidString.lowercased()
            )
            apply(result.room)
            commandStatus = result.status
            lastFailedCommand = result.status == .failed ? command : nil
            failureMessage = nil
            if result.status == .accepted {
                await refreshRoom()
            }
        } catch {
            commandStatus = .failed
            lastFailedCommand = command
        }
    }

    func retryLastCommand() async {
        guard let lastFailedCommand else { return }
        await command(lastFailedCommand)
    }

    func closeRoom() async {
        guard let room else { return }
        roomGeneration += 1
        isChangingRoom = true
        defer { isChangingRoom = false }
        do {
            self.room = try await api.closeListenTogetherRoom(
                handle: room.roomHandle,
                idempotencyKey: UUID().uuidString.lowercased()
            )
            commandStatus = nil
            lastFailedCommand = nil
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
            lastFailedCommand = nil
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
