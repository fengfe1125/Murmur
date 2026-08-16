import SwiftUI
@preconcurrency import UserNotifications

@main
@MainActor
struct MurmurApp: App {
    @UIApplicationDelegateAdaptor(MurmurAppDelegate.self) private var appDelegate
    @StateObject private var session: MurmurSessionModel
    @StateObject private var notifications: MurmurNotificationBridge

    init() {
        _session = StateObject(wrappedValue: MurmurSessionModel(
            api: MurmurEnvironment.makeAPIClient(),
            transcriptStore: MurmurEnvironment.makeTranscriptStore()
        ))
        _notifications = StateObject(wrappedValue: .shared)
    }

    var body: some Scene {
        WindowGroup {
            MurmurRootView(session: session)
                .environmentObject(notifications)
        }
    }
}

@MainActor
private struct MurmurRootView: View {
    @ObservedObject var session: MurmurSessionModel
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @AppStorage("murmur.notification-education-shown") private var didShowNotificationEducation = false
    @State private var showNotificationEducation = false

    var body: some View {
        MurmurChatView(model: session)
            .task {
                await session.bootstrap()
                await notifications.refreshAuthorizationStatus()
                await syncPushRegistration()
                if let momentID = notifications.takePendingMomentID() {
                    await session.handleNotification(momentID: momentID)
                }
            }
            .onChange(of: notifications.apnsToken, initial: false) { _, _ in
                Task { await syncPushRegistration() }
            }
            .onChange(of: notifications.authorization, initial: false) { _, _ in
                Task { await syncPushRegistration() }
            }
            .onChange(of: notifications.pendingMomentID, initial: false) { _, _ in
                guard let momentID = notifications.takePendingMomentID() else { return }
                Task { await session.handleNotification(momentID: momentID) }
            }
            .onChange(of: session.notificationPromptRequested, initial: false) { _, requested in
                guard requested else { return }
                if notifications.authorization == .notDetermined && !didShowNotificationEducation {
                    showNotificationEducation = true
                } else {
                    session.consumeNotificationPromptRequest()
                }
            }
            .alert("允许 Murmur 偶尔送来一条此刻？", isPresented: $showNotificationEducation) {
                Button("以后再说", role: .cancel) {
                    didShowNotificationEducation = true
                    session.consumeNotificationPromptRequest()
                }
                Button("允许") {
                    didShowNotificationEducation = true
                    Task {
                        await notifications.requestAuthorizationIfNeeded()
                        session.consumeNotificationPromptRequest()
                    }
                }
            } message: {
                Text("每天最多 \(session.preferences.dailyFrequency) 条，只在你设定的时间里出现，也可以随时在设置中关闭。")
            }
    }

    private func syncPushRegistration() async {
        guard MurmurNotificationBridge.shouldSyncToken(
            notifications.apnsToken,
            authorization: notifications.authorization
        ) else { return }
        let token = MurmurNotificationBridge.serverToken(
            notifications.apnsToken,
            authorization: notifications.authorization
        )
        await session.updatePushRegistration(token: token)
    }
}

@MainActor
final class MurmurNotificationBridge: ObservableObject {
    static let shared = MurmurNotificationBridge()

    enum Authorization: String {
        case unknown
        case notDetermined
        case allowed
        case denied
    }

    @Published private(set) var authorization: Authorization = .unknown
    @Published private(set) var apnsToken: String?
    @Published private(set) var pendingMomentID: String?

    func receiveAPNSToken(_ token: Data) {
        apnsToken = token.map { String(format: "%02x", $0) }.joined()
    }

    func receiveAPNSRegistrationFailure() {
        apnsToken = nil
    }

    func receive(momentID: String) {
        pendingMomentID = momentID
    }

    nonisolated static func serverToken(_ token: String?, authorization: Authorization) -> String? {
        authorization == .allowed ? token : nil
    }

    nonisolated static func shouldSyncToken(_ token: String?, authorization: Authorization) -> Bool {
        if authorization == .unknown { return false }
        if authorization == .allowed && token == nil { return false }
        return true
    }

    func takePendingMomentID() -> String? {
        defer { pendingMomentID = nil }
        return pendingMomentID
    }

    func refreshAuthorizationStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        authorization = switch settings.authorizationStatus {
        case .notDetermined: .notDetermined
        case .denied: .denied
        case .authorized, .provisional, .ephemeral: .allowed
        @unknown default: .unknown
        }
    }

    func requestAuthorizationIfNeeded() async {
        await refreshAuthorizationStatus()
        guard authorization == .notDetermined else { return }
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
        UIApplication.shared.registerForRemoteNotifications()
        await refreshAuthorizationStatus()
    }

    /// APNs answers through the app delegate whenever it feels like it, so a
    /// caller that wants to register the token right now has to wait for it
    /// rather than read a `nil` and conclude push is broken.
    func tokenAfterRegistering(timeout: Duration = .seconds(10)) async -> String? {
        if let apnsToken { return apnsToken }
        UIApplication.shared.registerForRemoteNotifications()
        let deadline = ContinuousClock.now.advanced(by: timeout)
        while apnsToken == nil, ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(120))
        }
        return apnsToken
    }
}

@MainActor
final class MurmurAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        application.registerForRemoteNotifications()
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        MurmurNotificationBridge.shared.receiveAPNSToken(deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        MurmurNotificationBridge.shared.receiveAPNSRegistrationFailure()
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        guard let momentID = response.notification.request.content.userInfo["moment_id"] as? String else { return }
        await MainActor.run { MurmurNotificationBridge.shared.receive(momentID: momentID) }
    }
}
