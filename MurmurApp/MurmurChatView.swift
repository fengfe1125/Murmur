// Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4
// Hallmark · native app · genre: editorial · macrostructure: Workbench · theme: Garden · chrome: N9 · status: Ft2
import PhotosUI
import SwiftUI
import UIKit

@MainActor
enum MurmurTheme {
    static let paper = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.08, green: 0.08, blue: 0.07, alpha: 1)
            : UIColor(red: 0.96, green: 0.95, blue: 0.91, alpha: 1)
    })
    static let raisedPaper = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.12, green: 0.12, blue: 0.10, alpha: 1)
            : UIColor(red: 0.99, green: 0.98, blue: 0.95, alpha: 1)
    })
    static let ink = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.92, green: 0.91, blue: 0.86, alpha: 1)
            : UIColor(red: 0.12, green: 0.12, blue: 0.10, alpha: 1)
    })
    static let secondaryInk = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.62, green: 0.62, blue: 0.56, alpha: 1)
            : UIColor(red: 0.38, green: 0.38, blue: 0.33, alpha: 1)
    })
    static let rule = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.24, green: 0.24, blue: 0.20, alpha: 1)
            : UIColor(red: 0.81, green: 0.80, blue: 0.73, alpha: 1)
    })
    static let olive = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.66, green: 0.70, blue: 0.47, alpha: 1)
            : UIColor(red: 0.32, green: 0.37, blue: 0.18, alpha: 1)
    })
    static let coral = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.93, green: 0.53, blue: 0.43, alpha: 1)
            : UIColor(red: 0.76, green: 0.27, blue: 0.20, alpha: 1)
    })

    static let pageInset: CGFloat = 20
    static let contentWidth: CGFloat = 1_080
    static let corner: CGFloat = 18

    static func display(_ style: Font.TextStyle) -> Font {
        .system(style, design: .serif, weight: .semibold)
    }

    static func body(_ style: Font.TextStyle = .body, weight: Font.Weight = .regular) -> Font {
        .system(style, design: .default, weight: weight)
    }
}

struct MurmurChatView: View {
    @ObservedObject var model: MurmurSessionModel
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @State private var showSettings = false
    @State private var showCamera = false

    var body: some View {
        NavigationStack {
            Group {
                if model.connection == .checking {
                    ConnectionLoadingView()
                } else if model.identity == nil {
                    EnrollmentView(model: model)
                } else if model.requiresDeviceReconnect {
                    DeviceReconnectView(model: model)
                } else {
                    MomentWorkbench(model: model, showCamera: $showCamera)
                }
            }
            .background(MurmurTheme.paper.ignoresSafeArea())
            .toolbarBackground(MurmurTheme.paper, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    // Just the mark: a bare toolbar item lets the system glass
                    // background stay a circle concentric with it.  An adjacent
                    // Text here collapses to zero width and leaves the HStack's
                    // trailing spacing behind, pushing the mark off-centre.
                    MurmurMark(size: 32)
                }
                if model.identity != nil {
                    ToolbarItem(placement: .topBarTrailing) {
                        HStack(spacing: 8) {
                            Text(model.connection.label)
                                .font(MurmurTheme.body(.caption2, weight: .medium))
                                .foregroundStyle(connectionCaptionColor)
                            Button {
                                showSettings = true
                            } label: {
                                Image(systemName: "gearshape")
                                    .font(.system(size: 17, weight: .regular))
                                    .frame(width: 44, height: 44)
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(MurmurPressStyle())
                            .foregroundStyle(MurmurTheme.ink)
                            .accessibilityLabel("设置，\(model.connection.label)")
                            .accessibilityIdentifier("settings-button")
                        }
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                MurmurSettingsView(model: model)
                    .environmentObject(notifications)
            }
            .sheet(isPresented: $showCamera) {
                CameraPicker(
                    onCaptureFile: { model.preparePhoto(at: $0) },
                    onCaptureImage: { model.prepareCapturedPhoto($0) }
                )
                .ignoresSafeArea()
            }
        }
        .tint(MurmurTheme.olive)
    }

    private var connectionCaptionColor: Color {
        switch model.connection {
        case .connected, .checking: MurmurTheme.secondaryInk
        case .needsEnrollment, .offline: MurmurTheme.coral
        }
    }
}

private struct DeviceReconnectView: View {
    @ObservedObject var model: MurmurSessionModel
    @State private var confirmReset = false

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "iphone.slash")
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(MurmurTheme.coral)
                .accessibilityHidden(true)
            Text("这台设备需要重新连接。")
                .font(MurmurTheme.display(.largeTitle))
                .foregroundStyle(MurmurTheme.ink)
                .accessibilityAddTraits(.isHeader)
            Text("本机的 App Attest 安全身份已经失效。重置只移除本机绑定，不会删除 Murmur 的账号或记忆。")
                .font(MurmurTheme.body(.body))
                .foregroundStyle(MurmurTheme.secondaryInk)
                .fixedSize(horizontal: false, vertical: true)
            Button("重新连接此设备", role: .destructive) { confirmReset = true }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .frame(minHeight: 44)
                .accessibilityIdentifier("reset-device-identity")
            Text("重置后，请向管理员索取新的设备码。")
                .font(MurmurTheme.body(.footnote))
                .foregroundStyle(MurmurTheme.secondaryInk)
        }
        .frame(maxWidth: 560, alignment: .leading)
        .padding(MurmurTheme.pageInset)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .alert("重置本机安全身份？", isPresented: $confirmReset) {
            Button("取消", role: .cancel) {}
            Button("确认重置", role: .destructive) {
                Task { await model.resetLocalDeviceIdentity() }
            }
        } message: {
            Text("账号与服务端记忆不会被删除；再次连接需要管理员签发的新设备码。")
        }
    }
}

private struct ConnectionLoadingView: View {
    var body: some View {
        VStack(spacing: 18) {
            MurmurMark(size: 58)
            ProgressView()
                .tint(MurmurTheme.olive)
            Text("正在确认这台设备")
                .font(MurmurTheme.body(.subheadline))
                .foregroundStyle(MurmurTheme.secondaryInk)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
    }
}

private struct EnrollmentView: View {
    @ObservedObject var model: MurmurSessionModel
    @State private var inviteCode = ""
    @FocusState private var focused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Spacer(minLength: 42)
                Text("把 Murmur 带到这里。")
                    .font(MurmurTheme.display(.largeTitle))
                    .foregroundStyle(MurmurTheme.ink)
                    .accessibilityAddTraits(.isHeader)
                Text("首次连接使用邀请码；已有账号的新设备使用管理员签发的设备码。设备会通过 App Attest 安全绑定。")
                    .font(MurmurTheme.body(.body))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .fixedSize(horizontal: false, vertical: true)

                VStack(alignment: .leading, spacing: 10) {
                    Text("邀请码或设备码")
                        .font(MurmurTheme.body(.caption, weight: .semibold))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                        .textCase(.uppercase)
                    TextField("输入邀请码或设备码", text: $inviteCode)
                        // Codes are case-sensitive base64url from token_urlsafe;
                        // auto-capitalising silently rewrites them and every
                        // redemption fails with "invalid invite".
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textContentType(.oneTimeCode)
                        .focused($focused)
                        .submitLabel(.continue)
                        .onSubmit(connect)
                        .padding(.horizontal, 16)
                        .frame(minHeight: 52)
                        .background(MurmurTheme.raisedPaper)
                        .overlay {
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(MurmurTheme.rule, lineWidth: 1)
                        }
                }

                if let failure = model.failure {
                    MurmurNotice(message: failure.message, identifier: "enrollment-error")
                }

                Button(action: connect) {
                    HStack {
                        if model.phase.isBusy { ProgressView().tint(MurmurTheme.paper) }
                        Text(model.phase.isBusy ? "正在连接" : "连接这台设备")
                    }
                    .font(MurmurTheme.body(.body, weight: .semibold))
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .foregroundStyle(MurmurTheme.paper)
                    .background(MurmurTheme.ink, in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(MurmurPressStyle())
                .disabled(inviteCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.phase.isBusy)
                .opacity(inviteCode.isEmpty ? 0.55 : 1)
                .accessibilityIdentifier("enroll-button")

                Text("聊天记录只留在这台设备上，删除 App 就一并消失。服务端保存的是私有记忆，不是对话本身。")
                    .font(MurmurTheme.body(.footnote))
                    .foregroundStyle(MurmurTheme.secondaryInk)
                    .padding(.top, 10)
                Spacer(minLength: 40)
            }
            .frame(maxWidth: 520)
            .padding(.horizontal, 28)
            .frame(maxWidth: .infinity)
        }
        .onAppear { focused = true }
    }

    private func connect() {
        guard !model.phase.isBusy else { return }
        Task { await model.enroll(inviteCode: inviteCode) }
    }
}

private struct MomentWorkbench: View {
    @ObservedObject var model: MurmurSessionModel
    @Binding var showCamera: Bool
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    var body: some View {
        MurmurTranscriptView(model: model)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                VStack(spacing: 0) {
                    // The draft photo still needs somewhere to show itself
                    // before it is sent; once sent it lives in the transcript.
                    if model.draftPhoto != nil || model.phase == .preparingPhoto {
                        MomentVisualPanel(model: model)
                            .frame(maxWidth: MurmurTheme.contentWidth)
                            .padding(.horizontal, MurmurTheme.pageInset)
                            .padding(.bottom, 10)
                            .frame(maxWidth: .infinity)
                    }
                    if model.phase == .error, let failure = model.failure {
                        MurmurNotice(
                            message: failure.message,
                            retryTitle: failure.retryable ? "再试一次" : nil,
                            identifier: "moment-error",
                            onRetry: { model.retry() }
                        )
                        .frame(maxWidth: MurmurTheme.contentWidth)
                        .padding(.horizontal, MurmurTheme.pageInset)
                        .padding(.bottom, 10)
                        .frame(maxWidth: .infinity)
                    }
                    MomentComposer(model: model, showCamera: $showCamera)
                }
                .background(MurmurTheme.paper)
            }
            .task { await model.loadTranscript() }
    }
}

private struct MomentVisualPanel: View {
    @ObservedObject var model: MurmurSessionModel

    private var shownPhoto: PhotoAttachment? { model.currentPhoto ?? model.draftPhoto }

    var body: some View {
        ZStack {
            MurmurTheme.raisedPaper
            if let photo = shownPhoto {
                Image(uiImage: photo.preview)
                    .resizable()
                    .scaledToFit()
                    .accessibilityLabel(model.currentPhoto == nil ? "待发送的照片" : "当前照片")
            } else if model.phase == .preparingPhoto {
                VStack(spacing: 12) {
                    ProgressView().tint(MurmurTheme.olive)
                    Text("正在准备照片")
                        .font(MurmurTheme.body(.footnote))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                }
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "viewfinder")
                        .font(.system(size: 32, weight: .light))
                        .foregroundStyle(MurmurTheme.olive)
                    Text("照片会在这里出现")
                        .font(MurmurTheme.body(.footnote))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                }
            }
        }
        .frame(minHeight: 260, idealHeight: 420, maxHeight: 560)
        .clipShape(RoundedRectangle(cornerRadius: MurmurTheme.corner))
        .overlay {
            RoundedRectangle(cornerRadius: MurmurTheme.corner)
                .stroke(MurmurTheme.rule, lineWidth: 1)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("visual-panel")
        .accessibilityLabel("图片预览区域")
        .accessibilitySortPriority(1)
    }
}

private struct MomentComposer: View {
    @ObservedObject var model: MurmurSessionModel
    @Binding var showCamera: Bool
    @State private var selectedItem: PhotosPickerItem?
    @State private var showLibrary = false
    @State private var showPhotoSource = false
    @FocusState private var textFocused: Bool

    var body: some View {
        VStack(spacing: 10) {
            if let photo = model.draftPhoto {
                HStack(spacing: 10) {
                    Image(uiImage: photo.preview)
                        .resizable()
                        .scaledToFill()
                        .frame(width: 48, height: 48)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .accessibilityHidden(true)
                    Text("照片已准备好 · \(ByteCountFormatter.string(fromByteCount: photo.byteCount, countStyle: .file))")
                        .font(MurmurTheme.body(.caption))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                        .lineLimit(1)
                    Spacer()
                    Button("移除照片", systemImage: "xmark") {
                        selectedItem = nil
                        model.removeDraftPhoto()
                    }
                    .labelStyle(.iconOnly)
                    .frame(width: 44, height: 44)
                    .buttonStyle(MurmurPressStyle())
                    .accessibilityLabel("移除待发送照片")
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 12))
                .overlay { RoundedRectangle(cornerRadius: 12).stroke(MurmurTheme.rule, lineWidth: 1) }
            }

            HStack(alignment: .bottom, spacing: 8) {
                Button {
                    showPhotoSource = true
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(width: 48, height: 48)
                        .contentShape(Rectangle())
                }
                .buttonStyle(MurmurPressStyle())
                .foregroundStyle(MurmurTheme.ink)
                .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 12))
                .overlay { RoundedRectangle(cornerRadius: 12).stroke(MurmurTheme.rule, lineWidth: 1) }
                .disabled(model.phase.isBusy)
                .accessibilityLabel("添加照片")
                .confirmationDialog("添加照片", isPresented: $showPhotoSource) {
                    Button("从照片中选择") { showLibrary = true }
                    if UIImagePickerController.isSourceTypeAvailable(.camera) {
                        Button("拍照") { showCamera = true }
                    }
                }
                .photosPicker(isPresented: $showLibrary, selection: $selectedItem, matching: .images)

                HStack(alignment: .bottom, spacing: 6) {
                    TextField("发一张图，或说点什么", text: $model.draftText, axis: .vertical)
                        .font(MurmurTheme.body(.body))
                        .foregroundStyle(MurmurTheme.ink)
                        .lineLimit(1...4)
                        .focused($textFocused)
                        .submitLabel(.send)
                        .onSubmit(submit)
                        .onChange(of: model.draftText, initial: false) { _, newValue in
                            guard newValue.contains("\n") else { return }
                            model.draftText = newValue.replacingOccurrences(of: "\n", with: "")
                            submit()
                        }
                        .disabled(model.phase.isBusy)
                        .padding(.leading, 8)
                        .padding(.vertical, 12)
                        .accessibilityLabel("这一刻的文字")
                        .accessibilityIdentifier("moment-composer")

                    Button(action: submit) {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 16, weight: .bold))
                            .frame(width: 44, height: 44)
                            .foregroundStyle(model.canSubmit ? MurmurTheme.paper : MurmurTheme.secondaryInk)
                            .background(
                                model.canSubmit ? MurmurTheme.ink : MurmurTheme.rule,
                                in: RoundedRectangle(cornerRadius: 10)
                            )
                    }
                    .buttonStyle(MurmurPressStyle())
                    .disabled(!model.canSubmit)
                    .accessibilityLabel("发送这一刻")
                    .accessibilityIdentifier("send-moment")
                }
                .padding(.horizontal, 6)
                .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 12))
                .overlay { RoundedRectangle(cornerRadius: 12).stroke(MurmurTheme.rule, lineWidth: 1) }
            }
        }
        .frame(maxWidth: MurmurTheme.contentWidth)
        .padding(.horizontal, MurmurTheme.pageInset)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity)
        .accessibilitySortPriority(3)
        .onChange(of: selectedItem, initial: false) { _, item in
            guard let item else { return }
            model.beginPhotoSelection()
            Task {
                do {
                    guard let file = try await item.loadTransferable(type: PhotoPickerFile.self) else {
                        selectedItem = nil
                        model.failPhotoSelection()
                        return
                    }
                    selectedItem = nil
                    model.preparePhoto(at: file.url)
                } catch {
                    selectedItem = nil
                    model.failPhotoSelection()
                }
            }
        }
    }

    private func submit() {
        guard model.canSubmit else { return }
        textFocused = false
        selectedItem = nil
        model.submit()
    }
}

private struct MurmurSettingsView: View {
    @ObservedObject var model: MurmurSessionModel
    @EnvironmentObject private var notifications: MurmurNotificationBridge
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDelete = false
    @State private var confirmReconnect = false
    @State private var deviceToRemove: MurmurDevice?

    var body: some View {
        NavigationStack {
            Form {
                Section("连接") {
                    LabeledContent("状态", value: model.connection.label)
                    if model.requiresDeviceReconnect {
                        Text("本机安全身份已失效。重置只会移除本机绑定，不会删除 Murmur 的记忆。")
                            .font(.footnote)
                            .foregroundStyle(MurmurTheme.coral)
                        Button("重新连接此设备", role: .destructive) { confirmReconnect = true }
                    }
                    Text("每个邀请用户最多可绑定 3 台设备。新增设备需要管理员签发设备码。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("设备") {
                    if !model.devicesLoaded {
                        HStack {
                            ProgressView()
                            Text("正在读取设备")
                        }
                        .foregroundStyle(.secondary)
                    } else if model.devices.isEmpty {
                        Text("还没有绑定设备")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(model.devices) { (device: MurmurDevice) in
                            HStack(spacing: 12) {
                                Image(systemName: device.deviceName?.contains("iPad") == true ? "ipad" : "iphone")
                                    .frame(width: 28)
                                    .foregroundStyle(MurmurTheme.olive)
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack(spacing: 6) {
                                        Text(device.deviceName ?? "Apple 设备")
                                        if device.id == model.identity?.deviceID {
                                            Text("当前")
                                                .font(.caption2.weight(.semibold))
                                                .foregroundStyle(MurmurTheme.olive)
                                        }
                                    }
                                    Text(device.pushEnabled ? "推送已连接" : "推送未连接")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button(role: .destructive) { deviceToRemove = device } label: {
                                    Image(systemName: "trash")
                                        .frame(width: 44, height: 44)
                                }
                                .accessibilityLabel("移除 \(device.deviceName ?? "设备")")
                            }
                        }
                    }
                }

                Section("主动消息") {
                    Picker("每天最多", selection: $model.preferences.dailyFrequency) {
                        Text("关闭").tag(0)
                        Text("2 条").tag(2)
                        Text("3 条").tag(3)
                        Text("4 条").tag(4)
                    }
                    DatePicker("安静从", selection: quietStart, displayedComponents: .hourAndMinute)
                    DatePicker("安静到", selection: quietEnd, displayedComponents: .hourAndMinute)
                    Button("保存频率与时段") { Task { await model.savePreferences() } }
                }

                Section("通知") {
                    Toggle("允许通知", isOn: notificationsAllowed)
                        .accessibilityIdentifier("notification-toggle")
                    LabeledContent("系统通知", value: notificationLabel)
                    if notifications.authorization == .denied {
                        Button("前往系统设置") { openSystemSettings() }
                    }
                }

                Section("当前界面") {
                    Button("清空这一刻") { model.clearCurrent() }
                        .disabled(!model.hasCurrentMoment && model.draftPhoto == nil && model.draftText.isEmpty)
                    Text("App 不会保存聊天列表；冷启动时始终从空白开始。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Button("删除账号与全部记忆", role: .destructive) { confirmDelete = true }
                } footer: {
                    Text("删除会移除设备身份、服务端记忆、dossier、照片预览和待处理任务，无法撤销。")
                }

                if let message = model.settingsMessage {
                    Section { Text(message).foregroundStyle(.secondary) }
                }
            }
            .scrollContentBackground(.hidden)
            .background(MurmurTheme.paper)
            .task {
                await model.loadPreferences()
                await model.refreshDevices()
            }
            .navigationTitle("设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } }
            }
            .alert("删除账号与全部记忆？", isPresented: $confirmDelete) {
                Button("取消", role: .cancel) {}
                Button("确认删除", role: .destructive) {
                    Task {
                        await model.deleteAccount()
                        if model.identity == nil { dismiss() }
                    }
                }
            } message: {
                Text("这个操作无法撤销。")
            }
            .alert("重置本机安全身份？", isPresented: $confirmReconnect) {
                Button("取消", role: .cancel) {}
                Button("确认重置", role: .destructive) {
                    Task {
                        await model.resetLocalDeviceIdentity()
                        if model.identity == nil { dismiss() }
                    }
                }
            } message: {
                Text("重置后需要管理员签发新的设备码才能再次连接；账号与服务端记忆不会被删除。")
            }
            .alert(item: $deviceToRemove) { device in
                let isCurrent = device.id == model.identity?.deviceID
                return Alert(
                    title: Text(isCurrent ? "移除当前设备？" : "移除这台设备？"),
                    message: Text(isCurrent
                        ? "当前设备会立即退出，需要管理员签发新的设备码才能再次连接。服务端记忆不会因此删除。"
                        : "这台设备之后不能再连接 Murmur。"),
                    primaryButton: .destructive(Text("确认移除")) {
                        Task {
                            await model.removeDevice(device)
                            if isCurrent && model.identity == nil { dismiss() }
                        }
                    },
                    secondaryButton: .cancel()
                )
            }
        }
    }

    private var quietStart: Binding<Date> { timeBinding(\.quietStart) }
    private var quietEnd: Binding<Date> { timeBinding(\.quietEnd) }

    private func timeBinding(_ keyPath: WritableKeyPath<MurmurPreferences, String>) -> Binding<Date> {
        Binding(
            get: { Self.date(from: model.preferences[keyPath: keyPath]) },
            set: { model.preferences[keyPath: keyPath] = Self.time(from: $0) }
        )
    }

    private static func date(from value: String) -> Date {
        let parts = value.split(separator: ":").compactMap { Int($0) }
        return Calendar.current.date(bySettingHour: parts.first ?? 8, minute: parts.dropFirst().first ?? 30, second: 0, of: .now) ?? .now
    }

    private static func time(from date: Date) -> String {
        let components = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", components.hour ?? 0, components.minute ?? 0)
    }

    private var notificationLabel: String {
        switch notifications.authorization {
        case .allowed: "已允许"
        case .denied: "已关闭"
        case .notDetermined: "尚未询问"
        case .unknown: "未知"
        }
    }

    private var notificationsAllowed: Binding<Bool> {
        Binding(
            get: { notifications.authorization == .allowed },
            set: { enabled in
                Task {
                    if enabled {
                        if notifications.authorization == .notDetermined {
                            await notifications.requestAuthorizationIfNeeded()
                        } else if notifications.authorization != .allowed {
                            openSystemSettings()
                        }
                    } else if notifications.authorization == .allowed {
                        openSystemSettings()
                    }
                }
            }
        )
    }

    private func openSystemSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}

private struct MurmurNotice: View {
    let message: String
    var retryTitle: String? = nil
    var identifier: String
    var onRetry: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Rectangle()
                .fill(MurmurTheme.coral)
                .frame(width: 2)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 10) {
                Text(message)
                    .font(MurmurTheme.body(.body))
                    .foregroundStyle(MurmurTheme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier(identifier)
                if let retryTitle, let onRetry {
                    Button(retryTitle, action: onRetry)
                        .font(MurmurTheme.body(.subheadline, weight: .semibold))
                        .foregroundStyle(MurmurTheme.olive)
                        .frame(minHeight: 44)
                        .buttonStyle(MurmurPressStyle())
                }
            }
        }
        .accessibilityElement(children: .contain)
    }
}

private struct MurmurMark: View {
    let size: CGFloat

    var body: some View {
        Image("MurmurMark")
            .resizable()
            .scaledToFill()
            .frame(width: size, height: size, alignment: .top)
            .clipShape(Circle())
            .overlay {
                Circle().strokeBorder(MurmurTheme.rule, lineWidth: 0.5)
            }
            .accessibilityLabel("Murmur")
    }
}

private struct MurmurPressStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.72 : 1)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
            .animation(.easeOut(duration: reduceMotion ? 0.1 : 0.14), value: configuration.isPressed)
    }
}

private struct CameraPicker: UIViewControllerRepresentable {
    let onCaptureFile: (URL) -> Void
    let onCaptureImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.cameraCaptureMode = .photo
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
        let parent: CameraPicker
        init(parent: CameraPicker) { self.parent = parent }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.dismiss()
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            if let url = info[.imageURL] as? URL {
                parent.onCaptureFile(url)
            } else if let image = info[.originalImage] as? UIImage {
                parent.onCaptureImage(image)
            }
            parent.dismiss()
        }
    }
}

#if DEBUG
#Preview {
    MurmurChatView(model: MurmurSessionModel(api: PreviewMurmurAPIClient()))
        .environmentObject(MurmurNotificationBridge.shared)
}

private actor PreviewMurmurAPIClient: MurmurAPIClient {
    func storedIdentity() async throws -> MurmurIdentity? { .init(userID: "preview", deviceID: "preview", keyID: "preview") }
    func enroll(inviteCode: String, deviceName: String) async throws -> MurmurIdentity { .init(userID: "preview", deviceID: "preview", keyID: "preview") }
    func createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String) async throws -> MomentReceipt { .init(momentID: "preview", status: "queued") }
    func events(momentID: String, lastEventID: String?) async -> AsyncThrowingStream<MurmurStreamEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func currentProactive() async throws -> ProactiveMoment? { nil }
    func acknowledge(momentID: String, reply: String?) async throws {}
    func updateDevice(apnsToken: String?, environment: String, timezone: String, deviceName: String) async throws {}
    func devices() async throws -> [MurmurDevice] { [] }
    func removeDevice(deviceID: String) async throws {}
    func preferences() async throws -> MurmurPreferences { MurmurPreferences() }
    func updatePreferences(_ preferences: MurmurPreferences) async throws {}
    func resetLocalIdentity() async throws {}
    func deleteAccount() async throws {}
}
#endif
