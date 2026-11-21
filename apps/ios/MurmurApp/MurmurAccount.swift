import Foundation
import ImageIO
import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct MurmurSyncMetadata: Codable, Sendable {
  var userID: String?
  var generation = 0
  var cursor = 0
  var acknowledged: Set<String> = []
  var previews: Set<String> = []
}

struct MurmurQuota: Decodable {
  let limit: Int
  let remaining: Int
  let resets_at: String
}

struct MurmurSyncSettings: Decodable {
  let enabled: Bool
  let chat_generation: Int
  let archive_generation: Int
}

private struct SyncedSong: Codable {
  let provider: String
  let track_id: String
  let title: String
  let artist: String
  let canonical_url: String?
}

private struct SyncedMessage: Codable {
  let id: String
  let author: String
  let text: String
  let sent_at: String
  let archive_day: String?
  let moment_id: String?
  let reply_id: String?
  let music_track: SyncedSong?

  init(_ row: MurmurMessage) {
    id = row.id
    author = row.author.rawValue
    text = row.text
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    sent_at = formatter.string(from: row.sentAt)
    archive_day = row.archiveDay.map { formatter.string(from: $0) }
    moment_id = row.momentID
    reply_id = row.author == .murmur ? row.id : nil
    music_track = row.musicTrack.map {
      SyncedSong(
        provider: $0.provider, track_id: $0.trackID, title: $0.title,
        artist: $0.artists.joined(separator: "、"), canonical_url: $0.canonicalURL.absoluteString)
    }
  }

  func local(imageFile: String?) throws -> MurmurMessage {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let sent = formatter.date(from: sent_at) ?? ISO8601DateFormatter().date(from: sent_at),
      let author = MurmurMessageAuthor(rawValue: author)
    else {
      throw MurmurFailure(code: "sync_format", message: "同步记录格式无法识别。", retryable: false)
    }
    let song: MusicTrackAttachmentV1? = music_track.flatMap {
      guard let link = $0.canonical_url.flatMap(URL.init(string:)) else { return nil }
      return MusicTrackAttachmentV1(
        provider: $0.provider, trackID: $0.track_id, title: $0.title,
        artists: [$0.artist], artworkURL: nil, canonicalURL: link, durationSeconds: nil,
        explicit: nil)
    }
    return MurmurMessage(
      id: id, author: author, text: text, imageFile: imageFile, musicTrack: song,
      sentAt: sent, archiveDay: archive_day.flatMap { formatter.date(from: $0) },
      delivery: .answered, momentID: moment_id)
  }
}

private struct SyncPage: Decodable {
  struct Entry: Decodable {
    let record: SyncedMessage
    let revision: Int
    let has_preview: Bool
  }
  let generation: Int
  let records: [Entry]
  let cursor: Int
  let has_more: Bool
}

@MainActor
final class MurmurAccountModel: ObservableObject {
  @Published private(set) var email: String?
  @Published private(set) var quota: MurmurQuota?
  @Published private(set) var syncEnabled = false
  @Published private(set) var busy = false
  @Published private(set) var message: String?
  private var cacheEvicted = false
  private(set) var didChangeHistory = false
  private var stopSyncRequested = false
  private let api: any MurmurAPIClient
  private let chat: MurmurTranscriptStore
  private let archive: MurmurTranscriptStore
  private let decoder = JSONDecoder()

  init(api: any MurmurAPIClient, chat: MurmurTranscriptStore, archive: MurmurTranscriptStore) {
    self.api = api
    self.chat = chat
    self.archive = archive
  }

  func retainHistoryOwner(_ userID: String) async throws {
    for store in [chat, archive] {
      var metadata = try await store.syncMetadata()
      if metadata.userID == nil {
        metadata.userID = userID
        try await store.saveSyncMetadata(metadata)
      }
    }
  }

  private func request(_ path: String, _ method: String = "GET", _ body: [String: Any]? = nil)
    async throws -> Data
  {
    try await api.accountRequest(
      path: path, method: method,
      body: body.map { try JSONSerialization.data(withJSONObject: $0) } ?? Data())
  }

  func refresh() async {
    do {
      struct Email: Decodable { let email: String? }
      email = try decoder.decode(Email.self, from: await request("/v1/account/email")).email
      quota = try decoder.decode(MurmurQuota.self, from: await request("/v1/account/quota"))
      let settings = try decoder.decode(
        MurmurSyncSettings.self, from: await request("/v1/sync/settings"))
      syncEnabled = settings.enabled
    } catch { message = MurmurFailure.from(error).message }
  }

  func bindingCode(_ email: String) async throws -> EmailCodeResponse {
    try decoder.decode(
      EmailCodeResponse.self,
      from: await request("/v1/account/email/codes", "POST", ["email": email]))
  }

  func bind(id: String, code: String) async throws {
    let proof = try decoder.decode(
      EmailGrantResponse.self,
      from: await request(
        "/v1/account/email/verify", "POST", ["verification_id": id, "code": code]))
    _ = try await request("/v1/account/email", "POST", ["email_grant": proof.email_grant])
    await refresh()
  }

  func setSync(_ enabled: Bool) async {
    guard !busy || !enabled else { return }
    stopSyncRequested = !enabled
    do {
      let settings = try decoder.decode(
        MurmurSyncSettings.self,
        from: await request("/v1/sync/settings", "PUT", ["enabled": enabled]))
      syncEnabled = settings.enabled
      message = enabled ? "准备同步本机记录…" : "同步已关闭，已有云端记录仍保留。"
      if enabled { await synchronize() }
    } catch { message = MurmurFailure.from(error).message }
  }

  func synchronize(automatic: Bool = false) async {
    didChangeHistory = false
    guard !busy else { return }
    if automatic && cacheEvicted { return }
    cacheEvicted = false
    stopSyncRequested = false
    busy = true
    defer { busy = false }
    do {
      guard let identity = try await api.storedIdentity() else { return }
      for store in [chat, archive] {
        var metadata = try await store.syncMetadata()
        if let owner = metadata.userID, owner != identity.userID {
          throw MurmurFailure(
            code: "history_account_mismatch", message: "本机历史属于其他账号，不会自动合并或上传。", retryable: false)
        }
        metadata.userID = identity.userID
        try await store.saveSyncMetadata(metadata)
      }
      let settings = try decoder.decode(
        MurmurSyncSettings.self, from: await request("/v1/sync/settings"))
      syncEnabled = settings.enabled
      // Delete barriers must also be applied on devices with transfer off.
      try await applyBarrier(chat, generation: settings.chat_generation)
      try await applyBarrier(archive, generation: settings.archive_generation)
      guard settings.enabled else { return }
      message = "正在同步…"
      try await channel(chat, kind: "chat")
      try await channel(archive, kind: "archive")
      message = "记录已同步"
    } catch {
      message =
        stopSyncRequested ? "同步已关闭，已有云端记录仍保留。" : "有记录待同步：" + MurmurFailure.from(error).message
    }
  }

  private func applyBarrier(_ store: MurmurTranscriptStore, generation: Int) async throws {
    let state = try await store.syncMetadata()
    if generation > state.generation {
      try await store.clearForSyncBarrier()
      didChangeHistory = true
      var next = MurmurSyncMetadata()
      next.generation = generation
      next.userID = state.userID
      try await store.saveSyncMetadata(next)
    }
  }

  private func channel(_ store: MurmurTranscriptStore, kind: String) async throws {
    var state = try await store.syncMetadata()
    // Pull first. Metadata and rows commit before the cursor advances, so
    // interrupted migration repeats safely and never skips a page.
    while true {
      if stopSyncRequested { throw CancellationError() }
      let page = try decoder.decode(
        SyncPage.self, from: await request("/v1/sync/\(kind)/records?cursor=\(state.cursor)"))
      guard page.generation == state.generation else {
        throw MurmurFailure(code: "sync_changed", message: "记录版本已改变，请重试。", retryable: true)
      }
      for entry in page.records {
        let existing = try await store.syncMessage(id: entry.record.id)
        var image = existing?.imageFile
        if entry.has_preview && (image == nil || !state.previews.contains(entry.record.id)) {
          let data = try await request("/v1/sync/\(kind)/preview/\(escaped(entry.record.id))")
          let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(
            UUID().uuidString + ".jpg")
          try data.write(to: temporary, options: [.atomic, .completeFileProtection])
          defer { try? FileManager.default.removeItem(at: temporary) }
          image = await store.adoptImage(at: temporary, id: entry.record.id)
          guard image != nil else {
            throw MurmurFailure(code: "sync_image", message: "预览图未能保存。", retryable: true)
          }
          state.previews.insert(entry.record.id)
        }
        var row: MurmurMessage
        if let preserved = existing {
          row = preserved
          row.imageFile = image
        } else {
          row = try entry.record.local(imageFile: image)
        }
        try await store.saveSynced(row)
        didChangeHistory = true
        // A local photo without a cloud preview still needs uploading.
        if row.imageFile == nil || entry.has_preview { state.acknowledged.insert(row.id) }
      }
      state.cursor = page.cursor
      try await store.saveSyncMetadata(state)
      if !page.has_more { break }
    }
    var anchor: MurmurMessage?
    while true {
      let local = try await store.syncPage(after: anchor)
      guard !local.isEmpty else { break }
      for row in local
      where !state.acknowledged.contains(row.id) && row.delivery != .sending
        && row.delivery != .failed
      {
        if stopSyncRequested { throw CancellationError() }
        let encoded = try JSONEncoder().encode(SyncedMessage(row))
        let object = try JSONSerialization.jsonObject(with: encoded)
        _ = try await request(
          "/v1/sync/\(kind)/records", "POST", ["generation": state.generation, "records": [object]])
        if let image = row.imageFile {
          let url = store.imageURL(for: image)
          let data = try await Task.detached { try Self.previewData(at: url) }.value
          _ = try await api.accountRequest(
            path: "/v1/sync/\(kind)/preview/\(escaped(row.id))?generation=\(state.generation)",
            method: "PUT", body: data)
          state.previews.insert(row.id)
        }
        state.acknowledged.insert(row.id)
        try await store.saveSyncMetadata(state)
      }
      anchor = local.last
    }
  }

  private func escaped(_ value: String) -> String {
    value.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? value
  }

  nonisolated private static func previewData(at url: URL) throws -> Data {
    let options: [CFString: Any] = [
      kCGImageSourceCreateThumbnailFromImageAlways: true,
      kCGImageSourceCreateThumbnailWithTransform: true, kCGImageSourceThumbnailMaxPixelSize: 960,
    ]
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    else {
      throw MurmurFailure(code: "sync_image", message: "本机照片读取失败。", retryable: true)
    }
    for quality in [0.7, 0.5, 0.3, 0.15] {
      let data = NSMutableData()
      if let target = CGImageDestinationCreateWithData(
        data, UTType.jpeg.identifier as CFString, 1, nil)
      {
        CGImageDestinationAddImage(
          target, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        if CGImageDestinationFinalize(target), data.length <= 180000 { return data as Data }
      }
    }
    throw MurmurFailure(code: "sync_image_size", message: "预览图仍过大，请重试。", retryable: true)
  }

  func clearEverywhere(kind: String) async {
    guard !busy else { return }
    busy = true
    defer { busy = false }
    do {
      let settings = try decoder.decode(
        MurmurSyncSettings.self, from: await request("/v1/sync/settings"))
      let generation = kind == "chat" ? settings.chat_generation : settings.archive_generation
      let result = try decoder.decode(
        MurmurSyncSettings.self,
        from: await request("/v1/sync/\(kind)/records", "DELETE", ["generation": generation]))
      try await applyBarrier(
        kind == "chat" ? chat : archive,
        generation: kind == "chat" ? result.chat_generation : result.archive_generation)
      message = "记录已删除，离线设备下次连接时会执行删除。"
    } catch { message = MurmurFailure.from(error).message }
  }

  func cleanCache() async {
    guard !busy else { return }
    do {
      try await chat.cleanSyncedCache()
      try await archive.cleanSyncedCache()
      cacheEvicted = true
      message = "已清理同步完成的本机副本。需要时可点“同步并下载记录”，未同步内容仍保留。"
    } catch { message = MurmurFailure.from(error).message }
  }
}

struct MurmurAccountSettingsView: View {
  @ObservedObject var account: MurmurAccountModel
  let refreshHistory: () async -> Void
  let bindEmail: () -> Void
  @State private var confirmSync = false
  @State private var deletion: String?
  @State private var confirmCache = false

  var body: some View {
    Group {
    Section("邮箱与额度") {
      if let email = account.email {
        LabeledContent("邮箱", value: email).accessibilityIdentifier("account-email")
      } else {
        Text("绑定邮箱后，可以在其他设备登录这个账号。现有记录和设备会保留。")
          .font(.footnote).foregroundStyle(.secondary)
        Button("绑定邮箱", action: bindEmail)
      }
      if let quota = account.quota {
        LabeledContent("今日剩余", value: "\(quota.remaining) / \(quota.limit) 次")
          .monospacedDigit()
        if let reset = ISO8601DateFormatter().date(from: quota.resets_at) {
          Text("恢复时间：\(reset.formatted(date: .abbreviated, time: .shortened))")
            .font(.footnote).foregroundStyle(.secondary)
        }
        Text("聊天和照片解读共用额度，北京时间零点恢复。失败不会扣次。")
          .font(.footnote).foregroundStyle(.secondary)
      }
    }
    Section("跨设备同步") {
      Toggle(
        "同步聊天与照片房间",
        isOn: Binding(
          get: { account.syncEnabled },
          set: { value in
            if value { confirmSync = true } else { Task { await account.setSync(false) } }
          })
      )
      .disabled(account.busy && !account.syncEnabled)
      .alert("开启跨设备同步？", isPresented: $confirmSync) {
        Button("取消", role: .cancel) {}
        Button("开启同步") {
          Task {
            await account.setSync(true)
            await refreshHistory()
          }
        }
      } message: {
        Text("会上传这台设备已有的聊天、照片房间记录及压缩预览图，并在登录同一账号的设备间同步。记录保留到你主动删除。")
      }
      Text("只同步文字、歌曲卡片和去除拍摄信息的压缩预览图，不上传原始照片。关闭同步后，已有云端记录仍保留。")
        .font(.footnote).foregroundStyle(.secondary)
      if let message = account.message {
        Text(message).font(.footnote).accessibilityIdentifier("sync-status")
      }
      if account.syncEnabled {
        Button(account.busy ? "正在同步…" : "同步并下载记录") {
          Task {
            await account.synchronize()
            await refreshHistory()
          }
        }.disabled(account.busy)
      }
      Button("清理本机缓存") { confirmCache = true }.disabled(account.busy)
        .alert("清理本机缓存？", isPresented: $confirmCache) {
          Button("取消", role: .cancel) {}
          Button("清理") {
            Task {
              await account.cleanCache()
              await refreshHistory()
            }
          }
        } message: {
          Text("仅清理已成功同步的本机副本，未同步内容会保留。云端记录可再次下载。")
        }
      Button("删除所有设备的聊天记录", role: .destructive) { deletion = "chat" }.disabled(account.busy)
        .alert(
          "删除所有设备记录？",
          isPresented: Binding(get: { deletion == "chat" }, set: { if !$0 { deletion = nil } })
        ) {
          Button("取消", role: .cancel) { deletion = nil }
          Button("删除记录", role: .destructive) {
            Task {
              await account.clearEverywhere(kind: "chat")
              await refreshHistory()
            }
            deletion = nil
          }
        } message: {
          Text("删除云端和各设备对应的记录与预览图。离线设备下次连接时执行，无法恢复。")
        }
      Button("删除所有设备的照片房间记录", role: .destructive) { deletion = "archive" }.disabled(account.busy)
        .alert(
          "删除所有设备记录？",
          isPresented: Binding(get: { deletion == "archive" }, set: { if !$0 { deletion = nil } })
        ) {
          Button("取消", role: .cancel) { deletion = nil }
          Button("删除记录", role: .destructive) {
            Task {
              await account.clearEverywhere(kind: "archive")
              await refreshHistory()
            }
            deletion = nil
          }
        } message: {
          Text("删除云端和各设备对应的记录与预览图。离线设备下次连接时执行，无法恢复。")
        }
    }
    .task { await account.refresh() }
    }
  }
}

struct MurmurEmailBindingView: View {
  @ObservedObject var account: MurmurAccountModel
  @Environment(\.dismiss) private var dismiss
  private enum Field: Hashable { case email, code }
  @FocusState private var focused: Field?
  @State private var email = ""
  @State private var code = ""
  @State private var verification: EmailCodeResponse?
  @State private var expiresAt = Date.distantFuture
  @State private var resendAt = Date.distantPast
  @State private var busy = false
  @State private var error: String?

  var body: some View {
    NavigationStack {
      Form {
        Section {
          TextField("邮箱地址", text: $email).keyboardType(.emailAddress).textContentType(.emailAddress)
            .textInputAutocapitalization(.never).autocorrectionDisabled().disabled(
              verification != nil)
            .focused($focused, equals: .email)
            .accessibilityIdentifier("binding-email")
          if verification != nil {
            TextField("6 位验证码", text: $code).keyboardType(.numberPad).textContentType(.oneTimeCode)
              .focused($focused, equals: .code)
              .accessibilityIdentifier("binding-code")
              .onChange(of: code) { _, value in
                code = String(value.filter { $0.isASCII && $0.isNumber }.prefix(6))
              }
          }
          if let error { Text(error).foregroundStyle(MurmurTheme.coral) }
          Button {
            guard !busy else { return }
            busy = true
            error = nil
            Task {
              defer { busy = false }
              do {
                if let verification {
                  try await account.bind(id: verification.verification_id, code: code)
                  dismiss()
                } else {
                  verification = try await account.bindingCode(email)
                  resendAt = Date().addingTimeInterval(TimeInterval(verification?.resend_after ?? 60))
                  expiresAt = Date().addingTimeInterval(TimeInterval(verification?.expires_in ?? 600))
                  code = ""
                }
              } catch { self.error = MurmurFailure.from(error).message }
            }
          } label: {
            Text(busy ? "请稍候…" : verification == nil ? "发送验证码" : "绑定邮箱")

          }
          .buttonStyle(.automatic)
          .disabled(busy || (verification == nil ? email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty : code.count != 6))
            .accessibilityIdentifier("binding-submit")
          if verification != nil {
            TimelineView(.periodic(from: .now, by: 1)) { context in
              let wait = max(0, Int(ceil(resendAt.timeIntervalSince(context.date))))
              if context.date >= expiresAt {
                Text("验证码已过期，请重新发送。").foregroundStyle(MurmurTheme.coral)
              }
              Button(wait > 0 ? "\(wait) 秒后可重发" : "重新发送") {
                busy = true
                error = nil
                Task {
                  defer { busy = false }
                  do {
                    verification = try await account.bindingCode(email)
                    resendAt = Date().addingTimeInterval(TimeInterval(verification?.resend_after ?? 60))
                    expiresAt = Date().addingTimeInterval(TimeInterval(verification?.expires_in ?? 600))
                    code = ""
                  } catch { self.error = MurmurFailure.from(error).message }
                }
              }.disabled(busy || wait > 0).monospacedDigit()
            }
          }
          Text("绑定后保留原账号、设备和记录。邮箱已属于其他账号时，不会自动合并。")
            .font(.footnote).foregroundStyle(MurmurTheme.secondaryInk)
        }
      }
      .background(MurmurTheme.paper)
      .navigationTitle("绑定邮箱")
      .navigationBarTitleDisplayMode(.inline)
      .defaultFocus($focused, .email)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(busy) }
      }
    }
  }
}
