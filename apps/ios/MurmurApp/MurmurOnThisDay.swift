import Photos
import SwiftUI
import UIKit

// MARK: - Authorization

/// The four doors this feature can be standing in front of.  `limited` is a
/// designed state, not an error: with a hand-picked slice of the library the
/// feature effectively does not exist, so the entry hides itself rather than
/// letting an empty screen lie about what the person photographed that day.
enum OnThisDayAuthorization: Equatable, Sendable {
    case notDetermined
    case denied
    case limited
    case authorized

    init(_ status: PHAuthorizationStatus) {
        switch status {
        case .notDetermined: self = .notDetermined
        case .authorized: self = .authorized
        case .limited: self = .limited
        case .denied, .restricted: self = .denied
        @unknown default: self = .denied
        }
    }
}

// MARK: - Candidate

/// One photo on the shelf.  Only the localIdentifier crosses onto the main
/// actor — the PHAsset itself is fetched, filtered and ranked on the library's
/// own queue and never leaves it.
struct OnThisDayCandidate: Identifiable, Equatable, Sendable {
    /// Why this photo is on the shelf.  The card says which it is, because
    /// 「去年的今天」 over a picture from an ordinary Tuesday is a lie, and a
    /// small one told about someone's own memory is not a small one.
    enum Origin: Equatable, Sendable {
        case sameDay(yearsAgo: Int)
        /// Drawn at random from the library, either because this day is blank
        /// in every earlier year or because the day's photos ran out.
        case elsewhere
    }

    let id: String
    let creationDate: Date
    let origin: Origin
    /// Where it was taken, straight off `PHAsset.location` — no EXIF parsing,
    /// and no location permission: read access to the library is the whole
    /// budget.  Nil is ordinary: the person had location off that day, or the
    /// photo came from somewhere other than a camera.
    let latitude: Double?
    let longitude: Double?

    init(
        id: String,
        creationDate: Date,
        origin: Origin,
        latitude: Double? = nil,
        longitude: Double? = nil
    ) {
        self.id = id
        self.creationDate = creationDate
        self.origin = origin
        self.latitude = latitude
        self.longitude = longitude
    }

    /// What this photo can say about itself on the way up.  The place name is
    /// not here: resolving it needs the network, so it is asked for only at the
    /// moment the photo is actually sent.
    var provenance: PhotoProvenance {
        PhotoProvenance(
            shotAt: creationDate, latitude: latitude, longitude: longitude
        )
    }
}

// MARK: - Library

protocol OnThisDayLibrary: Sendable {
    func authorization() async -> OnThisDayAuthorization
    func requestAuthorization() async -> OnThisDayAuthorization
    /// Ranked best-first: the first card is the one the feature is judged by,
    /// so "first in the fetch" is not an acceptable answer.
    func candidates(around date: Date, yearsBack: Int) async -> [OnThisDayCandidate]
    /// Photos from anywhere in the library, for when this day is blank in
    /// every earlier year or its photos have all been swiped past.  The shelf
    /// carries on rather than looping, so paging forward can continue.
    /// Returns fewer than asked — or none — when the library has no more.
    func randomCandidates(count: Int, excluding: Set<String>) async -> [OnThisDayCandidate]
    /// Memory only.  Browsing pixels never touch the disk: they live in an
    /// NSCache and die with the process, so the photo library gains no second
    /// lifecycle inside Murmur.  A file appears only when the person swipes a
    /// photo up into the conversation, and that file goes through
    /// PhotoLoader's swept temporary prefix like every other upload.
    func image(for candidate: OnThisDayCandidate, targetPixels: CGFloat) async -> UIImage?
}

/// PhotoKit, on its own actor.  No Moments API (dead since iOS 13): the query
/// is N explicit creationDate windows — this day ±1 in each earlier year —
/// with the noise removed by signals that are already on the PHAsset.
actor PhotoKitOnThisDayLibrary: OnThisDayLibrary {
    /// Browsing copies, keyed by "identifier@pixels".  countLimit keeps the
    /// whole shelf well under a memory warning.
    private let images = NSCache<NSString, UIImage>()

    init() {
        images.countLimit = 12
    }

    nonisolated func authorization() async -> OnThisDayAuthorization {
        OnThisDayAuthorization(PHPhotoLibrary.authorizationStatus(for: .readWrite))
    }

    nonisolated func requestAuthorization() async -> OnThisDayAuthorization {
        OnThisDayAuthorization(await PHPhotoLibrary.requestAuthorization(for: .readWrite))
    }

    /// Small enough to throw away stickers and reaction-meme saves; real
    /// camera output is never this small on a modern phone.
    private static let minimumEdgePixels = 600
    private static let perWindowLimit = 60
    private static let keptPerYear = 6

    func candidates(around date: Date, yearsBack: Int) async -> [OnThisDayCandidate] {
        let calendar = Calendar.current
        var picked: [OnThisDayCandidate] = []
        for yearsAgo in 1...yearsBack {
            guard let sameDay = calendar.date(byAdding: .year, value: -yearsAgo, to: date),
                  let start = calendar.date(byAdding: .day, value: -1, to: calendar.startOfDay(for: sameDay)),
                  let end = calendar.date(byAdding: .day, value: 2, to: calendar.startOfDay(for: sameDay))
            else { continue }

            let options = PHFetchOptions()
            // Screenshots are not memories; the predicate drops them before
            // the fetchLimit is spent on them.
            options.predicate = NSPredicate(
                format: "mediaType == %d AND creationDate >= %@ AND creationDate < %@ AND !((mediaSubtypes & %d) == %d)",
                PHAssetMediaType.image.rawValue,
                start as NSDate,
                end as NSDate,
                PHAssetMediaSubtype.photoScreenshot.rawValue,
                PHAssetMediaSubtype.photoScreenshot.rawValue
            )
            options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
            options.fetchLimit = Self.perWindowLimit

            let result = PHAsset.fetchAssets(with: options)
            var window: [(candidate: OnThisDayCandidate, favorite: Bool, area: Int)] = []
            result.enumerateObjects { asset, _, stop in
                // iCloud shared albums and synced media are somebody else's
                // day; 当年今日 only reads the device's own library.
                guard asset.sourceType == .typeUserLibrary else { return }
                // A burst contributes its representative frame, not all 80.
                if asset.burstIdentifier != nil, !asset.representsBurst { return }
                let area = asset.pixelWidth * asset.pixelHeight
                guard min(asset.pixelWidth, asset.pixelHeight) >= Self.minimumEdgePixels else { return }
                window.append((
                    OnThisDayCandidate(
                        id: asset.localIdentifier,
                        creationDate: asset.creationDate ?? start,
                        origin: .sameDay(yearsAgo: yearsAgo),
                        latitude: asset.location?.coordinate.latitude,
                        longitude: asset.location?.coordinate.longitude
                    ),
                    asset.isFavorite,
                    area
                ))
                if window.count >= Self.perWindowLimit { stop.pointee = true }
            }
            window.sort { lhs, rhs in
                if lhs.favorite != rhs.favorite { return lhs.favorite }
                if lhs.area != rhs.area { return lhs.area > rhs.area }
                return lhs.candidate.creationDate > rhs.candidate.creationDate
            }
            picked.append(contentsOf: window.prefix(Self.keptPerYear).map(\.candidate))
        }
        return picked
    }

    /// How many index draws one top-up may spend before giving up.  A draw
    /// that lands on a screenshot, a burst frame or a photo already on the
    /// shelf costs one attempt and nothing else.
    private static let randomDrawCeiling = 200

    func randomCandidates(count: Int, excluding: Set<String>) async -> [OnThisDayCandidate] {
        guard count > 0 else { return [] }
        let options = PHFetchOptions()
        options.predicate = NSPredicate(
            format: "mediaType == %d AND !((mediaSubtypes & %d) == %d)",
            PHAssetMediaType.image.rawValue,
            PHAssetMediaSubtype.photoScreenshot.rawValue,
            PHAssetMediaSubtype.photoScreenshot.rawValue
        )
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        // No fetchLimit: the result is lazy, `count` is cheap, and only the
        // indices actually drawn are ever realised.  A library of fifty
        // thousand photos is sampled, never enumerated.
        let result = PHAsset.fetchAssets(with: options)
        guard result.count > 0 else { return [] }

        var seen = excluding
        var picked: [OnThisDayCandidate] = []
        let ceiling = min(Self.randomDrawCeiling, max(result.count * 2, count * 8))
        var draws = 0
        while picked.count < count, draws < ceiling {
            draws += 1
            let asset = result.object(at: Int.random(in: 0..<result.count))
            guard !seen.contains(asset.localIdentifier) else { continue }
            guard asset.sourceType == .typeUserLibrary else { continue }
            if asset.burstIdentifier != nil, !asset.representsBurst { continue }
            guard min(asset.pixelWidth, asset.pixelHeight) >= Self.minimumEdgePixels else { continue }
            seen.insert(asset.localIdentifier)
            picked.append(OnThisDayCandidate(
                id: asset.localIdentifier,
                creationDate: asset.creationDate ?? Date(),
                origin: .elsewhere,
                latitude: asset.location?.coordinate.latitude,
                longitude: asset.location?.coordinate.longitude
            ))
        }
        return picked
    }

    func image(for candidate: OnThisDayCandidate, targetPixels: CGFloat) async -> UIImage? {
        let key = "\(candidate.id)@\(Int(targetPixels))" as NSString
        if let hit = images.object(forKey: key) { return hit }
        let fetched = PHAsset.fetchAssets(withLocalIdentifiers: [candidate.id], options: nil)
        guard let asset = fetched.firstObject else { return nil }
        let scale = await MainActor.run { UITraitCollection.current.displayScale }
        let size = CGSize(width: targetPixels / max(scale, 1), height: targetPixels / max(scale, 1))
        let options = PHImageRequestOptions()
        options.deliveryMode = .highQualityFormat
        // A photo that only exists in iCloud is still the person's photo;
        // downloading it to memory is browsing, not a second copy on disk.
        options.isNetworkAccessAllowed = true
        let image = await Self.requestImage(asset: asset, size: size, options: options)
        if let image { images.setObject(image, forKey: key) }
        return image
    }

    /// How long one photo may take before the card gives up and lets the
    /// person swipe on.  Long enough for an iCloud original over a thin
    /// connection, short enough that a stalled download does not read as a
    /// hung app.
    private static let imageTimeout: Duration = .seconds(20)

    /// One PhotoKit request, resolved exactly once.
    ///
    /// The result handler can be called more than once, and for an asset whose
    /// iCloud download stalls it can deliver a degraded placeholder and then
    /// nothing at all.  Waiting on a final delivery that never comes leaves
    /// the card spinning for the life of the process and leaks the
    /// continuation, so a delivery carrying an error or a cancellation counts
    /// as the last word, and a stalled request expires on its own.
    private nonisolated static func requestImage(
        asset: PHAsset, size: CGSize, options: PHImageRequestOptions
    ) async -> UIImage? {
        await withCheckedContinuation { continuation in
            let once = SingleResume(continuation)
            let requestID = PHImageManager.default().requestImage(
                for: asset, targetSize: size, contentMode: .aspectFit, options: options
            ) { image, info in
                let degraded = (info?[PHImageResultIsDegradedKey] as? NSNumber)?.boolValue ?? false
                let terminal = info?[PHImageErrorKey] != nil
                    || ((info?[PHImageCancelledKey] as? NSNumber)?.boolValue ?? false)
                // A degraded frame is a placeholder held up while the real one
                // loads — unless it arrives with the news that there will not
                // be a real one.
                guard !degraded || terminal else { return }
                once.resume(image)
            }
            once.arm(timeout: Self.imageTimeout) {
                PHImageManager.default().cancelImageRequest(requestID)
            }
        }
    }
}

/// Resumes its continuation exactly once, for whichever of the two racers
/// arrives first: PhotoKit's result handler, or the timeout guarding it.
/// PhotoKit calls that handler on a queue of its own choosing and may call it
/// repeatedly, and resuming a continuation twice is a crash, not a warning.
private final class SingleResume: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<UIImage?, Never>?
    private var timeout: Task<Void, Never>?

    init(_ continuation: CheckedContinuation<UIImage?, Never>) {
        self.continuation = continuation
    }

    /// Starts the timeout — unless the request has already answered, in which
    /// case there is nothing left to guard.
    func arm(timeout duration: Duration, onExpiry: @escaping @Sendable () -> Void) {
        let task = Task { [weak self] in
            try? await Task.sleep(for: duration)
            guard !Task.isCancelled else { return }
            onExpiry()
            self?.resume(nil)
        }
        lock.lock()
        let stillWaiting = continuation != nil
        if stillWaiting { timeout = task }
        lock.unlock()
        if !stillWaiting { task.cancel() }
    }

    func resume(_ image: UIImage?) {
        lock.lock()
        let pending = continuation
        let guarding = timeout
        continuation = nil
        timeout = nil
        lock.unlock()
        guard let pending else { return }
        guarding?.cancel()
        pending.resume(returning: image)
    }
}

// MARK: - Model

@MainActor
final class OnThisDayModel: ObservableObject {
    @Published private(set) var authorization: OnThisDayAuthorization = .notDetermined
    @Published private(set) var candidates: [OnThisDayCandidate] = []
    @Published private(set) var isLoading = false
    @Published private(set) var index = 0
    @Published private(set) var currentImage: UIImage?
    @Published private(set) var currentCandidate: OnThisDayCandidate?
    @Published private(set) var imageFailed = false
    @Published private(set) var cachedImages: [String: UIImage] = [:]

    var selectedID: String? { candidates.indices.contains(index) ? candidates[index].id : nil }
    var canSend: Bool { currentImage != nil && currentCandidate?.id == selectedID }
    var canGoBack: Bool { index > 0 }
    var canAdvance: Bool { index + 1 < candidates.count || !libraryExhausted }

    /// Keep a few candidates ready without decoding the entire library.
    private static let reserve = 3
    /// One top-up.  Small enough that a person who swipes twice and leaves has
    /// not made the library do work for nothing.
    private static let randomBatch = 6

    private let library: any OnThisDayLibrary
    private var imageGeneration = 0
    /// The one top-up in flight, so two swipes cannot start two fetches and a
    /// swipe that arrives mid-fetch waits for it instead of being dropped.
    private var topUpTask: Task<Void, Never>?
    /// The library has no more to give.  Asking again would only spend two
    /// hundred draws to be told the same thing.
    private var libraryExhausted = false

    init(library: any OnThisDayLibrary = OnThisDayLibraryResolver.makeDefault()) {
        self.library = library
    }

    func refreshAuthorization() async {
        authorization = await library.authorization()
    }

    /// The one place the system prompt is triggered from: the person pressed
    /// the button under the explanation, so the dialog's sentence — "Murmur
    /// 想在本机翻找你相册里同一天的旧照片" — is answering a question they asked.
    func requestAuthorization() async {
        authorization = await library.requestAuthorization()
        if authorization == .authorized { await load() }
    }

    func load() async {
        guard authorization == .authorized else { return }
        isLoading = true
        imageGeneration += 1
        libraryExhausted = false
        cachedImages = [:]
        currentImage = nil
        currentCandidate = nil
        candidates = await library.candidates(around: Date(), yearsBack: 5)
        index = 0
        // A blank day is not a dead end.  With nothing from this day in any
        // earlier year the shelf starts on album photos instead, and each card
        // says which it is rather than dressing a Tuesday up as an anniversary.
        await topUpIfNeeded()
        isLoading = false
        await showCurrent()
    }

    func select(id: String) {
        guard let target = candidates.firstIndex(where: { $0.id == id }), target != index else { return }
        index = target
        // Invalidate immediately, before an older request can resume.
        imageGeneration += 1
        currentImage = nil
        currentCandidate = nil
        imageFailed = false
        Task {
            await showCurrent()
            await topUpIfNeeded()
        }
    }

    func previous() {
        guard canGoBack else { return }
        select(id: candidates[index - 1].id)
    }

    func advance() {
        guard !candidates.isEmpty else { return }
        if index + 1 < candidates.count {
            select(id: candidates[index + 1].id)
        } else {
            let origin = selectedID
            Task {
                await topUp()
                guard selectedID == origin, index + 1 < candidates.count else { return }
                select(id: candidates[index + 1].id)
            }
        }
    }

    func retryImage() {
        guard imageFailed else { return }
        imageFailed = false
        Task { await showCurrent() }
    }

    private func topUpIfNeeded() async {
        guard candidates.count - index - 1 < Self.reserve else { return }
        await topUp()
    }

    /// Appends the next handful of album photos.  A second caller arriving
    /// mid-fetch waits for the same task rather than starting another — and,
    /// because the append happens inside that task, waking up after it means
    /// the photos are already on the shelf.
    private func topUp() async {
        if let existing = topUpTask {
            await existing.value
            return
        }
        guard !libraryExhausted else { return }
        let known = Set(candidates.map(\.id))
        let task = Task { @MainActor [weak self, library] in
            let more = await library.randomCandidates(
                count: Self.randomBatch, excluding: known
            )
            guard let self else { return }
            if more.isEmpty {
                self.libraryExhausted = true
            } else {
                self.candidates.append(contentsOf: more)
            }
        }
        topUpTask = task
        await task.value
        topUpTask = nil
    }

    private func showCurrent() async {
        guard candidates.indices.contains(index) else {
            currentImage = nil
            currentCandidate = nil
            return
        }
        imageGeneration += 1
        let generation = imageGeneration
        let candidate = candidates[index]
        let image: UIImage?
        if let cached = cachedImages[candidate.id] {
            image = cached
        } else {
            image = await library.image(for: candidate, targetPixels: MurmurImageCache.fullScreenPixels)
        }
        guard generation == imageGeneration, candidate.id == selectedID else { return }
        currentImage = image
        currentCandidate = image == nil ? nil : candidate
        imageFailed = image == nil
        if let image {
            let nearby = Set(candidates[max(0, index - 1)...min(candidates.count - 1, index + 1)].map(\.id))
            cachedImages = cachedImages.filter { nearby.contains($0.key) }
            cachedImages[candidate.id] = image
        }
    }
}

// MARK: - Stub (UI tests)

#if DEBUG
enum OnThisDayLibraryResolver {
    static func makeDefault() -> any OnThisDayLibrary {
        if let stub = StubOnThisDayLibrary(arguments: ProcessInfo.processInfo.arguments) {
            return stub
        }
        return PhotoKitOnThisDayLibrary()
    }
}

/// `--murmur-stub-onthisday`          → authorized, three photos from this day
/// `--murmur-stub-onthisday-ask`      → notDetermined; the button flips it
/// `--murmur-stub-onthisday-denied`   → denied
/// `--murmur-stub-onthisday-limited`  → limited (the entry disc stays hidden)
/// `--murmur-stub-onthisday-empty`    → blank day, album photos behind it
/// `--murmur-stub-onthisday-barren`   → authorized, and nothing anywhere
private struct StubOnThisDayLibrary: OnThisDayLibrary {
    let state: OnThisDayAuthorization
    let shelfSize: Int
    /// How many album photos the stub library holds behind the day's own.
    let albumSize: Int

    init?(arguments: [String]) {
        let flags = Set(arguments)
        if flags.contains("--murmur-stub-onthisday") { state = .authorized; shelfSize = 3; albumSize = 9 }
        else if flags.contains("--murmur-stub-onthisday-ask") { state = .notDetermined; shelfSize = 3; albumSize = 9 }
        else if flags.contains("--murmur-stub-onthisday-denied") { state = .denied; shelfSize = 0; albumSize = 0 }
        else if flags.contains("--murmur-stub-onthisday-limited") { state = .limited; shelfSize = 0; albumSize = 0 }
        else if flags.contains("--murmur-stub-onthisday-empty") { state = .authorized; shelfSize = 0; albumSize = 9 }
        else if flags.contains("--murmur-stub-onthisday-barren") { state = .authorized; shelfSize = 0; albumSize = 0 }
        else { return nil }
    }

    func authorization() async -> OnThisDayAuthorization { state }

    func requestAuthorization() async -> OnThisDayAuthorization {
        state == .notDetermined ? .authorized : state
    }

    func candidates(around date: Date, yearsBack: Int) async -> [OnThisDayCandidate] {
        let calendar = Calendar.current
        return (0..<shelfSize).map { offset in
            let yearsAgo = offset + 1
            let day = calendar.date(byAdding: .year, value: -yearsAgo, to: date) ?? date
            return OnThisDayCandidate(
                id: "stub-\(offset)", creationDate: day,
                origin: .sameDay(yearsAgo: yearsAgo),
                latitude: 31.201, longitude: 121.447
            )
        }
    }

    func randomCandidates(count: Int, excluding: Set<String>) async -> [OnThisDayCandidate] {
        let calendar = Calendar.current
        return (0..<albumSize)
            .map { "stub-album-\($0)" }
            .filter { !excluding.contains($0) }
            .prefix(count)
            .map { identifier in
                let days = abs(identifier.hashValue) % 900 + 30
                return OnThisDayCandidate(
                    id: identifier,
                    creationDate: calendar.date(byAdding: .day, value: -days, to: Date()) ?? Date(),
                    origin: .elsewhere,
                    latitude: 31.201, longitude: 121.447
                )
            }
    }

    func image(for candidate: OnThisDayCandidate, targetPixels: CGFloat) async -> UIImage? {
        let colors: [UIColor] = [.systemTeal, .systemIndigo, .systemOrange]
        let index = abs(candidate.id.hashValue) % colors.count
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 900, height: 1200))
        return renderer.image { ctx in
            colors[index].setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 900, height: 1200))
        }
    }
}
#else
enum OnThisDayLibraryResolver {
    static func makeDefault() -> any OnThisDayLibrary {
        PhotoKitOnThisDayLibrary()
    }
}
#endif

// MARK: - Flow

/// One navigation container owns the close action throughout the photo flow.
struct OnThisDayFlowView: View {
    @ObservedObject var model: OnThisDayModel
    let makeRoom: (UIImage, PhotoProvenance?) -> PhotoRoomModel
    @Environment(\.murmurReduceMotion) private var reduceMotion
    @Environment(\.dismiss) private var dismiss
    @State private var room: PhotoRoomModel?

    var body: some View {
        NavigationStack {
            ZStack {
                if let room {
                    PhotoRoomView(model: room)
                        .transition(.opacity)
                } else {
                    OnThisDayView(model: model) { image, provenance in
                        guard room == nil else { return }
                        withAnimation(reduceMotion ? nil : MurmurMotion.content) {
                            room = makeRoom(image, provenance)
                        }
                    }
                    .transition(.opacity)
                }
            }
            .navigationTitle(room.map { room in
                room.photoDate.map { $0.formatted(.dateTime.year().month().day().locale(Locale(identifier: "zh_Hans_CN"))) } ?? "照片房间"
            } ?? "当年今日")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("完成") { dismiss() }
                        .accessibilityLabel(room == nil ? "关闭当年今日" : "离开这张照片")
                        .accessibilityIdentifier(room == nil ? "close-onthisday" : "close-photo-room")
                }
            }
        }
    }
}

// MARK: - View

/// Native horizontal browsing; only an explicit button creates a photo room.
struct OnThisDayView: View {

    @ObservedObject var model: OnThisDayModel
    let onSend: (UIImage, PhotoProvenance?) -> Void
    @Environment(\.murmurReduceMotion) private var reduceMotion

    @State private var dateHeaderHeight: CGFloat = 76
    @State private var isSending = false

    var body: some View {
        ZStack {
            MurmurTheme.paper.ignoresSafeArea()
            switch model.authorization {
            case .notDetermined:
                gate(
                    title: "翻翻同一天的旧照片",
                    message: "Murmur 想在本机翻找你相册里同一天的旧照片；发送哪一张，由你决定。照片只在这台设备上翻找。",
                    buttonTitle: "允许翻找",
                    identifier: "onthisday-allow"
                ) {
                    Task { await model.requestAuthorization() }
                }
            case .denied:
                gate(
                    title: "相册的入口关着",
                    message: "没有相册权限，Murmur 翻不到那一天。到系统设置里打开后，回来就能翻。",
                    buttonTitle: "前往系统设置",
                    identifier: "onthisday-open-settings"
                ) {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                    UIApplication.shared.open(url)
                }
            case .limited:
                // The ordinary path now.  当年今日 is a tab, and a tab cannot
                // quietly absent itself the way the old disc did — so limited
                // access says what it is instead of the day looking empty.
                gate(
                    title: "只能看到你选的那几张",
                    message: "当年今日需要翻整个相册才找得到那一天。在系统设置里把权限改成「所有照片」后再来。",
                    buttonTitle: "前往系统设置",
                    identifier: "onthisday-open-settings"
                ) {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                    UIApplication.shared.open(url)
                }
            case .authorized:
                content
            }
        }
        .task {
            await model.refreshAuthorization()
            if model.authorization == .authorized, model.candidates.isEmpty {
                await model.load()
            }
        }

    }

    @ViewBuilder
    private var content: some View {
        if model.isLoading {
            ProgressView().tint(MurmurTheme.accentInk)
                .accessibilityIdentifier("onthisday-loading")
        } else if model.candidates.isEmpty {
            // Full access, and the library itself is empty — the one honest
            // dead end left.  A blank day is not this: it falls back to album
            // photos, so it never reaches here.
            VStack(spacing: 12) {
                Text("相册里还没有照片。")
                    .font(MurmurTheme.display(.title3))
                    .foregroundStyle(MurmurTheme.ink)
                Text("等你拍下第一张，这里就有东西可翻了。")
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.secondaryInk)
            }
            .padding(MurmurTheme.pageInset)
            .accessibilityIdentifier("onthisday-empty")
        } else {
            viewer
        }
    }

    private var viewer: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 16) {
                    if let candidate = model.candidates.first(where: { $0.id == model.selectedID }) {
                        VStack(spacing: 6) {
                            Text(dateLine(for: candidate))
                                .font(.title2.weight(.semibold))
                                .accessibilityIdentifier("onthisday-date")
                            Text(caption(for: candidate))
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 20)
                        .padding(.top, 8)
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { dateHeaderHeight = $0 }
                        .accessibilityElement(children: .contain)
                    }
                    TabView(selection: Binding(
                        get: { model.selectedID ?? "" },
                        set: { model.select(id: $0) }
                    )) {
                        ForEach(model.candidates) { candidate in
                            photoPage(candidate)
                                .tag(candidate.id)
                        }
                    }
                    .tabViewStyle(.page(indexDisplayMode: .never))
                    .frame(height: max(160, geometry.size.height - dateHeaderHeight - 24))
                    .accessibilityIdentifier("onthisday-pager")
                }
                .padding(.bottom, 8)
                .frame(maxWidth: 720)
                .frame(maxWidth: .infinity)
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { photoActions }
    }

    private var photoActions: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 16) {
                previousButton
                startRoomButton.fixedSize()
                nextButton
            }
            VStack(spacing: 8) {
                startRoomButton
                HStack {
                    previousButton
                    Spacer()
                    nextButton
                }
            }
        }
        .frame(maxWidth: 600)
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(MurmurTheme.paper)
    }

    private var previousButton: some View {
        Button("上一张", systemImage: "chevron.left") { model.previous() }
            .labelStyle(.iconOnly)
            .frame(minWidth: 44, minHeight: 44)
            .disabled(!model.canGoBack || isSending)
            .accessibilityIdentifier("onthisday-previous")
    }

    private var nextButton: some View {
        Button("下一张", systemImage: "chevron.right") { model.advance() }
            .labelStyle(.iconOnly)
            .frame(minWidth: 44, minHeight: 44)
            .disabled(!model.canAdvance || isSending)
            .accessibilityIdentifier("onthisday-next")
    }

    private var startRoomButton: some View {
        Button("聊聊这张", systemImage: "bubble.left") { send() }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(!model.canSend || isSending)
            .accessibilityIdentifier("onthisday-send")
    }

    private func photoPage(_ candidate: OnThisDayCandidate) -> some View {
        ZStack {
            if let image = model.cachedImages[candidate.id] {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .transition(.opacity)
            } else if candidate.id == model.selectedID && model.imageFailed {
                ContentUnavailableView {
                    Label("照片暂时载入不了", systemImage: "photo")
                } description: {
                    Text("可以重试，或换一张照片。")
                } actions: {
                    Button("重试") { model.retryImage() }
                        .accessibilityIdentifier("onthisday-retry")
                }
            } else {
                ProgressView("正在载入照片")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, 20)
        .animation(reduceMotion ? nil : MurmurMotion.content, value: model.cachedImages[candidate.id] != nil)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(model.cachedImages[candidate.id] != nil
            ? "\(caption(for: candidate))的照片"
            : "\(caption(for: candidate))的照片，\(candidate.id == model.selectedID && model.imageFailed ? "载入失败" : "正在载入")")
        .accessibilityIdentifier(candidate.id == model.selectedID ? "onthisday-photo" : "onthisday-neighbor")
        .accessibilityAction(named: "下一张") { model.advance() }
        .accessibilityAction(named: "上一张") { model.previous() }
        .accessibilityAction(named: "聊聊这张") { send() }
    }

    private func send() {
        guard !isSending, model.canSend, let image = model.currentImage else { return }
        isSending = true
        onSend(image, model.currentCandidate?.provenance)
    }

    private func gate(
        title: String,
        message: String,
        buttonTitle: String,
        identifier: String,
        action: @escaping () -> Void
    ) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            Spacer()
            Text(title)
                .font(MurmurTheme.display(.title2))
                .foregroundStyle(MurmurTheme.ink)
                .accessibilityAddTraits(.isHeader)
            Text(message)
                .font(MurmurTheme.body(.body))
                .foregroundStyle(MurmurTheme.secondaryInk)
                .fixedSize(horizontal: false, vertical: true)
            Button(action: action) {
                Text(buttonTitle)
                    .font(MurmurTheme.body(.body, weight: .semibold))
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .foregroundStyle(MurmurTheme.paper)
                    .background(MurmurTheme.ink, in: RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.automatic)
            .accessibilityIdentifier(identifier)
            Spacer()
            Spacer()
        }
        .frame(maxWidth: 520)
        .padding(.horizontal, 28)
        .frame(maxWidth: .infinity)
    }

    private func caption(for candidate: OnThisDayCandidate) -> String {
        switch candidate.origin {
        case .sameDay(let yearsAgo):
            yearsAgo == 1 ? "去年的今天" : "\(yearsAgo) 年前的今天"
        case .elsewhere:
            // Not this day, and the card says so.  Dressing an ordinary
            // Tuesday up as an anniversary is the one thing this screen
            // cannot do and still be worth opening.
            "相册里翻到的"
        }
    }

    private func dateLine(for candidate: OnThisDayCandidate) -> String {
        // The app's voice is Chinese-first; .formatted(date:) alone would
        // follow the system locale and say "August 20, 2025".
        candidate.creationDate.formatted(
            .dateTime.year().month().day().locale(Locale(identifier: "zh_Hans_CN"))
        )
    }
}
