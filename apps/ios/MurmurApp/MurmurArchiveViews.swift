import SwiftUI

// MARK: - The month

/// One month, with a deepened circle on every day that has a 当年今日 room
/// behind it.  The circle is the affordance: nothing else on the grid is
/// pressable, so a day that carries one is the only thing that answers.
struct MurmurMonthView: View {
    let month: Date
    let markedDays: Set<Date>
    let onSelect: (Date) -> Void

    private let calendar: Calendar
    private let today: Date

    init(
        month: Date,
        markedDays: Set<Date>,
        calendar: Calendar = .murmur,
        today: Date = Date(),
        onSelect: @escaping (Date) -> Void
    ) {
        self.month = month
        self.markedDays = markedDays
        self.calendar = calendar
        self.today = calendar.startOfDay(for: today)
        self.onSelect = onSelect
    }

    var body: some View {
        VStack(spacing: 6) {
            HStack(spacing: 0) {
                ForEach(weekdaySymbols, id: \.self) { symbol in
                    Text(symbol)
                        .font(MurmurTheme.body(.caption2))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                        .frame(maxWidth: .infinity)
                }
            }
            .padding(.bottom, 2)

            ForEach(Array(weeks.enumerated()), id: \.offset) { _, week in
                HStack(spacing: 0) {
                    ForEach(Array(week.enumerated()), id: \.offset) { _, day in
                        cell(day)
                    }
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 14)
        .padding(.bottom, 12)
        .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .strokeBorder(MurmurTheme.rule, lineWidth: 1)
        }
    }

    @ViewBuilder
    private func cell(_ day: Date?) -> some View {
        if let day {
            let marked = markedDays.contains(day)
            let isToday = day == today
            let number = calendar.component(.day, from: day)
            Group {
                if marked {
                    Button { onSelect(day) } label: {
                        dayLabel(number, marked: true, isToday: isToday)
                    }
                    .buttonStyle(MurmurPressStyle())
                    .accessibilityLabel(Self.spoken.string(from: day) + "，有当年今日的记录")
                    .accessibilityIdentifier("archive-day-\(Self.key.string(from: day))")
                } else {
                    dayLabel(number, marked: false, isToday: isToday)
                        .accessibilityHidden(true)
                }
            }
            .frame(maxWidth: .infinity)
        } else {
            Color.clear.frame(maxWidth: .infinity, minHeight: 40)
        }
    }

    private func dayLabel(_ number: Int, marked: Bool, isToday: Bool) -> some View {
        Text("\(number)")
            .font(MurmurTheme.body(.subheadline, weight: marked ? .medium : .regular))
            .monospacedDigit()
            .foregroundStyle(marked ? MurmurTheme.onAccent : MurmurTheme.secondaryInk)
            .frame(width: 34, height: 34)
            .background {
                if marked { Circle().fill(MurmurTheme.accent) }
            }
            // Today is a ring and nothing else, so "has a record" and "is
            // today" stay two different marks rather than one ambiguous one.
            .overlay {
                if isToday { Circle().strokeBorder(MurmurTheme.accent, lineWidth: 1.5) }
            }
            .frame(minWidth: 44, minHeight: 44)
            .contentShape(Rectangle())
    }

    private var weekdaySymbols: [String] {
        let symbols = calendar.veryShortStandaloneWeekdaySymbols
        let first = calendar.firstWeekday - 1
        return Array(symbols[first...] + symbols[..<first])
    }

    /// The month laid out in rows of seven, with `nil` for the days either side
    /// of it.  Built from `Calendar` rather than arithmetic so a locale that
    /// starts its week on Sunday gets the grid it expects.
    private var weeks: [[Date?]] {
        guard let interval = calendar.dateInterval(of: .month, for: month),
              let count = calendar.range(of: .day, in: .month, for: month)?.count
        else { return [] }
        let first = calendar.startOfDay(for: interval.start)
        let lead = (calendar.component(.weekday, from: first) - calendar.firstWeekday + 7) % 7
        var cells: [Date?] = Array(repeating: nil, count: lead)
        for offset in 0..<count {
            cells.append(calendar.date(byAdding: .day, value: offset, to: first))
        }
        while cells.count % 7 != 0 { cells.append(nil) }
        return stride(from: 0, to: cells.count, by: 7).map { Array(cells[$0..<$0 + 7]) }
    }

    static let key: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private static let spoken = DateFormatter.murmur("MMMd")
}

// MARK: - One day

/// A day's rooms, with a composer that continues the latest photo room while
/// keeping the new exchange filed on this selected day.
struct ArchiveDayView: View {
    @StateObject private var model: ArchiveDayModel
    @ObservedObject private var archive: MurmurArchive
    let day: Date
    @FocusState private var composerFocused: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.murmurTabBarClearance) private var tabBarClearance
    @EnvironmentObject private var keyboard: MurmurKeyboardState

    private static let title = DateFormatter.murmur("MMMd")
    private static let weekday = DateFormatter.murmur("EEEE")
    private static let bottomAnchor = "archive-day-bottom"

    private var rows: [MurmurMessage] { archive.rows(on: day) }
    private var bottomClearance: CGFloat {
        MurmurKeyboardClearance.total(
            overlap: keyboard.overlap,
            resting: tabBarClearance
        )
    }

    init(model: ArchiveDayModel) {
        _model = StateObject(wrappedValue: model)
        _archive = ObservedObject(wrappedValue: model.archive)
        day = model.day
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(rows) { row in
                            ArchiveRowView(
                                row: row,
                                imageURL: row.imageFile.map(archive.imageURL(for:))
                            )
                            .id(row.id)
                        }
                        if model.isAwaitingReply {
                            HStack {
                                ArchiveTypingIndicator()
                                Spacer(minLength: 56)
                            }
                            .accessibilityIdentifier("archive-day-typing")
                        }
                        if let failure = model.failure {
                            HStack {
                                Text(failure.message)
                                    .font(MurmurTheme.body(.footnote))
                                    .foregroundStyle(MurmurTheme.coral)
                                    .fixedSize(horizontal: false, vertical: true)
                                Spacer(minLength: 40)
                            }
                        }
                        Color.clear.frame(height: 1).id(Self.bottomAnchor)
                    }
                    .frame(maxWidth: MurmurTheme.contentWidth)
                    .padding(.horizontal, MurmurTheme.pageInset)
                    .padding(.vertical, 16)
                    .frame(maxWidth: .infinity)
                }
                .defaultScrollAnchor(.bottom)
                .defaultScrollAnchor(.bottom, for: .sizeChanges)
                .murmurKeyboardDismissSurface(
                    isFocused: composerFocused,
                    dismiss: { composerFocused = false }
                )
                .animation(.easeInOut(duration: 0.22), value: rows)
                .animation(.easeInOut(duration: 0.22), value: model.isAwaitingReply)
                .onChange(of: rows.count) { _, _ in scrollToBottom(proxy) }
                .onChange(of: model.isAwaitingReply) { _, _ in scrollToBottom(proxy) }
                .onChange(of: composerFocused) { _, focused in
                    guard focused else { return }
                    scrollToBottom(proxy)
                }
                .onChange(of: bottomClearance) { _, _ in
                    guard composerFocused else { return }
                    var transaction = Transaction()
                    transaction.animation = nil
                    withTransaction(transaction) {
                        proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            composer
                // A NavigationStack destination does not inherit the shell's
                // outer safe-area padding. It therefore uses the complete,
                // stable max(tab bar, keyboard) clearance from the helper.
                .padding(.bottom, bottomClearance)
        }
        // A pushed NavigationStack destination establishes its own keyboard
        // safe-area response instead of inheriting the shell's. The composer
        // already follows the window's keyboard layout guide, so allowing the
        // system response here would lift the whole page once and then add our
        // measured clearance a second time.
        .ignoresSafeArea(.keyboard, edges: .bottom)
        .background(MurmurTheme.paper.ignoresSafeArea())
        .navigationTitle(Self.title.string(from: day))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 0) {
                    Text(Self.title.string(from: day))
                        .font(MurmurTheme.display(.headline))
                        .foregroundStyle(MurmurTheme.ink)
                    Text("\(Self.weekday.string(from: day)) · 聊过 \(archive.photoCount(on: day)) 张")
                        .font(MurmurTheme.body(.caption2))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                }
            }
        }
        .onDisappear {
            composerFocused = false
            keyboard.focusDidChange(false)
            model.close()
        }
        .onChange(of: composerFocused, initial: true) { _, focused in
            keyboard.focusDidChange(focused)
        }
#if DEBUG
        .onChange(of: composerFocused, initial: true) { _, focused in
            MurmurDiagnostics.recordKeyboardFocus(source: "archive-day", focused: focused)
        }
        .onChange(of: bottomClearance, initial: true) { _, clearance in
            MurmurDiagnostics.recordKeyboardClearance(
                source: "archive-day",
                overlap: keyboard.overlap,
                clearance: clearance
            )
        }
#endif
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 6) {
            TextField("接着这天说", text: $model.draft, axis: .vertical)
                .font(MurmurTheme.body(.body))
                .foregroundStyle(MurmurTheme.ink)
                .lineLimit(1...4)
                .focused($composerFocused)
                .submitLabel(.send)
                .onSubmit { model.send() }
                .onChange(of: model.draft, initial: false) { _, newValue in
                    guard newValue.contains("\n") else { return }
                    model.draft = newValue.replacingOccurrences(of: "\n", with: "")
                    model.send()
                }
                .padding(.leading, 12)
                .padding(.vertical, 12)
                .accessibilityLabel("接着这天对 Murmur 说")
                .accessibilityIdentifier("archive-day-composer")

            Button { model.send() } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(model.canSend ? MurmurTheme.paper : MurmurTheme.secondaryInk)
                    .frame(width: MurmurTheme.disc, height: MurmurTheme.disc)
                    .background(model.canSend ? MurmurTheme.ink : MurmurTheme.rule, in: Circle())
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(MurmurPressStyle())
            .disabled(!model.canSend)
            .accessibilityLabel("继续这天的对话")
            .accessibilityIdentifier("archive-day-send")
        }
        .padding(.horizontal, 5)
        .padding(.vertical, 4)
        .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 26))
        .overlay { RoundedRectangle(cornerRadius: 26).stroke(MurmurTheme.rule, lineWidth: 1) }
        .frame(maxWidth: MurmurTheme.contentWidth)
        .padding(.horizontal, MurmurTheme.pageInset)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity)
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.22)) {
            proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
        }
    }
}

/// One row of an archived day.  The transcript's bubble shape again, read-only:
/// there is no resend here, because there is nothing in flight to resend.
private struct ArchiveRowView: View {
    let row: MurmurMessage
    let imageURL: URL?

    private var isOutgoing: Bool { row.author == .you }

    var body: some View {
        HStack {
            if isOutgoing { Spacer(minLength: 40) }
            VStack(alignment: isOutgoing ? .trailing : .leading, spacing: 7) {
                if let imageURL {
                    ArchivePhoto(url: imageURL)
                }
                if !row.text.isEmpty {
                    Text(row.text)
                        .font(MurmurTheme.body(.body))
                        .foregroundStyle(isOutgoing ? MurmurTheme.onAccent : MurmurTheme.ink)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(
                            isOutgoing ? MurmurTheme.outgoingBubble : MurmurTheme.raisedPaper,
                            in: RoundedRectangle(cornerRadius: 18, style: .continuous)
                        )
                        .overlay {
                            if !isOutgoing {
                                RoundedRectangle(cornerRadius: 18, style: .continuous)
                                    .strokeBorder(MurmurTheme.rule, lineWidth: 1)
                            }
                        }
                }
            }
            if !isOutgoing { Spacer(minLength: 40) }
        }
    }
}

private struct ArchivePhoto: View {
    let url: URL
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(maxWidth: 220, maxHeight: 220)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            } else {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(MurmurTheme.rule.opacity(0.5))
                    .frame(width: 180, height: 140)
            }
        }
        .overlay {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(MurmurTheme.rule, lineWidth: 1)
        }
        .accessibilityLabel("这天聊过的照片")
        .task(id: url) {
            // Off the main actor: a day with a dozen photos would otherwise
            // decode all of them in the middle of a scroll.
            let decoded = await Task.detached(priority: .userInitiated) {
                UIImage(contentsOfFile: url.path)
            }.value
            image = decoded
        }
    }
}

private struct ArchiveTypingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase = 0
    private let timer = Timer.publish(every: 0.28, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(MurmurTheme.secondaryInk)
                    .frame(width: 5, height: 5)
                    .offset(y: reduceMotion || phase != index ? 0 : -2)
            }
        }
        .padding(.horizontal, 14)
        .frame(height: 36)
        .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 18))
        .overlay {
            RoundedRectangle(cornerRadius: 18).strokeBorder(MurmurTheme.rule, lineWidth: 1)
        }
        .accessibilityLabel("Murmur 正在回应")
        .onReceive(timer) { _ in
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 0.18)) { phase = (phase + 1) % 3 }
        }
    }
}


// MARK: - One language

/// Murmur is written in Chinese and is not localised: every string on every
/// screen is Chinese in the source.  A calendar that followed the device locale
/// would be the only English thing in the app on an English phone, so dates are
/// pinned the same way the copy is.  Monday first, as the design has it.
extension Calendar {
    static let murmur: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = Locale(identifier: "zh_Hans_CN")
        calendar.firstWeekday = 2
        return calendar
    }()
}

extension DateFormatter {
    static func murmur(_ template: String) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_Hans_CN")
        formatter.calendar = .murmur
        formatter.setLocalizedDateFormatFromTemplate(template)
        return formatter
    }
}
