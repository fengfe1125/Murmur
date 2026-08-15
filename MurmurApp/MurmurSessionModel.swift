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

    private let api: any MurmurAPIClient
    private let photoLoader: PhotoLoader
    private let requestTimeoutSeconds: TimeInterval
    private let uploadTimeoutSeconds: TimeInterval
    private var operationTask: Task<Void, Never>?
    private var photoTask: Task<Void, Never>?
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
        uploadTimeoutSeconds: TimeInterval = 300
    ) {
        self.api = api
        self.photoLoader = photoLoader
        self.requestTimeoutSeconds = requestTimeoutSeconds
        self.uploadTimeoutSeconds = uploadTimeoutSeconds
    }

    var canSubmit: Bool {
        !phase.isBusy && (!draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draftPhoto != nil)
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
        guard phase != .uploading && phase != .responding else { return }
        photoTask?.cancel()
        failure = nil
        phase = .preparingPhoto
    }

    func failPhotoSelection() {
        guard phase == .preparingPhoto else { return }
        failure = .init(code: "photo_unavailable", message: "没有读取到这张图片。", retryable: false)
        phase = .error
    }

    func preparePhoto(at url: URL) {
        guard phase != .uploading && phase != .responding else { return }
        photoTask?.cancel()
        phase = .preparingPhoto
        failure = nil
        photoTask = Task { [weak self] in
            guard let self else { return }
            let loaded: PhotoAttachment
            do {
                loaded = try await self.photoLoader.load(fileURL: url)
            } catch is CancellationError {
                await self.photoLoader.discardFile(at: url)
                self.phase = self.canSubmit ? .ready : .idle
                return
            } catch {
                await self.photoLoader.discardFile(at: url)
                self.failure = MurmurFailure.from(error)
                self.phase = .error
                return
            }
            guard self.phase == .preparingPhoto, !Task.isCancelled else {
                await self.photoLoader.discard(loaded)
                await self.photoLoader.discardFile(at: url)
                return
            }
            let old = self.draftPhoto
            self.draftPhoto = loaded
            self.phase = .ready
            await self.photoLoader.discard(old)
        }
    }

    func prepareCapturedPhoto(_ image: UIImage) {
        guard !phase.isBusy else { return }
        photoTask?.cancel()
        phase = .preparingPhoto
        failure = nil
        photoTask = Task { [weak self] in
            guard let self else { return }
            let loaded: PhotoAttachment
            do {
                loaded = try await self.photoLoader.load(capturedImage: image)
            } catch is CancellationError {
                self.phase = self.canSubmit ? .ready : .idle
                return
            } catch {
                self.failure = MurmurFailure.from(error)
                self.phase = .error
                return
            }
            guard self.phase == .preparingPhoto, !Task.isCancelled else {
                await self.photoLoader.discard(loaded)
                return
            }
            let old = self.draftPhoto
            self.draftPhoto = loaded
            self.phase = .ready
            await self.photoLoader.discard(old)
        }
    }

    func removeDraftPhoto() {
        photoTask?.cancel()
        let old = draftPhoto
        draftPhoto = nil
        if phase == .ready || phase == .error || phase == .preparingPhoto {
            phase = draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .idle : .ready
        }
        Task { await photoLoader.discard(old) }
    }

    func submit() {
        guard canSubmit else { return }
        let note = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        let submission = Submission(
            note: note.isEmpty ? nil : note,
            photo: draftPhoto,
            idempotencyKey: UUID().uuidString.lowercased(),
            replyToProactiveMomentID: note.isEmpty ? nil : proactiveMomentID
        )
        begin(submission: submission, replacingCurrent: true)
    }

    func retry() {
        guard phase == .error, failure?.retryable == true, let lastSubmission else { return }
        bubbles = []
        move = nil
        scene = nil
        currentMomentID = nil
        begin(submission: lastSubmission, replacingCurrent: false)
    }

    func cancelCurrentOperation() {
        photoTask?.cancel()
        let cancelledTask = operationTask
        let original = lastSubmission?.photo
        let abandonedDraft = phase == .preparingPhoto ? draftPhoto : nil
        if phase == .preparingPhoto {
            draftPhoto = nil
        }
        cancelledTask?.cancel()
        operationTask = nil
        lastSubmission = nil
        failure = nil
        phase = currentMomentID == nil && currentNote.isEmpty && currentPhoto == nil ? .idle : .ready
        Task { [photoLoader] in
            await photoLoader.discard(original)
            await photoLoader.discard(abandonedDraft)
        }
    }

    func clearCurrent() {
        photoTask?.cancel()
        operationTask?.cancel()
        let oldDraft = draftPhoto
        let oldCurrent = currentPhoto
        draftText = ""
        draftPhoto = nil
        currentPhoto = nil
        currentNote = ""
        bubbles = []
        move = nil
        scene = nil
        currentMomentID = nil
        proactiveMomentID = nil
        failure = nil
        lastSubmission = nil
        phase = .idle
        Task {
            await photoLoader.discard(oldDraft)
            await photoLoader.discard(oldCurrent)
        }
    }

    func handleBackground() {
        // Deliberately keep the in-memory current moment alive while the process is running.
        // Nothing is persisted, so a true cold launch still begins empty.
    }

    func handleNotification(momentID: String) async {
        await refreshProactive(expectedMomentID: momentID)
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

    private func begin(submission: Submission, replacingCurrent: Bool) {
        guard !phase.isBusy else { return }
        operationTask?.cancel()
        failure = nil
        if replacingCurrent {
            let oldCurrent = currentPhoto
            currentPhoto = submission.photo
            currentNote = submission.note ?? ""
            currentMomentID = nil
            proactiveMomentID = nil
            bubbles = []
            move = nil
            scene = nil
            draftText = ""
            draftPhoto = nil
            if oldCurrent?.id != currentPhoto?.id {
                Task { await photoLoader.discard(oldCurrent) }
            }
        }
        lastSubmission = submission
        phase = .uploading
        operationTask = Task { [weak self] in
            await self?.run(submission)
        }
    }

    private func run(_ submission: Submission) async {
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
                    idempotencyKey: submission.idempotencyKey
                )
            }
            try Task.checkCancellation()
            currentMomentID = receipt.momentID
            connection = .connected
            phase = .responding

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
                        case let .bubble(_, text):
                            if !text.isEmpty {
                                bubbles.append(.init(id: eventID ?? UUID().uuidString, text: text))
                            }
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
            await photoLoader.discard(submission.photo)
            if !bubbles.isEmpty && !didRequestNotificationPrompt {
                didRequestNotificationPrompt = true
                notificationPromptRequested = true
            }
        } catch is CancellationError {
            return
        } catch {
            failure = MurmurFailure.from(error)
            requiresDeviceReconnect = failure?.requiresDeviceReconnect == true
            if failure?.retryable == false {
                await photoLoader.discard(submission.photo)
                lastSubmission = nil
            }
            if failure?.requiresDeviceReconnect == true {
                connection = .offline(failure?.message ?? "连接异常")
            }
            phase = .error
        }
    }

    private func refreshProactive(expectedMomentID: String?) async {
        do {
            guard let proactive = try await api.currentProactive() else { return }
            if let expectedMomentID, proactive.momentID != expectedMomentID { return }
            operationTask?.cancel()
            let oldCurrent = currentPhoto
            currentPhoto = nil
            currentNote = ""
            currentMomentID = proactive.momentID
            proactiveMomentID = proactive.momentID
            bubbles = proactive.resolvedBubbles.enumerated().map {
                .init(id: "\(proactive.momentID)-\($0.offset)", text: $0.element)
            }
            move = proactive.move
            scene = proactive.scene
            phase = bubbles.isEmpty ? .quiet : .complete
            connection = .connected
            await photoLoader.discard(oldCurrent)
            try await api.acknowledge(momentID: proactive.momentID, reply: nil)
        } catch {
            if expectedMomentID != nil {
                failure = MurmurFailure.from(error)
                if failure?.requiresDeviceReconnect == true {
                    requiresDeviceReconnect = true
                    connection = .offline(failure?.message ?? "设备验证失效")
                }
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
    let note: String?
    let photo: PhotoAttachment?
    let idempotencyKey: String
    let replyToProactiveMomentID: String?
}

private extension MurmurStreamEvent {
    var eventID: String? {
        switch self {
        case let .accepted(id), let .bubble(id, _), let .quiet(id), let .done(id, _, _), let .failure(id, _): id
        }
    }
}

private func withTimeout<T: Sendable>(
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
