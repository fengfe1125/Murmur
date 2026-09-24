import SwiftUI
import UIKit

/// `yyyy-MM-dd` keys for local calendar days, shared by the chat calendar and
/// 当年今日.  They follow the device's current time zone, the same zone the
/// transcript's day index is grouped in.
enum MurmurDay {
    /// One shared formatter rather than a new one for every calendar cell.
    private static let formatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .autoupdatingCurrent
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    static func key(_ date: Date) -> String {
        formatter.string(from: date)
    }

    static func date(_ key: String) -> Date? {
        formatter.date(from: key)
    }
}

/// The chat's date entry, its review entry when reviews are offered, and its
/// local-storage failure line.
struct MurmurChatNavigation: ViewModifier {
    @ObservedObject var session: MurmurSessionModel
    @ObservedObject var reviews: MurmurReviewModel
    @State private var showsCalendar = false
    @State private var reviewDay: String?

    func body(content: Content) -> some View {
        content
            .safeAreaInset(edge: .top, spacing: 0) {
                if let failure = session.storageFailure {
                    Button { session.retryTranscriptSave() } label: {
                        Label(failure, systemImage: "exclamationmark.circle")
                            .font(.caption)
                    }
                    .padding(.horizontal)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("查看日期日历", systemImage: "calendar") { showsCalendar = true }
                        .labelStyle(.iconOnly)
                        .accessibilityValue(session.readingDate.formatted(.dateTime.year().month().day()))
                        .accessibilityIdentifier("chat-calendar")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    MurmurReviewEntryButton(reviews: reviews) { reviewDay = $0 }
                }
            }
            .sheet(isPresented: $showsCalendar) {
                MurmurChatCalendar(session: session, reviews: reviews) { day in
                    showsCalendar = false
                    reviewDay = day
                }
            }
            .sheet(item: Binding(
                get: { reviewDay.map(MurmurReviewSelection.init) },
                set: { reviewDay = $0?.id }
            )) { selected in
                NavigationStack {
                    MurmurReviewDetailView(reviews: reviews, day: selected.id) { context in
                        session.questionContext = context
                        session.returnToLatest()
                        reviewDay = nil
                    }
                }
            }
    }
}

struct MurmurChatCalendar: View {
    @ObservedObject var session: MurmurSessionModel
    @ObservedObject var reviews: MurmurReviewModel
    let openReview: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var selected: Date?

    private var chatDays: Set<String> {
        Set(session.transcriptDays.map(\.id))
    }

    private var reviewDays: Set<String> {
        Set(reviews.dates.map(\.day))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    MurmurNativeCalendar(
                        selected: $selected,
                        markedDays: chatDays,
                        markLabel: "有聊天",
                        secondaryDays: reviewDays,
                        secondaryLabel: "有回顾"
                    )
                    .frame(maxWidth: 420)
                    .clipped()
                    Text(reviews.available ? "• 聊天记录　◇ 每日回顾" : "• 聊天记录")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if let selected {
                        let key = MurmurDay.key(selected)
                        Divider()
                        Text(selected, format: .dateTime.month().day())
                            .font(MurmurTheme.display(.title3))
                        if chatDays.contains(key) {
                            Button("查看当天第一条消息") {
                                Task { if await session.openChatDate(selected) { dismiss() } }
                            }
                            .reviewAction()
                            .accessibilityIdentifier("chat-jump-date")
                        } else {
                            Text(reviewDays.contains(key) ? "本机原记录不可用，仍可查看回顾。" : "暂无记录")
                                .foregroundStyle(MurmurTheme.secondaryInk)
                        }
                        if reviewDays.contains(key) {
                            Button("打开每日回顾") { openReview(key) }
                                .reviewAction()
                        }
                    }
                    if reviews.hasOlder {
                        Button("载入更早的回顾日期") { Task { await reviews.refreshDates(older: true) } }
                            .reviewAction()
                    }
                }
                .padding(20)
                .frame(maxWidth: 700)
            }
            .background(MurmurTheme.paper)
            .navigationTitle("按日期查看")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("完成") { dismiss() }
                }
            }
        }
        .tint(MurmurTheme.accentInk)
    }
}

/// UIKit's native calendar supplies month navigation, selection and accessibility.
struct MurmurNativeCalendar: UIViewRepresentable {
    @Binding var selected: Date?
    /// Days that carry a record, as `MurmurDay` keys.
    var markedDays: Set<String>
    /// What VoiceOver reads for a marked day.
    var markLabel: String
    /// A second kind of record, drawn as ◇ beside the first.
    var secondaryDays: Set<String> = []
    var secondaryLabel: String = ""
    /// When set, only these days can be selected.
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
        if old.markedDays != markedDays || old.secondaryDays != secondaryDays {
            let days = old.markedDays.union(old.secondaryDays).union(markedDays).union(secondaryDays)
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
            withHorizontalFittingPriority: .required,
            verticalFittingPriority: .fittingSizeLevel
        )
    }

    final class Coordinator: NSObject, UICalendarViewDelegate, UICalendarSelectionSingleDateDelegate {
        var parent: MurmurNativeCalendar
        var calendar = Calendar(identifier: .gregorian)

        init(_ parent: MurmurNativeCalendar) {
            self.parent = parent
        }

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
            let marked = parent.markedDays.contains(key)
            let secondary = parent.secondaryDays.contains(key)
            guard marked || secondary else { return nil }
            let text = [marked ? "•" : nil, secondary ? "◇" : nil].compactMap { $0 }.joined(separator: " ")
            let spoken = [marked ? parent.markLabel : nil, secondary ? parent.secondaryLabel : nil]
                .compactMap { $0 }
                .joined(separator: "，")
            return .customView {
                let label = UILabel()
                label.text = text
                label.font = .preferredFont(forTextStyle: .caption2)
                label.textColor = .label
                label.accessibilityLabel = spoken
                return label
            }
        }
    }
}
