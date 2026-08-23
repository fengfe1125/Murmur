import Foundation
import SwiftUI

/// What was said in 当年今日's rooms, kept by the day it was said on.
///
/// Deliberately not the conversation.  A room is about one old photo, and the
/// thing worth coming back to is "which day did we talk about this" — so the
/// archive is indexed by day and the chat stays a chat.  Same file format and
/// the same complete file protection as the transcript; a directory of its own.
@MainActor
final class MurmurArchive: ObservableObject, MurmurRoomRecorder {
    /// Every row, oldest first.  Days are a view over this rather than a second
    /// structure to keep in step.
    @Published private(set) var rows: [MurmurMessage] = []
    @Published private(set) var isLoaded = false

    private let store: MurmurTranscriptStore
    private let calendar: Calendar

    init(store: MurmurTranscriptStore? = nil, calendar: Calendar = .murmur) {
        self.store = store ?? MurmurTranscriptStore.archive()
        self.calendar = calendar
    }

    func load() async {
        guard !isLoaded else { return }
        rows = await store.load()
        isLoaded = true
    }

    /// Which photo file a row names, resolved against the archive's own images.
    nonisolated func imageURL(for name: String) -> URL { store.imageURL(for: name) }

    // ---- Reading -------------------------------------------------------------

    /// The days that have anything on them, as `startOfDay` dates.
    var daysWithRooms: Set<Date> {
        Set(rows.map { calendar.startOfDay(for: $0.sentAt) })
    }

    /// One day's thread, in the order it happened.
    func rows(on day: Date) -> [MurmurMessage] {
        let start = calendar.startOfDay(for: day)
        return rows.filter { calendar.isDate($0.sentAt, inSameDayAs: start) }
    }

    /// How many photos that day carried.  The count the calendar's day row
    /// shows — rows without a picture are the talk about one, not another one.
    func photoCount(on day: Date) -> Int {
        rows(on: day).count { $0.imageFile != nil }
    }

    /// The most recent days that have anything on them, newest first.
    func recentDays(limit: Int = 60) -> [Date] {
        daysWithRooms.sorted(by: >).prefix(limit).map { $0 }
    }

    // ---- Writing -------------------------------------------------------------

    func record(_ message: MurmurMessage, photoURL: URL?) async {
        var row = message
        if let photoURL {
            row.imageFile = await store.adoptImage(at: photoURL, id: row.id)
        }
        // A row with neither words nor a picture is an empty bubble; the copy
        // failing is not a reason to put one in the archive.
        guard !row.text.isEmpty || row.imageFile != nil else { return }
        rows.append(row)
        persist()
    }

    func setDelivery(_ delivery: MurmurDeliveryState, for messageID: String) {
        guard let index = rows.firstIndex(where: { $0.id == messageID }) else { return }
        guard rows[index].delivery != delivery else { return }
        rows[index].delivery = delivery
        persist()
    }

    func withdraw(_ messageID: String) {
        guard rows.contains(where: { $0.id == messageID }) else { return }
        rows.removeAll { $0.id == messageID }
        persist()
    }

    func clear() async {
        rows = []
        await store.clear()
    }

    private func persist() {
        let snapshot = rows
        Task { [store] in await store.save(snapshot) }
    }
}
