import SwiftUI

struct ArchiveDayView: View {
    @StateObject private var model: ArchiveDayModel
    @ObservedObject private var archive: MurmurArchive
    let day: Date
    @FocusState private var composerFocused: Bool
    @Environment(\.murmurReduceMotion) private var reduceMotion
    @EnvironmentObject private var keyboard: MurmurKeyboardState

    private static let title = DateFormatter.murmur("MMMd")
    private static let weekday = DateFormatter.murmur("EEEE")
    private static let bottomAnchor = "archive-day-bottom"

    private var rows: [MurmurMessage] { archive.rows(on: day) }
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
                        Button("载入更早的记录") { Task {
                            let anchor = rows.first?.id
                            await archive.load(day: day, earlier: true)
                            if let anchor { proxy.scrollTo(anchor, anchor: .top) }
                        } }.frame(minHeight:44)
                        if let failure = archive.storageFailure { Text(failure).font(.footnote).foregroundStyle(MurmurTheme.coral); Button("重试本机保存") { Task { await archive.retryStorage() } }.frame(minHeight: 44) }
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
                .murmurKeyboardDismissSurface(
                    isFocused: composerFocused,
                    dismiss: { composerFocused = false }
                )
                .animation(reduceMotion ? nil : MurmurMotion.content, value: rows.count)
                .animation(reduceMotion ? nil : MurmurMotion.content, value: model.isAwaitingReply)
                .onChange(of: rows.last?.id) { _, _ in scrollToBottom(proxy) }
                .onChange(of: model.isAwaitingReply) { _, _ in scrollToBottom(proxy) }
                .onChange(of: composerFocused) { _, focused in
                    guard focused else { return }
                    scrollToBottom(proxy)
                }
                .onChange(of: keyboard.overlap) { _, _ in
                    guard composerFocused else { return }
                    var transaction = Transaction()
                    transaction.animation = nil
                    withTransaction(transaction) {
                        proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { composer }
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
        .task { await archive.load(day: day) }
        .toolbar(composerFocused ? .hidden : .visible, for: .tabBar)
        .onDisappear {
            composerFocused = false
            model.close()
        }
#if DEBUG
        .onChange(of: composerFocused, initial: true) { _, focused in
            MurmurDiagnostics.recordKeyboardFocus(source: "archive-day", focused: focused)
        }
#endif
    }

    private var composer: some View {
        MurmurComposer(
            text: $model.draft,
            focused: $composerFocused,
            placeholder: "接着这天说",
            fieldLabel: "接着这天对 Murmur 说",
            fieldIdentifier: "archive-day-composer",
            sendIdentifier: "archive-day-send",
            canSend: model.canSend,
            onSend: { model.send() }
        ) { EmptyView() }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(reduceMotion ? nil : MurmurMotion.content) {
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
    var body: some View {
        ProgressView("Murmur 正在回应")
            .font(.footnote)
            .padding(12)
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
