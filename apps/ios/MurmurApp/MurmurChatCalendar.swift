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

/// The chat's date entry and its local-storage failure line.
struct MurmurChatNavigation: ViewModifier {
    @ObservedObject var session: MurmurSessionModel
    @State private var showsCalendar = false

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
            }
            .sheet(isPresented: $showsCalendar) {
                MurmurChatCalendar(session: session)
            }
    }
}

struct MurmurChatCalendar: View {
    @ObservedObject var session: MurmurSessionModel
    @Environment(\.dismiss) private var dismiss
    @State private var selected: Date?

    private var chatDays: Set<String> {
        Set(session.transcriptDays.map(\.id))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    MurmurNativeCalendar(
                        selected: $selected,
                        markedDays: chatDays,
                        markLabel: "有聊天"
                    )
                    .frame(maxWidth: 420)
                    .clipped()
                    Text("• 聊天记录")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if let selected {
                        Divider()
                        Text(selected, format: .dateTime.month().day())
                            .font(MurmurTheme.display(.title3))
                        if chatDays.contains(MurmurDay.key(selected)) {
                            Button("查看当天第一条消息") {
                                Task { if await session.openChatDate(selected) { dismiss() } }
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.large)
                            .accessibilityIdentifier("chat-jump-date")
                        } else {
                            Text("暂无记录")
                                .foregroundStyle(MurmurTheme.secondaryInk)
                        }
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
        if old.markedDays != markedDays {
            let days = old.markedDays.union(markedDays)
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
            guard let date = calendarView.calendar.date(from: components),
                  parent.markedDays.contains(MurmurDay.key(date))
            else { return nil }
            let markLabel = parent.markLabel
            return .customView {
                let label = UILabel()
                label.text = "•"
                label.font = .preferredFont(forTextStyle: .caption2)
                label.textColor = .label
                label.accessibilityLabel = markLabel
                return label
            }
        }
    }
}
