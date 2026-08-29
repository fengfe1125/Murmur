import Foundation
import UIKit

@MainActor
final class MurmurSessionModel: ObservableObject {
    @Published var draftText = ""
    @Published private(set) var draftPhoto: PhotoAttachment?
    @Published private(set) var currentPhoto: PhotoAttachment?
    @Published private(set) var currentNote = ""
    @Published private(set) var bubbles: [MurmurBubble] = []
    @Published private(set) var move: String?
    @Published private(set) var scene: String?
    @Published private(set) var currentMomentID: String?
    @Published private(set) var phase: MurmurPhase = .idle
    @Published private(set) var connection: MurmurConnectionState = .checking
    @Published private(set) var failure: MurmurFailure?
    @Published private(set) var identity: MurmurIdentity?
    @Published var preferences = MurmurPreferences()
    @Published private(set) var devices: [MurmurDevice] = []
    @Published private(set) var devicesLoaded = false
    @Published private(set) var settingsMessage: String?
    @Published private(set) var notificationPromptRequested = false
    @Published private(set) var requiresDeviceReconnect = false
    /// The scrollback the person reads.  Held here rather than derived from
    /// `bubbles`, which only ever describes the moment in flight.
    @Published private(set) var messages: [MurmurMessage] = []
    /// Why an outgoing row never landed, keyed by that row.
    ///
    /// A send that failed belongs to the line the person wrote, not to the
    /// screen: the mark and the reason sit on the bubble itself, so a second
    /// message sent after the failure does not inherit the first one's error,
    /// and two failed rows each say their own piece.  In memory only — reading
    /// back 「暂时没有连上」 a week later would be a lie about now.
    @Published private(set) var sendFailures: [String: MurmurSendFailure] = [:]
    /// A problem with what is still in the composer — a photo that could not be
    /// read.  Kept apart from `sendFailures` because a draft has no transcript
    /// row to carry a mark; this is the one thing the composer says out loud.
    @Published private(set) var draftFailure: String?
    /// Picking a photo runs on its own clock.  A photo can be chosen while
    /// Murmur is still answering the previous message, so its progress cannot
    /// live in `phase`, which describes the moment in flight.
    @Published private(set) var isPreparingPhoto = false
    /// True from the moment something is queued until the last reply lands.
    /// The typing indicator reads this rather than `phase`, so choosing a photo
    /// mid-answer does not make Murmur look like it stopped talking.
    @Published private(set) var isAwaitingReply = false

    let transcriptStore: MurmurTranscriptStore
    /// 当年今日's own history, kept apart from the conversation.  Owned here
    /// because this is what hands it to a room; 当年今日's tab reads the same
    /// object back, so a room that just closed is already on the calendar.
    let archive: MurmurArchive
    private let api: any MurmurAPIClient
    private let photoLoader: PhotoLoader
    private let requestTimeoutSeconds: TimeInterval
    private let uploadTimeoutSeconds: TimeInterval
    private let bubblePacing: MurmurBubblePacing
    /// Index of the outgoing message whose ticks the running moment drives.
    private var pendingMessageID: String?
    /// Messages wait their turn rather than blocking the composer: a person can
    /// type the next line while Murmur is still answering the last one, and the
    /// server still sees one moment at a time.
    private var queue: [Submission] = []
    private var pumpTask: Task<Void, Never>?
    private var pumpGeneration = 0
    private var isRunning = false
    private var photoTask: Task<Void, Never>?
    /// Bumped whenever the draft photo is replaced, removed or cancelled, so a
    /// decode that finishes late knows it is no longer the one being waited on.
    private var photoGeneration = 0
    /// Copies of sent photos into transcript storage, keyed by message, so the
    /// upload's cleanup can wait for the copy instead of racing it.
    private var adoptTasks: [String: Task<Void, Never>] = [:]
    /// Failed sends that can still be tried again, keyed by the row they belong
    /// to.  A row's mark is only an offer while its submission is here: the same
    /// idempotency key and, for a photo, a temporary file that has not been
    /// swept up yet.  Everything else shows the mark and no offer.
    private var resendable: [String: Submission] = [:]
    /// Reads of a failed row's photo back out of transcript storage, keyed by
    /// that row, so a second press does not start a second copy.
    private var rebuildTasks: [String: Task<Void, Never>] = [:]
    private var lastBubbleAt: Date?
    private var lastSubmission: Submission?
    private var didBootstrap = false
    private var pendingAPNSToken: String?
    private var hasPendingPushRegistration = false
    private var proactiveMomentID: String?
    private var preferencesLoaded = false
    private var didRequestNotificationPrompt = false

    init(
        api: any MurmurAPIClient,
        photoLoader: PhotoLoader = PhotoLoader(),
        requestTimeoutSeconds: TimeInterval = 45,
        uploadTimeoutSeconds: TimeInterval = 300,
        transcriptStore: MurmurTranscriptStore = MurmurTranscriptStore(),
        archive: MurmurArchive? = nil,
        bubblePacing: MurmurBubblePacing = .human
    ) {
        self.api = api
        self.photoLoader = photoLoader
        self.requestTimeoutSeconds = requestTimeoutSeconds
        self.uploadTimeoutSeconds = uploadTimeoutSeconds
        self.transcriptStore = transcriptStore
        self.archive = archive ?? MurmurArchive()
        self.bubblePacing = bubblePacing
    }

    // ---- Transcript ---------------------------------------------------------

    func loadTranscript() async {
        guard messages.isEmpty else { return }
        messages = await transcriptStore.load()
    }

    func clearTranscript() async {
        messages = []
        pendingMessageID = nil
        adoptTasks = [:]
        for task in rebuildTasks.values { task.cancel() }
        rebuildTasks = [:]
        // The marks belonged to rows that no longer exist; the originals those
        // rows were holding for a resend go with them.
        withdrawResendOffers()
        sendFailures = [:]
        await transcriptStore.clear()
    }

    private func persistTranscript() {
        let snapshot = messages
        Task { [transcriptStore] in await transcriptStore.save(snapshot) }
    }

    private func append(_ message: MurmurMessage) {
        messages.append(message)
        persistTranscript()
    }

    private func updatePending(_ mutate: (inout MurmurMessage) -> Void) {
        guard let id = pendingMessageID,
              let index = messages.firstIndex(where: { $0.id == id }) else { return }
        mutate(&messages[index])
        persistTranscript()
    }

    /// A reply already in flight is no reason to hold the next line back; only
    /// a photo that has not finished decoding is, because sending then would
    /// silently drop it.
    var canSubmit: Bool {
        !isPreparingPhoto
            && (!draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draftPhoto != nil)
    }

    var hasCurrentMoment: Bool {
        currentMomentID != nil || currentPhoto != nil || !currentNote.isEmpty || !bubbles.isEmpty || phase.isBusy || phase == .quiet
    }

    var statusText: String {
        switch phase {
        case .idle: "此刻为空"
        case .preparingPhoto: "正在准备照片"
        case .ready: "准备好了"
        case .uploading: "正在送往 Murmur"
        case .responding: "Murmur 正在回应"
        case .complete: "这一刻已完成"
        case .quiet: "Murmur 选择安静陪着"
        case .error: "没有送达"
        }
    }

    func bootstrap() async {
        guard !didBootstrap else { return }
        didBootstrap = true
        await photoLoader.cleanupStaleTemporaryFiles()
        connection = .checking
        do {
            identity = try await api.storedIdentity()
            guard identity != nil else {
                requiresDeviceReconnect = false
                connection = .needsEnrollment
                return
            }
            devices = try await withTimeout(seconds: requestTimeoutSeconds) { [api] in
                try await api.devices()
            }
            devicesLoaded = true
            connection = .connected
            do {
                preferences = try await withTimeout(seconds: requestTimeoutSeconds) { [api] in
                    try await api.preferences()
                }
                preferencesLoaded = true
            } catch {
                settingsMessage = MurmurFailure.from(error).message
            }
            if hasPendingPushRegistration { await syncDevice(token: pendingAPNSToken) }
        } catch {
            let mapped = MurmurFailure.from(error)
            requiresDeviceReconnect = mapped.requiresDeviceReconnect
            connection = .offline(mapped.message)
        }
    }

    func enroll(inviteCode: String) async {
        let code = inviteCode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !code.isEmpty, !phase.isBusy else { return }
        phase = .uploading
        failure = nil
        do {
            identity = try await withTimeout(seconds: requestTimeoutSeconds) { [api] in
                try await api.enroll(inviteCode: code, deviceName: Self.genericDeviceName)
            }
            connection = .connected
            requiresDeviceReconnect = false
            phase = .idle
            if hasPendingPushRegistration { await syncDevice(token: pendingAPNSToken) }
        } catch {
            let mapped = MurmurFailure.from(error)
            failure = mapped
            connection = .needsEnrollment
            phase = .error
        }
    }

    func beginPhotoSelection() {
        photoTask?.cancel()
        photoGeneration += 1
        isPreparingPhoto = true
        failure = nil
        draftFailure = nil
        phase = .preparingPhoto
    }

    func failPhotoSelection() {
        guard isPreparingPhoto else { return }
        photoGeneration += 1
        isPreparingPhoto = false
        let mapped = MurmurFailure(code: "photo_unavailable", message: "没有读取到这张图片。", retryable: false)
        failure = mapped
        draftFailure = mapped.message
        phase = .error
    }

    func preparePhoto(at url: URL) {
        let generation = beginPreparing()
        photoTask = Task { [weak self] in
            guard let self else { return }
            let loaded: PhotoAttachment
            do {
                loaded = try await self.photoLoader.load(fileURL: url)
            } catch is CancellationError {
                await self.photoLoader.discardFile(at: url)
                self.finishPreparing(generation)
                return
            } catch {
                await self.photoLoader.discardFile(at: url)
                self.failPreparing(generation, with: error)
                return
            }
            guard self.adopt(loaded, generation: generation) else {
                await self.photoLoader.discard(loaded)
                await self.photoLoader.discardFile(at: url)
                return
            }
        }
    }

    func prepareCapturedPhoto(_ image: UIImage) {
        prepareImage(image) { try await $0.load(capturedImage: image) }
    }

    /// A room for one photo swiped up out of 当年今日.  Built here rather than
    /// in the view so it borrows the app's own authenticated client, photo
    /// loader and timeouts: the room's upload goes out the same door as every
    /// other, and its temporary original lands under the same swept prefix.
    func makePhotoRoom(image: UIImage) -> PhotoRoomModel {
        PhotoRoomModel(
            image: image,
            api: api,
            photoLoader: photoLoader,
            uploadTimeoutSeconds: uploadTimeoutSeconds,
            requestTimeoutSeconds: requestTimeoutSeconds,
            bubblePacing: bubblePacing,
            // Into the archive, never the conversation: what is said about an
            // old photo belongs to the day it was said on, and the chat stays
            // a chat.  当年今日 reads that archive back as a calendar.
            transcript: archive
        )
    }

    /// Today and older archive dates use one continuation model.  Building it
    /// here keeps the authenticated client, timeout and bubble rhythm aligned
    /// with the photo room and ordinary conversation.
    func makeArchiveDay(day: Date) -> ArchiveDayModel {
        ArchiveDayModel(
            day: day,
            archive: archive,
            api: api,
            requestTimeoutSeconds: requestTimeoutSeconds,
            bubblePacing: bubblePacing
        )
    }

    private func prepareImage(
        _ image: UIImage,
        using load: @escaping @Sendable (PhotoLoader) async throws -> PhotoAttachment
    ) {
        let generation = beginPreparing()
        photoTask = Task { [weak self] in
            guard let self else { return }
            let loaded: PhotoAttachment
            do {
                loaded = try await load(self.photoLoader)
            } catch is CancellationError {
                self.finishPreparing(generation)
                return
            } catch {
                self.failPreparing(generation, with: error)
                return
            }
            guard self.adopt(loaded, generation: generation) else {
                await self.photoLoader.discard(loaded)
                return
            }
        }
    }

    func removeDraftPhoto() {
        photoTask?.cancel()
        photoGeneration += 1
        isPreparingPhoto = false
        draftFailure = nil
        let old = draftPhoto
        draftPhoto = nil
        if phase == .ready || phase == .error || phase == .preparingPhoto {
            phase = draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .idle : .ready
        }
        Task { await photoLoader.discard(old) }
    }

    private func beginPreparing() -> Int {
        photoTask?.cancel()
        photoGeneration += 1
        isPreparingPhoto = true
        phase = .preparingPhoto
        failure = nil
        draftFailure = nil
        return photoGeneration
    }

    /// Late results are dropped by generation rather than by reading `phase`:
    /// a reply landing while the decode runs moves `phase` on, and a photo the
    /// person is still waiting for must survive that.
    private func adopt(_ photo: PhotoAttachment, generation: Int) -> Bool {
        guard generation == photoGeneration, !Task.isCancelled else { return false }
        let old = draftPhoto
        draftPhoto = photo
        isPreparingPhoto = false
        phase = .ready
        Task { [photoLoader] in await photoLoader.discard(old) }
        return true
    }

    private func finishPreparing(_ generation: Int) {
        guard generation == photoGeneration else { return }
        isPreparingPhoto = false
        phase = canSubmit ? .ready : .idle
    }

    private func failPreparing(_ generation: Int, with error: Error) {
        guard generation == photoGeneration else { return }
        isPreparingPhoto = false
        let mapped = MurmurFailure.from(error)
        failure = mapped
        draftFailure = mapped.message
        phase = .error
    }

    func submit() {
        guard canSubmit else { return }
        let note = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        let photo = draftPhoto
        // The outgoing turn joins the transcript before anything is queued, so
        // the bubble is on screen the instant the send button is pressed.
        let key = UUID().uuidString.lowercased()
        let outgoing = MurmurMessage(
            author: .you,
            text: note,
            sentAt: Date(),
            delivery: .sending,
            idempotencyKey: key
        )
        let submission = Submission(
            messageID: outgoing.id,
            note: note.isEmpty ? nil : note,
            photo: photo,
            idempotencyKey: key,
            replyToProactiveMomentID: note.isEmpty ? nil : proactiveMomentID
        )
        draftText = ""
        draftPhoto = nil
        draftFailure = nil
        proactiveMomentID = nil
        append(outgoing)
        if let photo {
            adoptTasks[outgoing.id] = Task { [transcriptStore] in
                let name = await transcriptStore.adoptImage(at: photo.originalURL, id: outgoing.id)
                await MainActor.run {
                    guard let name,
                          let index = self.messages.firstIndex(where: { $0.id == outgoing.id })
                    else { return }
                    self.messages[index].imageFile = name
                    self.persistTranscript()
                }
            }
        }
        enqueue(submission)
    }

    /// Send one failed row again.
    ///
    /// The row itself is the handle, not "the last error": by the time somebody
    /// reaches for the mark they may have sent two more lines, and the one they
    /// pressed is the one that has to go.  The submission keeps its original
    /// idempotency key, so a moment the server did accept before the wire broke
    /// is picked back up rather than said twice.
    ///
    /// A submission still in memory is used as it stands.  Otherwise the row
    /// itself is enough to build one: the transcript holds the words, its own
    /// copy of the photo, and the key the send went up under.  Before this, a
    /// relaunch turned every failed row into a mark that could not be pressed —
    /// which, after a bad afternoon on the server, is a screen full of messages
    /// with no way to send any of them.
    func resend(_ messageID: String) {
        sendFailures.removeValue(forKey: messageID)
        if let index = messages.firstIndex(where: { $0.id == messageID }) {
            messages[index].delivery = .sending
            persistTranscript()
        }
        if let submission = resendable.removeValue(forKey: messageID) {
            enqueue(submission)
            return
        }
        guard let row = messages.first(where: { $0.id == messageID }) else { return }
        // Re-reading the photo is I/O, so the row spins from the moment the
        // button is pressed rather than after the file comes back.
        rebuildTasks[messageID]?.cancel()
        rebuildTasks[messageID] = Task { [weak self] in
            guard let self else { return }
            let photo = await self.reloadPhoto(for: row)
            guard !Task.isCancelled else {
                await self.photoLoader.discard(photo)
                return
            }
            self.rebuildTasks.removeValue(forKey: messageID)
            let note = row.text.isEmpty ? nil : row.text
            guard note != nil || photo != nil else {
                // Nothing left to send: the picture this row carried is gone
                // from transcript storage and there were never any words.
                self.markRebuildFailed(messageID)
                return
            }
            self.enqueue(Submission(
                messageID: messageID,
                note: note,
                photo: photo,
                idempotencyKey: row.idempotencyKey ?? UUID().uuidString.lowercased(),
                replyToProactiveMomentID: nil
            ))
        }
    }

    /// Copies a row's photo back out of transcript storage into a temporary
    /// original the uploader can use.  The transcript keeps its own copy, so
    /// the send's cleanup deletes only the temporary one.
    private func reloadPhoto(for row: MurmurMessage) async -> PhotoAttachment? {
        guard let name = row.imageFile else { return nil }
        let url = transcriptStore.imageURL(for: name)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try? await photoLoader.load(fileURL: url)
    }

    private func markRebuildFailed(_ messageID: String) {
        rebuildTasks.removeValue(forKey: messageID)
        guard let index = messages.firstIndex(where: { $0.id == messageID }) else { return }
        messages[index].delivery = .failed
        persistTranscript()
        sendFailures[messageID] = .init(message: "这条的内容已经不在了。", canResend: false)
    }

    func cancelCurrentOperation() {
        photoTask?.cancel()
        photoGeneration += 1
        let original = lastSubmission?.photo
        let abandonedDraft = isPreparingPhoto ? draftPhoto : nil
        if isPreparingPhoto {
            draftPhoto = nil
            isPreparingPhoto = false
        }
        cancelPump()
        withdrawResendOffers()
        lastSubmission = nil
        failure = nil
        draftFailure = nil
        phase = currentMomentID == nil && currentNote.isEmpty && currentPhoto == nil ? .idle : .ready
        Task { [photoLoader] in
            await photoLoader.discard(original)
            await photoLoader.discard(abandonedDraft)
        }
    }

    func clearCurrent() {
        photoTask?.cancel()
        photoGeneration += 1
        cancelPump()
        withdrawResendOffers()
        let oldDraft = draftPhoto
        let oldCurrent = currentPhoto
        draftText = ""
        draftPhoto = nil
        isPreparingPhoto = false
        currentPhoto = nil
        currentNote = ""
        bubbles = []
        move = nil
        scene = nil
        currentMomentID = nil
        proactiveMomentID = nil
        failure = nil
        draftFailure = nil
        lastSubmission = nil
        phase = .idle
        Task {
            await photoLoader.discard(oldDraft)
            await photoLoader.discard(oldCurrent)
        }
    }

    /// Stops the line and tells the truth about the turns that never left: a
    /// row stuck on a spinner forever is worse than a row marked failed.
    private func cancelPump() {
        pumpTask?.cancel()
        pumpTask = nil
        let stranded = Set(queue.map(\.messageID)).union(pendingMessageID.map { [$0] } ?? [])
        for id in stranded { adoptTasks.removeValue(forKey: id) }
        queue = []
        isRunning = false
        isAwaitingReply = false
        pendingMessageID = nil
        var changed = false
        for index in messages.indices
        where stranded.contains(messages[index].id) && messages[index].delivery == .sending {
            messages[index].delivery = .failed
            // Cancelled on purpose, and no offer to say it again: the photo the
            // turn carried is being thrown away in the same breath, so a mark
            // that promised a resend would be promising an upload with nothing
            // left to upload.
            sendFailures[messages[index].id] = .init(message: "已取消发送。", canResend: false)
            resendable.removeValue(forKey: messages[index].id)
            changed = true
        }
        if changed { persistTranscript() }
    }

    func handleBackground() {
        // Deliberately keep the in-memory current moment alive while the process is running.
        // Nothing is persisted, so a true cold launch still begins empty.
    }

    func handleNotification(momentID: String) async {
        await refreshProactive(expectedMomentID: momentID)
    }

    /// Without APNs (development installs) no notification ever arrives, so
    /// launch/foreground polls for whatever Murmur sent on its own. Errors are
    /// swallowed by refreshProactive when no specific moment is expected.
    func checkProactive() async {
        await refreshProactive(expectedMomentID: nil)
    }

    func updatePushRegistration(token: String?) async {
        pendingAPNSToken = token
        hasPendingPushRegistration = true
        guard identity != nil else { return }
        await syncDevice(token: token)
    }

    func loadPreferences() async {
        settingsMessage = nil
        do {
            preferences = try await api.preferences()
            preferencesLoaded = true
        } catch {
            recordSettingsFailure(error)
        }
    }

    func savePreferences() async {
        settingsMessage = nil
        guard preferencesLoaded else {
            settingsMessage = "还没有读到当前设置。"
            return
        }
        do {
            try await api.updatePreferences(preferences)
            settingsMessage = "已保存"
        } catch {
            recordSettingsFailure(error)
        }
    }

    func refreshDevices() async {
        settingsMessage = nil
        do {
            devices = try await api.devices()
            devicesLoaded = true
        } catch {
            devicesLoaded = true
            recordSettingsFailure(error)
        }
    }

    func removeDevice(_ device: MurmurDevice) async {
        settingsMessage = nil
        do {
            try await api.removeDevice(deviceID: device.id)
            devices.removeAll { $0.id == device.id }
            if device.id == identity?.deviceID {
                identity = nil
                connection = .needsEnrollment
                clearCurrent()
            }
        } catch {
            recordSettingsFailure(error)
        }
    }

    func deleteAccount() async {
        settingsMessage = nil
        do {
            try await api.deleteAccount()
            identity = nil
            devices = []
            connection = .needsEnrollment
            clearCurrent()
        } catch {
            recordSettingsFailure(error)
        }
    }

    func resetLocalDeviceIdentity() async {
        settingsMessage = nil
        do {
            try await api.resetLocalIdentity()
            identity = nil
            devices = []
            requiresDeviceReconnect = false
            connection = .needsEnrollment
            clearCurrent()
        } catch {
            settingsMessage = MurmurFailure.from(error).message
        }
    }

    func consumeNotificationPromptRequest() {
        notificationPromptRequested = false
    }

    private func enqueue(_ submission: Submission) {
        queue.append(submission)
        isAwaitingReply = true
        phase = .uploading
        failure = nil
        guard pumpTask == nil else { return }
        pumpGeneration += 1
        let generation = pumpGeneration
        pumpTask = Task { [weak self] in
            await self?.pump(generation: generation)
        }
    }

    /// One moment at a time, in the order they were said.  The server treats a
    /// moment as a unit of attention, so overlapping them would have Murmur
    /// answering two halves of a thought at once.
    private func pump(generation: Int) async {
        while !queue.isEmpty, !Task.isCancelled {
            let next = queue.removeFirst()
            activate(next)
            isRunning = true
            await run(next)
            isRunning = false
        }
        guard pumpGeneration == generation else { return }
        pumpTask = nil
        isAwaitingReply = !queue.isEmpty
    }

    private func activate(_ submission: Submission) {
        let oldCurrent = currentPhoto
        currentPhoto = submission.photo
        currentNote = submission.note ?? ""
        currentMomentID = nil
        bubbles = []
        move = nil
        scene = nil
        lastSubmission = submission
        pendingMessageID = submission.messageID
        failure = nil
        phase = .uploading
        // A moment taking over sweeps the last one's original — unless a failed
        // row upstairs is still offering to send that same file again.  An
        // offer whose file has been deleted is a button that cannot work.
        if oldCurrent?.id != currentPhoto?.id, !isHeldForResend(oldCurrent) {
            Task { [photoLoader] in await photoLoader.discard(oldCurrent) }
        }
    }

    private func isHeldForResend(_ photo: PhotoAttachment?) -> Bool {
        guard let photo else { return false }
        return resendable.values.contains { $0.photo?.id == photo.id }
    }

    /// Drop every held submission and delete the temporary originals they were
    /// holding.  The marks stay, and so do their offers: what a resend needs is
    /// in the transcript — the words, its own copy of the photo, and the key —
    /// so losing the in-memory submission no longer costs the person the send.
    ///
    /// This is the terminal path for a temporary original held by a failed row,
    /// and the reason cancelling or clearing does not leave full-resolution
    /// photos behind in the temporary directory.
    private func withdrawResendOffers() {
        guard !resendable.isEmpty else { return }
        let photos = resendable.values.compactMap(\.photo)
        resendable = [:]
        Task { [photoLoader] in
            for photo in photos { await photoLoader.discard(photo) }
        }
    }

    private func run(_ submission: Submission) async {
        lastBubbleAt = nil
        do {
            if let proactiveMomentID = submission.replyToProactiveMomentID,
               let reply = submission.note {
                try await withTimeout(seconds: requestTimeoutSeconds) { [api] in
                    try await api.acknowledge(momentID: proactiveMomentID, reply: reply)
                }
            }
            let receipt = try await withTimeout(seconds: uploadTimeoutSeconds) { [api] in
                try await api.createMoment(
                    note: submission.note,
                    photo: submission.photo,
                    idempotencyKey: submission.idempotencyKey,
                    intent: nil
                )
            }
            try Task.checkCancellation()
            currentMomentID = receipt.momentID
            connection = .connected
            phase = .responding
            // One tick: the server has the moment.
            updatePending {
                $0.delivery = .sent
                $0.momentID = receipt.momentID
            }

            var lastEventID: String?
            var retries = 0
            var terminal = false
            var wasQuiet = false
            var seenEventIDs = Set<String>()
            while !terminal {
                let stream = await api.events(momentID: receipt.momentID, lastEventID: lastEventID)
                do {
                    for try await event in stream {
                        try Task.checkCancellation()
                        let eventID = event.eventID
                        if let eventID, seenEventIDs.contains(eventID) { continue }
                        if let eventID {
                            seenEventIDs.insert(eventID)
                            lastEventID = eventID
                        }
                        switch event {
                        case .accepted:
                            phase = .responding
                            // The clock for the first bubble starts here, so a
                            // server that thought for two seconds does not then
                            // make the reader wait another two.
                            lastBubbleAt = Date()
                            // Two ticks: Murmur has started composing.
                            updatePending { $0.delivery = .answered }
                        case let .bubble(_, text):
                            if !text.isEmpty {
                                try await pace(for: text)
                                let id = eventID ?? UUID().uuidString
                                bubbles.append(.init(id: id, text: text))
                                append(.init(
                                    id: "\(receipt.momentID)-\(id)",
                                    author: .murmur,
                                    text: text,
                                    momentID: receipt.momentID
                                ))
                                lastBubbleAt = Date()
                            }
                        case .angles:
                            // The three openers belong to 当年今日's room,
                            // which streams its own moments.  A chat moment
                            // never asks for them; if one ever arrives here it
                            // is somebody else's answer, and the conversation
                            // is not the place to show it.
                            break
                        case .quiet:
                            wasQuiet = true
                            phase = .quiet
                        case let .done(_, nextMove, nextScene):
                            move = nextMove
                            scene = nextScene
                            phase = wasQuiet && bubbles.isEmpty ? .quiet : .complete
                            terminal = true
                        case let .failure(_, streamFailure):
                            throw streamFailure
                        }
                    }
                    if !terminal {
                        throw MurmurFailure(code: "stream_ended", message: "回应中断了。", retryable: true)
                    }
                } catch {
                    guard !Task.isCancelled, retries < 2 else { throw error }
                    retries += 1
                    try await Task.sleep(for: .milliseconds(350 * retries))
                }
            }
            await discardAfterTranscriptCopy(submission)
            pendingMessageID = nil
            if !bubbles.isEmpty && !didRequestNotificationPrompt {
                didRequestNotificationPrompt = true
                notificationPromptRequested = true
            }
        } catch is CancellationError {
            return
        } catch {
            let mapped = MurmurFailure.from(error)
            updatePending { $0.delivery = .failed }
            pendingMessageID = nil
            // The row carries its own verdict from here on.  It is only an
            // offer while the submission survives with it — a turn that cannot
            // be retried has its photo swept up just below.
            sendFailures[submission.messageID] = .init(message: mapped.message, canResend: mapped.retryable)
            if mapped.retryable {
                resendable[submission.messageID] = submission
            }
            failure = mapped
            requiresDeviceReconnect = mapped.requiresDeviceReconnect
            if !mapped.retryable {
                await discardAfterTranscriptCopy(submission)
                lastSubmission = nil
            }
            if mapped.requiresDeviceReconnect {
                connection = .offline(mapped.message)
            }
            phase = .error
        }
    }

    /// Holds a bubble back to roughly the time it would take to type it,
    /// counting from when the previous one landed.
    private func pace(for text: String) async throws {
        let target = bubblePacing.delay(for: text)
        guard target > 0 else { return }
        let elapsed = lastBubbleAt.map { Date().timeIntervalSince($0) } ?? 0
        let remaining = target - elapsed
        guard remaining > 0 else { return }
        try await Task.sleep(for: .seconds(remaining))
    }

    /// The upload's temporary file is also what the transcript copies from, so
    /// deleting it before that copy lands would leave a photo message with no
    /// photo in it.
    private func discardAfterTranscriptCopy(_ submission: Submission) async {
        await adoptTasks.removeValue(forKey: submission.messageID)?.value
        await photoLoader.discard(submission.photo)
    }

    private func refreshProactive(expectedMomentID: String?) async {
        do {
            guard let proactive = try await api.currentProactive() else { return }
            if let expectedMomentID, proactive.momentID != expectedMomentID { return }
            currentMomentID = proactive.momentID
            proactiveMomentID = proactive.momentID
            bubbles = proactive.resolvedBubbles.enumerated().map {
                .init(id: "\(proactive.momentID)-\($0.offset)", text: $0.element)
            }
            move = proactive.move
            scene = proactive.scene
            // A message Murmur sent on its own belongs in the scrollback like
            // any other; without this it only ever existed in the notification.
            for bubble in bubbles where !messages.contains(where: { $0.id == bubble.id }) {
                append(.init(
                    id: bubble.id,
                    author: .murmur,
                    text: bubble.text,
                    momentID: proactive.momentID
                ))
            }
            // A send already on the wire keeps its own phase; a proactive
            // message arriving must not make it look finished.
            if !isRunning && queue.isEmpty {
                phase = bubbles.isEmpty ? .quiet : .complete
            }
            connection = .connected
            try await api.acknowledge(momentID: proactive.momentID, reply: nil)
        } catch {
            if expectedMomentID != nil {
                let mapped = MurmurFailure.from(error)
                failure = mapped
                // Only ever escalates: a routine network blip while fetching a
                // proactive message says nothing about whether the device's
                // binding is still good.
                if mapped.requiresDeviceReconnect { requiresDeviceReconnect = true }
                // Fetching a message Murmur sent on its own is a connection
                // problem, not a failed send: there is no row of the person's
                // own to mark, so it belongs where every other connection
                // trouble is already shown — the capsule at the top.
                connection = .offline(mapped.message)
                phase = .error
            }
        }
    }

    private func syncDevice(token: String?) async {
        do {
            try await api.updateDevice(
                apnsToken: token,
                environment: Self.pushEnvironment,
                timezone: TimeZone.current.identifier,
                deviceName: Self.genericDeviceName
            )
            connection = .connected
        } catch {
            let mapped = MurmurFailure.from(error)
            requiresDeviceReconnect = mapped.requiresDeviceReconnect
            connection = .offline(mapped.message)
        }
    }

    private func recordSettingsFailure(_ error: Error) {
        let mapped = MurmurFailure.from(error)
        settingsMessage = mapped.message
        if mapped.requiresDeviceReconnect {
            requiresDeviceReconnect = true
            connection = .offline(mapped.message)
        }
    }

    private static var pushEnvironment: String {
#if DEBUG
        "development"
#else
        "production"
#endif
    }

    private static var genericDeviceName: String {
        "\(UIDevice.current.model) · iOS \(UIDevice.current.systemVersion.split(separator: ".").first ?? "18")"
    }
}

private struct Submission: Sendable {
    /// The transcript row this send owns, so a retry re-uses the bubble the
    /// person already saw instead of saying the same thing twice.
    let messageID: String
    let note: String?
    let photo: PhotoAttachment?
    let idempotencyKey: String
    let replyToProactiveMomentID: String?
}

private extension MurmurStreamEvent {
    var eventID: String? {
        switch self {
        case let .accepted(id), let .bubble(id, _), let .angles(id, _), let .quiet(id),
             let .done(id, _, _), let .failure(id, _): id
        }
    }
}

/// Shared with 当年今日's photo room, which runs its own turns against the same
/// API client and needs the same ceiling on a request that never answers.
func withTimeout<T: Sendable>(
    seconds: TimeInterval,
    operation: @escaping @Sendable () async throws -> T
) async throws -> T {
    try await withThrowingTaskGroup(of: T.self) { group in
        group.addTask { try await operation() }
        group.addTask {
            try await Task.sleep(for: .seconds(seconds))
            throw MurmurFailure(code: "timeout", message: "等待时间有点久，请再试一次。", retryable: true)
        }
        guard let result = try await group.next() else { throw CancellationError() }
        group.cancelAll()
        return result
    }
}
