import Foundation
import SwiftUI

enum NeteaseSharedTextDetector {
    private static let longHosts: Set<String> = ["music.163.com", "y.music.163.com"]
    private static let shortHosts: Set<String> = ["163cn.tv"]

    /// This is only a cheap UI routing check. The server still follows short
    /// links with a strict redirect policy and re-fetches trusted metadata.
    static func containsCandidate(in text: String) -> Bool {
        guard text.utf8.count <= 16_384 else { return false }
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
        errorCode: String? = nil
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

    private let api: any MurmurAPIClient
    private let draftStore: SharedMusicDraftStore
    private var resolvingDraftID: UUID?
    private var roomGeneration = 0

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

    func createRoom(for track: MusicTrackAttachmentV1) async -> URL? {
        guard track.isNetease else { return nil }
        isChangingRoom = true
        commandStatus = nil
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
            } else {
                snapshot = try await api.createListenTogetherRoom(
                    initialTrack: track,
                    idempotencyKey: UUID().uuidString.lowercased()
                )
                commandStatus = .accepted
            }
            room = snapshot
            failureMessage = nil
            return snapshot.inviteURL
        } catch {
            failureMessage = MurmurFailure.from(error).message
            commandStatus = .failed
            return nil
        }
    }

    func refreshRoom(reportFailure: Bool = false) async {
        do {
            room = try await api.currentListenTogetherRoom()
            failureMessage = nil
        } catch {
            if reportFailure {
                failureMessage = MurmurFailure.from(error).message
            }
        }
    }

    func command(_ command: ListenTogetherCommand) async {
        guard let room, room.isActive else { return }
        isChangingRoom = true
        defer { isChangingRoom = false }
        do {
            let result = try await api.commandListenTogetherRoom(
                handle: room.roomHandle,
                command: command,
                track: nil,
                idempotencyKey: UUID().uuidString.lowercased()
            )
            self.room = result.room
            commandStatus = result.status
            failureMessage = nil
        } catch {
            commandStatus = .failed
            failureMessage = MurmurFailure.from(error).message
        }
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
            failureMessage = nil
        } catch {
            failureMessage = MurmurFailure.from(error).message
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

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 14) {
                    AsyncImage(url: preview.track.artworkURL) { image in
                        image.resizable().scaledToFill()
                    } placeholder: {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(MurmurTheme.rule)
                            .overlay { Image(systemName: "music.note") }
                    }
                    .frame(width: 72, height: 72)
                    .clipShape(RoundedRectangle(cornerRadius: 12))

                    VStack(alignment: .leading, spacing: 5) {
                        Text(preview.track.title)
                            .font(MurmurTheme.display(.headline))
                            .foregroundStyle(MurmurTheme.ink)
                            .lineLimit(2)
                        Text(preview.track.artists.joined(separator: "、"))
                            .font(MurmurTheme.body(.subheadline))
                            .foregroundStyle(MurmurTheme.secondaryInk)
                            .lineLimit(1)
                    }
                }

                Text("发送后，Murmur 收到的是这首歌的官方元数据和链接，不是分享文案里的内容。")
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.secondaryInk)

                Button("在网易云里核对") { openURL(preview.track.canonicalURL) }
                    .font(MurmurTheme.body(.subheadline, weight: .medium))

                Spacer(minLength: 0)

                Button(action: onConfirm) {
                    Text("发给 Murmur")
                        .font(MurmurTheme.body(.headline, weight: .semibold))
                        .foregroundStyle(MurmurTheme.paper)
                        .frame(maxWidth: .infinity, minHeight: 50)
                        .background(MurmurTheme.ink, in: RoundedRectangle(cornerRadius: 15))
                }
                .buttonStyle(MurmurPressStyle())
                .accessibilityIdentifier("confirm-netease-share")
            }
            .padding(22)
            .background(MurmurTheme.paper)
            .navigationTitle("发送这首歌？")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消", action: onCancel)
                }
            }
        }
    }
}

/// Process-local room state shown above the composer.  It never claims a
/// command succeeded merely because the VPS accepted it: accepted, synced and
/// failed each get their own wording.
struct NeteaseListenTogetherCard: View {
    let room: ListenTogetherRoomSnapshotV1
    let commandStatus: ListenTogetherCommandStatus?
    let isChanging: Bool
    let onCommand: (ListenTogetherCommand) -> Void
    let onOpen: () -> Void
    let onClose: () -> Void

    private var stateLine: String {
        if let commandStatus {
            return switch commandStatus {
            case .accepted: "命令已送出，等待网易云同步"
            case .synchronized: "已与网易云同步"
            case .failed: "这次没有同步成功"
            }
        }
        return switch room.state {
        case .creating: "正在创建房间"
        case .waitingForUser: "等你在网易云加入"
        case .connected: "已连接，正在一起听"
        case .syncing: "正在同步"
        case .ended: "一起听已结束"
        case .failed: "房间暂时不可用"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: room.userJoined ? "person.2.fill" : "person.2")
                    .foregroundStyle(MurmurTheme.accentInk)
                VStack(alignment: .leading, spacing: 2) {
                    Text(room.currentTrack?.title ?? "和 Murmur 一起听")
                        .font(MurmurTheme.body(.subheadline, weight: .semibold))
                        .foregroundStyle(MurmurTheme.ink)
                        .lineLimit(1)
                    Text(stateLine)
                        .font(MurmurTheme.body(.caption))
                        .foregroundStyle(commandStatus == .failed ? MurmurTheme.coral : MurmurTheme.secondaryInk)
                        .lineLimit(2)
                }
                Spacer(minLength: 8)
                if isChanging { ProgressView().controlSize(.small) }
            }

            HStack(spacing: 2) {
                roomButton("上一首", systemImage: "backward.fill") { onCommand(.previous) }
                roomButton("暂停", systemImage: "pause.fill") { onCommand(.pause) }
                roomButton("继续", systemImage: "play.fill") { onCommand(.resume) }
                roomButton("下一首", systemImage: "forward.fill") { onCommand(.next) }
                Menu {
                    if room.inviteURL != nil {
                        Button("在网易云打开", systemImage: "arrow.up.right.square", action: onOpen)
                    }
                    Button("结束一起听", systemImage: "xmark", role: .destructive, action: onClose)
                } label: {
                    Image(systemName: "ellipsis")
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .disabled(isChanging)
                .accessibilityLabel("更多一起听操作")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .frame(maxWidth: MurmurTheme.contentWidth)
        .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 18))
        .overlay { RoundedRectangle(cornerRadius: 18).stroke(MurmurTheme.rule, lineWidth: 1) }
        .shadow(color: MurmurTheme.ink.opacity(0.05), radius: 6, y: 2)
        .accessibilityIdentifier("netease-listen-together-card")
    }

    private func roomButton(
        _ label: String,
        systemImage: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .frame(maxWidth: .infinity, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isChanging)
        .accessibilityLabel(label)
    }
}
