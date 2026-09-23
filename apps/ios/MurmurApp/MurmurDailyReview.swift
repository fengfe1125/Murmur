import CryptoKit
import Foundation
import SwiftUI
import UIKit

struct MurmurReviewRequest: Sendable {
    enum Operation: Sendable { case configuration, preferences, dates, detail, refresh, edit, forget }
    let operation: Operation
    var key: String = ""
    var body: Data = Data()
    var path: String {
        let escaped =
            key.addingPercentEncoding(
                withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-_"))) ?? ""
        switch operation {
        case .configuration: return "/v1/reviews/config"
        case .preferences: return "/v1/reviews/preferences"
        case .dates: return "/v1/reviews" + (key.isEmpty ? "" : "?before=\(escaped)")
        case .detail: return "/v1/reviews/\(escaped)"
        case .refresh: return "/v1/reviews/\(escaped)/refresh"
        case .edit, .forget: return "/v1/review-memories/\(escaped)"
        }
    }
    var method: String {
        switch operation {
        case .preferences, .edit: "PATCH"
        case .refresh: "POST"
        case .forget: "DELETE"
        default: "GET"
        }
    }
}

struct MurmurReviewPreferences: Codable, Sendable {
    var enabled: Bool
    var localTime: String
    var firstDay: String
}
struct MurmurReviewConfiguration: Codable, Sendable {
    var available: Bool
    var preferences: MurmurReviewPreferences?
}
struct MurmurReviewDate: Codable, Identifiable, Sendable {
    var day: String
    var status: String
    var version: Int
    var updatedAt: String?
    var id: String { day }
}
struct MurmurReviewDatePage: Codable, Sendable {
    var dates: [MurmurReviewDate]
    var nextBefore: String?
}
struct MurmurReviewSource: Codable, Identifiable, Sendable {
    var id: String
    var occurredAt: String
    var channel: String
    var userText: String
    var observation: String
    var shotAt: String?
    var channelLabel: String { channel == "photo_room" ? "当年今日中的交流" : "首页聊天" }
}
struct MurmurMemoryItem: Codable, Identifiable, Sendable {
    var id: String
    var text: String
    var evidence: String
    var sourceIds: [String]
    var edited: Bool
    var updatedAt: String
    var evidenceLabel: String {
        if edited { return "用户已纠正" }
        switch evidence {
        case "user_stated": return "用户说过"
        case "image_observed": return "画面可见"
        default: return "尚不确定"
        }
    }
}
struct MurmurDailyReview: Codable, Identifiable, Sendable {
    var day: String
    var timezone: String
    var status: String
    var summary: String
    var question: String
    var version: Int
    var updatedAt: String?
    var error: String?
    var memories: [MurmurMemoryItem]
    var sources: [MurmurReviewSource]
    var id: String { day }
}
struct MurmurQuestionContext: Codable, Equatable, Sendable {
    var day: String
    var question: String
}

enum MurmurDay {
    static func key(_ date: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
    static func date(_ key: String) -> Date? {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: key)
    }
    static func timestamp(_ raw: String?) -> String {
        guard let raw else { return "" }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = f.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        return date?.formatted(date: .abbreviated, time: .shortened) ?? raw
    }
}

/// Only one selected detail is loaded in memory. Disk keeps a per-day offline
/// cache under an account-specific directory; it never contains original photos.
@MainActor final class MurmurReviewModel: ObservableObject {
    @Published private(set) var available = false
    @Published private(set) var preferences: MurmurReviewPreferences?
    @Published private(set) var dates: [MurmurReviewDate] = []
    @Published private(set) var detail: MurmurDailyReview?
    @Published private(set) var failure: String?
    @Published private(set) var loading = false
    @Published private(set) var readVersions: [String: Int] = [:]
    private let api: any MurmurAPIClient
    private var userID: String?
    private var cacheDirectory: URL?
    private var nextBefore: String?
    private var detailGeneration = 0
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    init(api: any MurmurAPIClient) { self.api = api }

    private func data(_ request: MurmurReviewRequest) async throws -> Data {
        try await api.dailyReviews(request)
    }
    private func cache<T: Encodable>(_ value: T, name: String) throws {
        guard let directory = cacheDirectory else { return }
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.complete])
        try JSONEncoder().encode(value).write(
            to: directory.appendingPathComponent(name), options: [.atomic, .completeFileProtection])
    }
    private func cached<T: Decodable>(_ type: T.Type, name: String) -> T? {
        guard let directory = cacheDirectory,
            let bytes = try? Data(contentsOf: directory.appendingPathComponent(name))
        else { return nil }
        return try? JSONDecoder().decode(type, from: bytes)
    }
    func activate(user: String) async {
        guard userID != user else {
            await refreshDates()
            return
        }
        detailGeneration += 1
        userID = user
        detail = nil
        dates = []
        failure = nil
        available = false
        preferences = nil
        loading = false
        let hash = SHA256.hash(data: Data(user.utf8)).map { String(format: "%02x", $0) }.joined()
        cacheDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Murmur/reviews/\(hash)")
        if let config = cached(MurmurReviewConfiguration.self, name: "configuration.json") {
            available = config.available
            preferences = config.preferences
        }
        dates = cached([MurmurReviewDate].self, name: "dates.json") ?? []
        readVersions = cached([String: Int].self, name: "read.json") ?? [:]
        do {
            let config = try decoder.decode(
                MurmurReviewConfiguration.self, from: await data(.init(operation: .configuration)))
            guard userID == user else { return }
            available = config.available
            preferences = config.preferences
            if available && preferences == nil { try await updatePreferences(enabled: true, time: "22:30") }
            try cache(
                MurmurReviewConfiguration(available: available, preferences: preferences),
                name: "configuration.json")
            await refreshDates()
        } catch {
            let mapped = MurmurFailure.from(error)
            if mapped.code != "not_found" { failure = "回顾暂时无法更新，已保存的内容仍可离线查看。" }
        }
    }
    func refreshDates(older: Bool = false) async {
        guard available, !loading else { return }
        let user = userID
        do {
            let response = try decoder.decode(
                MurmurReviewDatePage.self,
                from: await data(.init(operation: .dates, key: older ? (nextBefore ?? "") : "")))
            guard userID == user else { return }
            if older {
                var seen = Set(dates.map(\.day))
                dates += response.dates.filter { seen.insert($0.day).inserted }
            } else {
                // Keep previously loaded older pages when refreshing the newest
                // page, so their cached details remain reachable offline.
                if let cutoff = response.nextBefore {
                    dates = response.dates + dates.filter { $0.day < cutoff }
                } else {
                    dates = response.dates
                }
            }
            nextBefore = response.nextBefore
            try cache(dates, name: "dates.json")
            failure = nil
        } catch { failure = "回顾暂时无法更新，已保存的内容仍可离线查看。" }
    }
    func open(day: String) async {
        guard MurmurDay.date(day) != nil else { return }
        detailGeneration += 1
        let generation = detailGeneration
        detail = cached(MurmurDailyReview.self, name: "\(day).json")
        loading = true
        failure = nil
        defer { if generation == detailGeneration { loading = false } }
        let user = userID
        do {
            let review = try decoder.decode(
                MurmurDailyReview.self, from: await data(.init(operation: .detail, key: day)))
            guard user == userID, generation == detailGeneration else { return }
            detail = review
            try cache(review, name: "\(day).json")
            readVersions[day] = review.version
            try cache(readVersions, name: "read.json")
        } catch {
            guard user == userID, generation == detailGeneration else { return }
            if MurmurFailure.from(error).code == "not_found" {
                detail = nil
                failure = nil
            } else {
                failure = detail == nil ? "暂时无法连接，请重试。" : "正在显示离线保存的版本。"
            }
        }
    }
    func refresh(day: String) async {
        do {
            _ = try await data(.init(operation: .refresh, key: day, body: Data("{}".utf8)))
            await open(day: day)
        } catch { failure = "整理请求未成功，请重试。" }
    }
    func updatePreferences(enabled: Bool, time: String) async throws {
        let user = userID
        let body = try JSONSerialization.data(withJSONObject: ["enabled": enabled, "local_time": time])
        let response = try decoder.decode(
            MurmurReviewPreferences.self, from: await data(.init(operation: .preferences, body: body)))
        guard user == userID else { return }
        preferences = response
        try cache(
            MurmurReviewConfiguration(available: available, preferences: preferences),
            name: "configuration.json")
    }
    func edit(_ item: MurmurMemoryItem, text: String, forget: Bool) async throws {
        let day = detail?.day
        let user = userID
        let body = forget ? Data() : try JSONSerialization.data(withJSONObject: ["text": text])
        _ = try await data(.init(operation: forget ? .forget : .edit, key: item.id, body: body))
        guard user == userID else { return }
        detailGeneration += 1
        detail = nil
        // Clear cached derived versions before displaying the operation as done.
        if let directory = cacheDirectory, FileManager.default.fileExists(atPath: directory.path) {
            for url in try FileManager.default.contentsOfDirectory(
                at: directory, includingPropertiesForKeys: nil)
            where MurmurDay.date(url.deletingPathExtension().lastPathComponent) != nil {
                try FileManager.default.removeItem(at: url)
            }
        }
        detail = nil
        if let day { await open(day: day) }
        await refreshDates()
    }
    func reset() throws {
        if let directory = cacheDirectory, FileManager.default.fileExists(atPath: directory.path) {
            try FileManager.default.removeItem(at: directory)
        }
        detailGeneration += 1
        loading = false
        userID = nil
        cacheDirectory = nil
        available = false
        preferences = nil
        dates = []
        detail = nil
        readVersions = [:]
    }
    var hasOlder: Bool { nextBefore != nil }
    func unread(_ date: MurmurReviewDate) -> Bool { date.version > (readVersions[date.day] ?? 0) }
}

struct MurmurReviewNavigation: ViewModifier {
    @ObservedObject var session: MurmurSessionModel
    @ObservedObject var reviews: MurmurReviewModel
    @State private var calendar = false
    @State private var reviewDay: String?
    @Environment(\.scenePhase) private var scenePhase
    func body(content: Content) -> some View {
        content
            .safeAreaInset(edge: .top, spacing: 0) {
                if let failure = session.storageFailure {
                    Button { session.retryTranscriptSave() } label: {
                        Label(failure, systemImage: "exclamationmark.circle").font(.caption)
                    }
                    .padding(.horizontal)
                }
            }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("查看日期日历", systemImage: "calendar") { calendar = true }
                .labelStyle(.iconOnly)
                .accessibilityValue(session.readingDate.formatted(.dateTime.year().month().day()))
                .accessibilityIdentifier("chat-calendar")
            }
            ToolbarItem(placement: .topBarTrailing) {
                if reviews.available {
                    Button { reviewDay = reviews.dates.first?.day ?? MurmurDay.key(Date()) } label: {
                        Label("每日回顾", systemImage: reviews.dates.first.map { reviews.unread($0) } == true ? "text.book.closed.fill" : "text.book.closed").frame(minWidth: 44, minHeight: 44)
                    }
                    .accessibilityHint(entrySubtitle)
                    .accessibilityValue(reviews.dates.first?.day ?? MurmurDay.key(Date()))
                    .accessibilityIdentifier("daily-review-entry")
                }
            }
        }
        .sheet(isPresented: $calendar) {
            MurmurChatCalendar(session: session, reviews: reviews) { day in
                calendar = false
                reviewDay = day
            }
        }
        .sheet(item: Binding(get: { reviewDay.map(ReviewSelection.init) }, set: { reviewDay = $0?.id })) {
            selected in
            NavigationStack {
                MurmurReviewDetailView(reviews: reviews, day: selected.id) { context in
                    session.questionContext = context
                    session.returnToLatest()
                    reviewDay = nil
                }
            }
        }
        .task(id: session.identity?.userID) {
            guard let user = session.identity?.userID else { return }
            await reviews.activate(user: user)
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                if scenePhase == .active { await reviews.refreshDates() }
            }
        }
    }
    private var entrySubtitle: String {
        guard let first = reviews.dates.first else {
            return "有交流的日子于 \(reviews.preferences?.localTime ?? "22:30") 整理"
        }
        switch first.status {
        case "queued", "generating": return "正在整理"
        case "failed": return "暂未生成 · 轻点重试"
        default: return reviews.unread(first) ? "有新内容 · 总结与一个问题" : "查看总结与记忆"
        }
    }
}
private struct ReviewSelection: Identifiable { var id: String }

struct MurmurChatCalendar: View {
    @ObservedObject var session: MurmurSessionModel
    @ObservedObject var reviews: MurmurReviewModel
    let openReview: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var selected: Date?
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    MurmurNativeCalendar(
                        selected: $selected,
                        chatDays: Set(session.transcriptDays.map(\.id)),
                        reviewDays: Set(reviews.dates.map(\.day))
                    )
                    .frame(maxWidth: 420)
                    .clipped()
                    Text("• 聊天记录　◇ 每日回顾").font(.caption).foregroundStyle(.secondary)
                    if let selected {
                        let key = MurmurDay.key(selected)
                        Divider()
                        Text(selected, format: .dateTime.month().day()).font(MurmurTheme.display(.title3))
                        if session.transcriptDays.contains(where: { $0.id == key }) {
                            Button("查看当天第一条消息") {
                                Task { if await session.openChatDate(selected) { dismiss() } }
                            }.reviewAction().accessibilityIdentifier("chat-jump-date")
                        } else {
                            Text(
                                reviews.dates.contains(where: { $0.day == key }) ? "本机原记录不可用，仍可查看回顾。" : "暂无记录"
                            ).foregroundStyle(MurmurTheme.secondaryInk)
                        }
                        if reviews.dates.contains(where: { $0.day == key }) {
                            Button("打开每日回顾") { openReview(key) }.reviewAction()
                        }
                    }
                    if reviews.hasOlder {
                        Button("载入更早的回顾日期") { Task { await reviews.refreshDates(older: true) } }
                            .reviewAction()
                    }
                }.padding(20).frame(maxWidth: 700)
            }.background(MurmurTheme.paper).navigationTitle("按日期查看").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
        }.tint(MurmurTheme.accentInk)
    }
}

/// UIKit's native calendar supplies month navigation, selection and accessibility.
struct MurmurNativeCalendar: UIViewRepresentable {
    @Binding var selected: Date?
    var chatDays: Set<String>
    var reviewDays: Set<String>
    var selectableDays: Set<String>? = nil

    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIView(context: Context) -> UICalendarView {
        let view = UICalendarView()
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        calendar.firstWeekday = 2
        view.calendar = calendar
        view.locale = Locale(identifier: "zh_Hans_CN")
        view.clipsToBounds = true
        context.coordinator.calendar = calendar
        view.delegate = context.coordinator
        view.selectionBehavior = UICalendarSelectionSingleDate(delegate: context.coordinator)
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        view.accessibilityIdentifier = "native-calendar"
        return view
    }
    func updateUIView(_ view: UICalendarView, context: Context) {
        let old = context.coordinator.parent
        context.coordinator.parent = self
        if old.chatDays != chatDays || old.reviewDays != reviewDays {
            let days = old.chatDays.union(old.reviewDays).union(chatDays).union(reviewDays)
            view.reloadDecorations(forDateComponents: days.compactMap { key in
                let parts = key.split(separator: "-").compactMap { Int($0) }
                guard parts.count == 3 else { return nil }
                return DateComponents(year: parts[0], month: parts[1], day: parts[2])
            }, animated: false)
        }
        let components = selected.map { view.calendar.dateComponents([.year, .month, .day], from: $0) }
        if let selection = view.selectionBehavior as? UICalendarSelectionSingleDate,
           selection.selectedDate != components {
            selection.setSelected(components, animated: false)
        }
    }
    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UICalendarView, context: Context) -> CGSize? {
        let width = proposal.width ?? 320
        return uiView.systemLayoutSizeFitting(
            CGSize(width: width, height: UIView.layoutFittingCompressedSize.height),
            withHorizontalFittingPriority: .required, verticalFittingPriority: .fittingSizeLevel
        )
    }
    final class Coordinator: NSObject, UICalendarViewDelegate, UICalendarSelectionSingleDateDelegate {
        var parent: MurmurNativeCalendar
        var calendar = Calendar(identifier: .gregorian)
        init(_ parent: MurmurNativeCalendar) { self.parent = parent }
        func dateSelection(_ selection: UICalendarSelectionSingleDate, didSelectDate components: DateComponents?) {
            parent.selected = components.flatMap { calendar.date(from: $0) }
        }
        func dateSelection(_ selection: UICalendarSelectionSingleDate, canSelectDate components: DateComponents?) -> Bool {
            guard let allowed = parent.selectableDays else { return true }
            guard let components, let date = calendar.date(from: components) else { return false }
            return allowed.contains(MurmurDay.key(date))
        }
        func calendarView(_ calendarView: UICalendarView, decorationFor components: DateComponents) -> UICalendarView.Decoration? {
            guard let date = calendarView.calendar.date(from: components) else { return nil }
            let key = MurmurDay.key(date)
            let chat = parent.chatDays.contains(key)
            let review = parent.reviewDays.contains(key)
            guard chat || review else { return nil }
            return .customView {
                let label = UILabel()
                label.text = chat && review ? "• ◇" : chat ? "•" : "◇"
                label.font = .preferredFont(forTextStyle: .caption2)
                label.textColor = .label
                label.accessibilityLabel = chat && review ? "有聊天，有回顾" : chat ? "有聊天" : "有回顾"
                return label
            }
        }
    }
}

struct MurmurReviewDetailView: View {
    @ObservedObject var reviews: MurmurReviewModel
    let day: String
    let answer: (MurmurQuestionContext) -> Void
    @State private var editing: MurmurMemoryItem?
    @State private var editingSources: [MurmurReviewSource] = []
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("\(day) 的回顾").font(MurmurTheme.display(.title)).accessibilityIdentifier("daily-review-title")
                if let detail = reviews.detail, detail.day == day {
                    Text("更新于 \(MurmurDay.timestamp(detail.updatedAt))\n\(detail.timezone)").font(.caption)
                        .foregroundStyle(MurmurTheme.secondaryInk)
                    if detail.status != "ready" {
                        Label(detail.status == "failed" ? "暂未生成" : "正在整理", systemImage: "clock")
                            .foregroundStyle(MurmurTheme.secondaryInk)
                    }
                    if !detail.summary.isEmpty {
                        section("今天谈到的事")
                        Text(detail.summary).textSelection(.enabled)
                    }
                    if !detail.question.isEmpty {
                        section("一个问题")
                        Text(detail.question).font(.title3)
                        Button("在聊天中回答", systemImage: "arrow.up.right") {
                            answer(.init(day: day, question: detail.question))
                        }.reviewAction().accessibilityIdentifier("answer-daily-question")
                    }
                    if !detail.memories.isEmpty {
                        Divider()
                        section("留下的记忆")
                        ForEach(detail.memories) { item in
                            Button {
                                editingSources = detail.sources.filter { item.sourceIds.contains($0.id) }
                                editing = item
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(item.evidenceLabel).font(.caption).foregroundStyle(
                                        MurmurTheme.secondaryInk)
                                    Text(item.text).foregroundStyle(MurmurTheme.ink)
                                    Text("查看来源与纠正  ›").font(.caption).foregroundStyle(MurmurTheme.accentInk)
                                }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 8).contentShape(Rectangle())
                            }.buttonStyle(.plain)
                        }
                    }
                    DisclosureGroup("来源（\(detail.sources.count)）") {
                        ForEach(detail.sources) { source in sourceView(source) }
                    }
                } else if reviews.loading {
                    ProgressView("正在读取")
                } else {
                    Text(emptyMessage).foregroundStyle(MurmurTheme.secondaryInk)
                }
                if let failure = reviews.failure {
                    Text(failure).font(.footnote).foregroundStyle(MurmurTheme.coral)
                }
                Button("刷新回顾") { Task { await reviews.refresh(day: day) } }.reviewAction().disabled(
                    reviews.loading)
            }.padding(20).frame(maxWidth: 700, alignment: .leading).frame(maxWidth: .infinity)
        }.background(MurmurTheme.paper).foregroundStyle(MurmurTheme.ink).tint(MurmurTheme.accentInk)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
            .navigationDestination(
                isPresented: Binding(get: { editing != nil }, set: { if !$0 { editing = nil } })
            ) {
                if let item = editing {
                    MurmurMemoryEditor(reviews: reviews, item: item, sources: editingSources)
                }
            }
            .task(id: day) { await reviews.open(day: day) }
    }
    private var emptyMessage: String {
        guard let preferences = reviews.preferences else { return "暂无记录" }
        if !preferences.enabled { return "自动整理已关闭。已生成的回顾仍可查看。" }
        let parts = preferences.localTime.split(separator: ":").compactMap { Int($0) }
        if day == MurmurDay.key(Date()), parts.count == 2,
            let due = Calendar.current.date(bySettingHour: parts[0], minute: parts[1], second: 0, of: Date()),
            Date() < due
        {
            return "尚未到整理时间。有交流的日子于 \(preferences.localTime) 生成回顾。"
        }
        return "暂无记录"
    }
    private func section(_ title: String) -> some View {
        Text(title).font(MurmurTheme.display(.headline)).accessibilityAddTraits(.isHeader)
    }
}

@MainActor private func sourceView(_ source: MurmurReviewSource) -> some View {
    VStack(alignment: .leading, spacing: 8) {
        Text("\(source.channelLabel) · \(MurmurDay.timestamp(source.occurredAt))").font(.caption)
            .foregroundStyle(MurmurTheme.secondaryInk)
        if !source.userText.isEmpty { Text("用户说：\(source.userText)") }
        if !source.observation.isEmpty { Text("画面观察：\(source.observation)") }
        if let shotAt = source.shotAt {
            Text("照片拍摄于 \(MurmurDay.timestamp(shotAt))").font(.caption).foregroundStyle(
                MurmurTheme.secondaryInk)
        }
    }.padding(.vertical, 8).textSelection(.enabled)
}

struct MurmurMemoryEditor: View {
    @ObservedObject var reviews: MurmurReviewModel
    let item: MurmurMemoryItem
    let sources: [MurmurReviewSource]
    @State private var text = ""
    @FocusState private var editorFocused: Bool
    @State private var saving = false
    @State private var confirmForget = false
    @State private var result: String?
    @State private var failure: String?
    @State private var forgotten = false
    @State private var retryForget = false
    var body: some View {
        Form {
            Section {
                Text("这条记忆").font(MurmurTheme.display(.title))
                Text(item.evidenceLabel).font(.caption).foregroundStyle(MurmurTheme.secondaryInk)
                if !forgotten {
                    TextField("记忆内容", text: $text, axis: .vertical).lineLimit(3...12).focused($editorFocused).accessibilityIdentifier("memory-editor")
                    Text("依据").font(MurmurTheme.display(.headline))
                    ForEach(sources) { source in sourceView(source) }
                    Button(saving ? "正在保存" : "保存纠正") { save(forget: false) }.reviewAction().disabled(
                        saving || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    ).accessibilityIdentifier("memory-save")
                    Text("你的更改优先，自动整理不会覆盖。").font(.caption).foregroundStyle(MurmurTheme.secondaryInk)
                    Divider()
                    Button("忘记这条", role: .destructive) { confirmForget = true }.reviewAction().disabled(
                        saving
                    ).accessibilityIdentifier("memory-forget")
                    Text("忘记后不再用于 AI 回答。原始聊天仍保留。").font(.footnote).foregroundStyle(MurmurTheme.secondaryInk)
                }
                if let failure {
                    Text(failure).foregroundStyle(MurmurTheme.coral)
                    Button("重试") { save(forget: retryForget) }.reviewAction()
                }
            }
        }.background(MurmurTheme.paper).foregroundStyle(MurmurTheme.ink).tint(MurmurTheme.accentInk)
            .safeAreaInset(edge: .bottom) {
                if let result {
                    Text(result).font(.callout).frame(maxWidth: .infinity)
                        .padding().background(.regularMaterial)
                        .accessibilityIdentifier("memory-result")
                }
            }
            .onAppear { text = item.text }
            .confirmationDialog("忘记这条记忆？", isPresented: $confirmForget, titleVisibility: .visible) {
                Button("忘记这条", role: .destructive) { save(forget: true) }.accessibilityIdentifier(
                    "memory-forget-confirm")
            } message: {
                Text("这条及同一段来源生成的记忆会撤销，原始聊天仍保留。")
            }
    }
    private func save(forget: Bool) {
        editorFocused = false
        saving = true
        failure = nil
        retryForget = forget
        Task {
            do {
                try await reviews.edit(item, text: text, forget: forget)
                forgotten = forget
                result = forget ? "已忘记，后续回答不再引用。" : "已保存纠正。"
            } catch { failure = "操作未完成，编辑内容已保留，请重试。" }
            saving = false
        }
    }
}

struct MurmurReviewSettings: View {
    @ObservedObject var reviews: MurmurReviewModel
    @State private var enabled = true
    @State private var time = Date()
    @State private var failure: String?
    @State private var saving = false
    var body: some View {
        Form {
            Section {
                Text("每日整理").font(MurmurTheme.display(.title))
                Toggle("自动整理", isOn: $enabled)
                DatePicker("当地生成时间", selection: $time, displayedComponents: .hourAndMinute)
                Text("按最近活跃设备的时区整理。\n\(TimeZone.current.identifier)").font(.caption).foregroundStyle(
                    MurmurTheme.secondaryInk)
                Button(saving ? "正在保存" : "保存设置") {
                    saving = true
                    Task {
                        do {
                            let c = Calendar.current.dateComponents([.hour, .minute], from: time)
                            try await reviews.updatePreferences(
                                enabled: enabled,
                                time: String(format: "%02d:%02d", c.hour ?? 22, c.minute ?? 30))
                            failure = "已保存。"
                        } catch { failure = "保存失败，请重试。" }
                        saving = false
                    }
                }.reviewAction().disabled(saving)
                if let failure { Text(failure).font(.footnote) }
                Divider()
                Text("当天新增内容会更新同一份回顾，问题不会自动换题。没有交流的日子不生成内容。")
                Text("只在 App 内显示，不发送系统通知。")
                Text("从启用当天开始，不补写历史。聊天与图片仅在本机长期保存，回顾和记忆会缓存供离线查看。").font(.footnote).foregroundStyle(
                    MurmurTheme.secondaryInk)
            }
        }.background(MurmurTheme.paper).tint(MurmurTheme.accentInk)
            .onAppear {
                enabled = reviews.preferences?.enabled ?? true
                let parts = (reviews.preferences?.localTime ?? "22:30").split(separator: ":").compactMap {
                    Int($0)
                }
                if parts.count == 2 {
                    time =
                        Calendar.current.date(
                            bySettingHour: parts[0], minute: parts[1], second: 0, of: Date()) ?? Date()
                }
            }
    }
}
extension View {
    fileprivate func reviewAction() -> some View {
        buttonStyle(.bordered).controlSize(.large)
    }
}

struct MurmurReviewSettingsEntry: View {
    @ObservedObject var reviews: MurmurReviewModel
    var body: some View {
        if reviews.available {
            Section("回顾与记忆") { NavigationLink("每日整理") { MurmurReviewSettings(reviews: reviews) } }
        }
    }
}
