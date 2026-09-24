import SwiftUI

/// Native date selection opens independent photo-room archives.
struct OnThisDayTabView: View {
    @ObservedObject var model: MurmurSessionModel
    /// Owned by `MurmurShell`, so leaving the tab does not throw away the
    /// album read that filled it.
    @ObservedObject var onThisDay: OnThisDayModel
    /// The calendar's marks change while a photo room cover is open.  Watching
    /// only the session leaves the calendar stale until another redraw.
    @ObservedObject private var archive: MurmurArchive
    @State private var selectedDay: Date?
    @State private var openDay: Date?
    @State private var showBrowser = false
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private let calendar = Calendar.murmur

    private var archiveDays: Set<String> {
        Set(archive.daysWithRooms.map { MurmurDay.key($0) })
    }

    init(model: MurmurSessionModel, onThisDay: OnThisDayModel) {
        _model = ObservedObject(wrappedValue: model)
        _onThisDay = ObservedObject(wrappedValue: onThisDay)
        _archive = ObservedObject(wrappedValue: model.archive)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text(Date.now.formatted(.dateTime.month().day().weekday(.wide).locale(Locale(identifier: "zh_Hans_CN"))))
                        .font(.title2.weight(.semibold))
                        .accessibilityAddTraits(.isHeader)
                    OnThisDayEntry { showBrowser = true }
                    Text("照片聊天记录")
                        .font(.headline)
                        .accessibilityAddTraits(.isHeader)
                    MurmurNativeCalendar(
                        selected: $selectedDay,
                        markedDays: archiveDays,
                        markLabel: "有照片房间存档",
                        selectableDays: archiveDays
                    )
                    .frame(maxWidth: 420)
                    .frame(maxWidth: .infinity)
                    .clipped()
                    .onChange(of: selectedDay) { _, day in openDay = day }
                    Text("• 有照片房间存档").font(.caption).foregroundStyle(.secondary)
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
            .navigationDestination(item: $openDay) { day in
                ArchiveDayView(model: model.makeArchiveDay(day: day))

            }
        }
        .onChange(of: openDay) { _, day in
            if day == nil { selectedDay = nil }
        }
        .task {
            await archive.load()
            await onThisDay.refreshAuthorization()
        }
        .onChange(of: scenePhase, initial: false) { _, phase in
            guard phase == .active else { return }
            Task { await onThisDay.refreshAuthorization() }
        }
        .fullScreenCover(isPresented: $showBrowser) {
            OnThisDayFlowView(model: onThisDay) { image, provenance in
                model.makePhotoRoom(image: image, provenance: provenance)
            }
            .environment(\.dynamicTypeSize, dynamicTypeSize)
        }
    }


}

/// A deliberate entry; the home page does not request photo access.
private struct OnThisDayEntry: View {
    let action: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: "photo.on.rectangle.angled")
                .font(.title2)
                .foregroundStyle(MurmurTheme.accentInk)
                .accessibilityHidden(true)
            Text("翻翻旧照片").font(.title3.weight(.semibold))
            Text("看看往年的这一天，也聊聊照片里的故事。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Button("翻翻同一天的旧照片", systemImage: "arrow.right", action: action)
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .accessibilityIdentifier("onthisday-entry")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(20)
        .background(MurmurTheme.raisedPaper, in: RoundedRectangle(cornerRadius: 20))
    }
}
