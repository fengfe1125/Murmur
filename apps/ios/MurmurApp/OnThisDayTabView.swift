import SwiftUI

/// 当年今日, one level up from the browser it used to be.
///
/// The calendar is the first thing: a deepened circle on every day that carries
/// a room, and pressing one opens that day's thread — the browser's exchanges,
/// filed by the day they happened on.  Under it is the way in to the browser
/// itself, which is unchanged: particles, up to send, down for the next one.
struct OnThisDayTabView: View {
    @ObservedObject var model: MurmurSessionModel
    /// Owned by `MurmurShell`, so leaving the tab does not throw away the
    /// album read that filled it.
    @ObservedObject var onThisDay: OnThisDayModel
    /// The calendar's marks change while a photo room cover is open.  Watching
    /// only the session leaves the calendar stale until another redraw.
    @ObservedObject private var archive: MurmurArchive
    @State private var month = Date()
    @State private var openDay: Date?
    @State private var showBrowser = false
    @Environment(\.scenePhase) private var scenePhase

    private let calendar = Calendar.murmur

    init(model: MurmurSessionModel, onThisDay: OnThisDayModel) {
        _model = ObservedObject(wrappedValue: model)
        _onThisDay = ObservedObject(wrappedValue: onThisDay)
        _archive = ObservedObject(wrappedValue: model.archive)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    MurmurMonthView(
                        month: month,
                        markedDays: archive.daysWithRooms,
                        onSelect: { openDay = $0 }
                    )
                    OnThisDayEntry { showBrowser = true }
                }
                .frame(maxWidth: MurmurTheme.contentWidth)
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, 16)
                .frame(maxWidth: .infinity)
            }
            .background(MurmurTheme.paper.ignoresSafeArea())
            .navigationTitle("当年今日")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    VStack(spacing: 0) {
                        Text("当年今日")
                            .font(MurmurTheme.display(.headline))
                            .foregroundStyle(MurmurTheme.ink)
                        Text(Self.monthLabel.string(from: month))
                            .font(MurmurTheme.body(.caption2))
                            .foregroundStyle(MurmurTheme.secondaryInk)
                    }
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button { step(-1) } label: { Image(systemName: "chevron.left") }
                        .accessibilityLabel("上个月")
                        .accessibilityIdentifier("archive-prev-month")
                    Button { step(1) } label: { Image(systemName: "chevron.right") }
                        .disabled(isCurrentMonth)
                        .accessibilityLabel("下个月")
                        .accessibilityIdentifier("archive-next-month")
                }
            }
            .navigationDestination(item: $openDay) { day in
                ArchiveDayView(model: model.makeArchiveDay(day: day))
            }
        }
        .task {
            await archive.load()
            await onThisDay.refreshAuthorization()
        }
        .onChange(of: scenePhase, initial: false) { _, phase in
            guard phase == .active else { return }
            Task { await onThisDay.refreshAuthorization() }
        }
        // The browser keeps its cover: the dissolve owns the whole screen, and
        // a tab bar under it would be a second thing to look at mid-gesture.
        .fullScreenCover(isPresented: $showBrowser) {
            OnThisDayFlowView(model: onThisDay) { image, provenance in
                model.makePhotoRoom(image: image, provenance: provenance)
            }
        }
    }

    private var isCurrentMonth: Bool {
        calendar.isDate(month, equalTo: Date(), toGranularity: .month)
    }

    private func step(_ months: Int) {
        guard let next = calendar.date(byAdding: .month, value: months, to: month) else { return }
        guard months < 0 || next <= Date() else { return }
        month = next
    }

    private static let monthLabel = DateFormatter.murmur("yMMMM")
}

/// The door into the browser.  Deliberately the largest thing under the
/// calendar: the calendar is where you look something up, this is where the
/// feature actually happens.
private struct OnThisDayEntry: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 14) {
                ParticlePreview()
                VStack(spacing: 3) {
                    Text("当年今日")
                        .font(MurmurTheme.display(.title3))
                        .foregroundStyle(MurmurTheme.ink)
                    Text(subtitle)
                        .font(MurmurTheme.body(.caption))
                        .foregroundStyle(MurmurTheme.secondaryInk)
                }
                HStack(spacing: 18) {
                    hint("arrow.up", "发给 Murmur")
                    hint("arrow.down", "换下一张")
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 20)
            .padding(.horizontal, 18)
            .murmurGlass()
            .contentShape(Rectangle())
        }
        .buttonStyle(MurmurPressStyle())
        .accessibilityLabel("当年今日，\(subtitle)")
        .accessibilityIdentifier("onthisday-entry")
    }

    // Deliberately not a count: the album is only read once the cover is up,
    // and a number that has to be corrected a second later is worse than none.
    private let subtitle = "翻翻同一天的旧照片"

    private func hint(_ symbol: String, _ text: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: symbol)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(MurmurTheme.accentInk)
            Text(text)
                .font(MurmurTheme.body(.caption2))
                .foregroundStyle(MurmurTheme.secondaryInk)
        }
    }
}

/// A photo caught mid-dissolve.  Not the shader — this is a still of what the
/// gesture does, so the door looks like the room behind it.
private struct ParticlePreview: View {
    var body: some View {
        ZStack(alignment: .trailing) {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [
                            MurmurTheme.accent.opacity(0.55),
                            MurmurTheme.ink.opacity(0.55)
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: 132, height: 132)
                .overlay {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .strokeBorder(MurmurTheme.rule, lineWidth: 1)
                }
            Canvas { context, size in
                var seed: UInt64 = 7
                func next() -> Double {
                    seed = seed &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
                    return Double(seed >> 33) / Double(UInt64(1) << 31)
                }
                for _ in 0..<26 {
                    let side = 3 + next() * 4
                    let x = size.width - 54 + next() * 54
                    let y = next() * size.height
                    context.fill(
                        Path(roundedRect: CGRect(x: x, y: y, width: side, height: side), cornerRadius: 1),
                        with: .color(MurmurTheme.accent.opacity(0.2 + next() * 0.6))
                    )
                }
            }
            .frame(width: 92, height: 132)
            .offset(x: 34)
            .allowsHitTesting(false)
        }
        .frame(height: 132)
        .accessibilityHidden(true)
    }
}
