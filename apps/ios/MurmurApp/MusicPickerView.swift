import SwiftUI

/// The catalogue reads the picker makes.
///
/// A protocol rather than `AudiusClient` directly, so the part with the actual
/// logic — paging, resetting between sources, empty versus failed — can be
/// tested without a network or an account.
protocol MusicLibraryBrowsing: Sendable {
    func searchTracks(query: String, limit: Int, offset: Int) async throws -> [MusicTrackAttachmentV1]
    func favoriteTracks(limit: Int, offset: Int) async throws -> [MusicTrackAttachmentV1]
    func playlists(limit: Int, offset: Int) async throws -> [AudiusPlaylist]
    func playlistTracks(
        playlistID: String, limit: Int, offset: Int
    ) async throws -> [MusicTrackAttachmentV1]
}

extension AudiusClient: MusicLibraryBrowsing {}

/// The three places a song can come from, and what is on screen for each.
///
/// One model rather than three: they differ only in which call fills the list,
/// and splitting them would mean three copies of the paging, the empty state
/// and the error wording.
@MainActor
final class MusicPickerModel: ObservableObject {
    enum Source: String, CaseIterable, Identifiable {
        case search
        case favorites
        case playlists

        var id: String { rawValue }

        var title: String {
            switch self {
            case .search: "搜索"
            case .favorites: "收藏"
            case .playlists: "歌单"
            }
        }

        /// Public catalogue search needs no account; the other two are the
        /// person's own library and cannot exist without one.
        var needsAccount: Bool { self != .search }
    }

    enum Phase: Equatable {
        case idle
        case loading
        case ready
        case failed(String)
    }

    @Published var source: Source = .search {
        didSet {
            guard source != oldValue else { return }
            reload()
        }
    }
    @Published var query = ""
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var tracks: [MusicTrackAttachmentV1] = []
    @Published private(set) var playlists: [AudiusPlaylist] = []
    /// The playlist being read, if the person has opened one.
    @Published private(set) var openPlaylist: AudiusPlaylist?
    @Published private(set) var canLoadMore = false

    private let client: any MusicLibraryBrowsing
    private let pageSize: Int
    private var offset = 0
    private var work: Task<Void, Never>?

    init(client: any MusicLibraryBrowsing, pageSize: Int = 20) {
        self.client = client
        self.pageSize = pageSize
    }

    deinit { work?.cancel() }

    var isEmpty: Bool {
        phase == .ready && tracks.isEmpty && playlists.isEmpty
    }

    var emptyMessage: String {
        switch source {
        case .search:
            query.trimmingCharacters(in: .whitespaces).isEmpty
                ? "搜索 Audius 上的公开曲库。"
                : "没有找到这首。"
        case .favorites: "你在 Audius 上还没有收藏。"
        case .playlists: openPlaylist == nil ? "你在 Audius 上还没有歌单。" : "这个歌单是空的。"
        }
    }

    func open(_ playlist: AudiusPlaylist) {
        openPlaylist = playlist
        reload()
    }

    func closePlaylist() {
        openPlaylist = nil
        reload()
    }

    /// Start the list over. Every switch — source, query, opening a playlist —
    /// comes through here, so there is one place that resets paging.
    func reload() {
        work?.cancel()
        offset = 0
        tracks = []
        playlists = []
        canLoadMore = false
        if source == .search, query.trimmingCharacters(in: .whitespaces).isEmpty {
            phase = .ready
            return
        }
        phase = .loading
        work = Task { [weak self] in await self?.fetch(replacing: true) }
    }

    func loadMore() {
        guard canLoadMore, phase != .loading else { return }
        // Paging is a load like any other. The list keeps whatever is already
        // on screen — `content` only shows a bare spinner when there is
        // nothing yet — and this is what stops a second page being asked for
        // twice while the first request is still out.
        phase = .loading
        work = Task { [weak self] in await self?.fetch(replacing: false) }
    }

    private func fetch(replacing: Bool) async {
        do {
            if source == .playlists, openPlaylist == nil {
                let page = try await client.playlists(limit: pageSize, offset: offset)
                guard !Task.isCancelled else { return }
                playlists = replacing ? page : playlists + page
                canLoadMore = page.count == pageSize
            } else {
                let page = try await loadTracks()
                guard !Task.isCancelled else { return }
                // A page that partly failed to normalise is still a page; what
                // matters is whether the provider had more to give.
                tracks = replacing ? page : tracks + page
                canLoadMore = page.count == pageSize
            }
            offset += pageSize
            phase = .ready
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            phase = .failed(Self.message(for: error))
        }
    }

    private func loadTracks() async throws -> [MusicTrackAttachmentV1] {
        switch source {
        case .search:
            try await client.searchTracks(
                query: query.trimmingCharacters(in: .whitespaces),
                limit: pageSize, offset: offset
            )
        case .favorites:
            try await client.favoriteTracks(limit: pageSize, offset: offset)
        case .playlists:
            if let openPlaylist {
                try await client.playlistTracks(
                    playlistID: openPlaylist.id, limit: pageSize, offset: offset
                )
            } else {
                []
            }
        }
    }

    private static func message(for error: Error) -> String {
        switch error {
        case AudiusClientError.rateLimited:
            "Audius 请求太频繁，稍等一下再试。"
        case AudiusClientError.notAuthenticated, AudiusClientError.unauthorized:
            "需要重新连接 Audius。"
        case AudiusClientError.notConfigured:
            "这个版本还没有配置 Audius。"
        default:
            "暂时没有连上 Audius。"
        }
    }
}

/// The sheet behind 「音乐」 in the composer's menu.
struct MusicPickerView: View {
    @StateObject private var model: MusicPickerModel
    @ObservedObject var account: AudiusAccountModel
    let onSend: (MusicTrackAttachmentV1) -> Void

    @Environment(\.dismiss) private var dismiss
    @FocusState private var searchFocused: Bool

    init(
        client: any MusicLibraryBrowsing,
        account: AudiusAccountModel,
        onSend: @escaping (MusicTrackAttachmentV1) -> Void
    ) {
        _model = StateObject(wrappedValue: MusicPickerModel(client: client))
        self.account = account
        self.onSend = onSend
    }

    private var sources: [MusicPickerModel.Source] {
        // 收藏 and 歌单 are the person's own library: without an account there
        // is nothing behind those two stops, so they are not offered.
        MusicPickerModel.Source.allCases.filter {
            !$0.needsAccount || account.isSignedIn
        }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                if sources.count > 1 {
                    Picker("来源", selection: $model.source) {
                        ForEach(sources) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, MurmurTheme.pageInset)
                }
                if model.source == .search { searchField }
                if let playlist = model.openPlaylist {
                    Button {
                        model.closePlaylist()
                    } label: {
                        Label(playlist.name, systemImage: "chevron.left")
                            .font(MurmurTheme.body(.footnote, weight: .medium))
                            .foregroundStyle(MurmurTheme.accentInk)
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .padding(.horizontal, MurmurTheme.pageInset)
                }
                content
            }
            .background(MurmurTheme.paper)
            .navigationTitle("音乐")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
        }
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(MurmurTheme.secondaryInk)
            TextField("搜索 Audius", text: $model.query)
                .font(MurmurTheme.body(.body))
                .foregroundStyle(MurmurTheme.ink)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .focused($searchFocused)
                .onSubmit { model.reload() }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(
            MurmurTheme.raisedPaper,
            in: RoundedRectangle(cornerRadius: MurmurTheme.corner, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: MurmurTheme.corner, style: .continuous)
                .stroke(searchFocused ? MurmurTheme.accentInk : MurmurTheme.rule,
                        lineWidth: searchFocused ? 2 : 1)
        }
        .padding(.horizontal, MurmurTheme.pageInset)
    }

    @ViewBuilder
    private var content: some View {
        switch model.phase {
        case .loading where model.tracks.isEmpty && model.playlists.isEmpty:
            Spacer()
            ProgressView().controlSize(.regular)
            Spacer()
        case let .failed(message):
            Spacer()
            VStack(spacing: 12) {
                Text(message)
                    .font(MurmurTheme.body(.subheadline))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .multilineTextAlignment(.center)
                Button("再试一次") { model.reload() }
                    .font(MurmurTheme.body(.footnote, weight: .medium))
                    .foregroundStyle(MurmurTheme.accentInk)
                    .frame(minHeight: 44)
            }
            .padding(.horizontal, MurmurTheme.pageInset)
            Spacer()
        default:
            if model.isEmpty {
                Spacer()
                Text(model.emptyMessage)
                    .font(MurmurTheme.body(.subheadline))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, MurmurTheme.pageInset)
                Spacer()
            } else {
                list
            }
        }
    }

    private var list: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(model.playlists) { playlist in
                    PlaylistRow(playlist: playlist) { model.open(playlist) }
                    Divider().overlay(MurmurTheme.rule)
                }
                ForEach(model.tracks) { track in
                    PickerTrackRow(track: track) {
                        onSend(track)
                        dismiss()
                    }
                    Divider().overlay(MurmurTheme.rule)
                }
                if model.canLoadMore {
                    ProgressView()
                        .controlSize(.small)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                        .onAppear { model.loadMore() }
                }
            }
            .padding(.horizontal, MurmurTheme.pageInset)
        }
    }
}

private struct PickerTrackRow: View {
    let track: MusicTrackAttachmentV1
    let onSend: () -> Void
    @ScaledMetric(relativeTo: .body) private var artworkSide: CGFloat = 44

    var body: some View {
        HStack(spacing: 12) {
            artwork
            VStack(alignment: .leading, spacing: 2) {
                Text(track.title)
                    .font(MurmurTheme.body(.subheadline, weight: .medium))
                    .foregroundStyle(MurmurTheme.ink)
                    .lineLimit(1)
                Text(track.artists.joined(separator: "、"))
                    .font(MurmurTheme.body(.caption))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            // An explicit button, not a tap on the row: picking a song and
            // sending it to somebody are different decisions, and only one of
            // them can be taken back.
            Button("发送", action: onSend)
                .font(MurmurTheme.body(.footnote, weight: .semibold))
                .foregroundStyle(MurmurTheme.accentInk)
                .padding(.horizontal, 12)
                .frame(minHeight: MurmurTheme.floatingDisc)
                .background(MurmurTheme.accent.opacity(0.16), in: Capsule())
                .buttonStyle(.plain)
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(track.title)，\(track.artists.joined(separator: "、"))")
        .accessibilityAction(named: "发送给 Murmur", onSend)
    }

    @ViewBuilder
    private var artwork: some View {
        let shape = RoundedRectangle(cornerRadius: 8, style: .continuous)
        AsyncImage(url: track.artworkURL) { image in
            image.resizable().aspectRatio(contentMode: .fill)
        } placeholder: {
            shape.fill(MurmurTheme.secondaryInk.opacity(0.16))
        }
        .frame(width: artworkSide, height: artworkSide)
        .clipShape(shape)
    }
}

private struct PlaylistRow: View {
    let playlist: AudiusPlaylist
    let onOpen: () -> Void
    @ScaledMetric(relativeTo: .body) private var artworkSide: CGFloat = 44

    var body: some View {
        Button(action: onOpen) {
            HStack(spacing: 12) {
                let shape = RoundedRectangle(cornerRadius: 8, style: .continuous)
                AsyncImage(url: playlist.artworkURL) { image in
                    image.resizable().aspectRatio(contentMode: .fill)
                } placeholder: {
                    shape.fill(MurmurTheme.secondaryInk.opacity(0.16))
                }
                .frame(width: artworkSide, height: artworkSide)
                .clipShape(shape)
                VStack(alignment: .leading, spacing: 2) {
                    Text(playlist.name)
                        .font(MurmurTheme.body(.subheadline, weight: .medium))
                        .foregroundStyle(MurmurTheme.ink)
                        .lineLimit(1)
                    if let count = playlist.trackCount {
                        Text("\(count) 首")
                            .font(MurmurTheme.body(.caption).monospacedDigit())
                            .foregroundStyle(MurmurTheme.secondaryInk)
                    }
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(MurmurTheme.body(.caption))
                    .foregroundStyle(MurmurTheme.secondaryInk)
            }
            .padding(.vertical, 8)
            .frame(minHeight: MurmurTheme.floatingDisc)
        }
        .buttonStyle(.plain)
    }
}
