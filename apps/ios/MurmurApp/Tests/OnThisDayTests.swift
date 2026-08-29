import Photos
import XCTest
@testable import Murmur

final class OnThisDayShaderTests: XCTestCase {
    /// The dissolve resolves its shader by name at draw time, so a .metal file
    /// that never made Compile Sources compiles clean and only fails on a
    /// swipe.  MurmurShaderSupport is the guard against that — but a guard
    /// that answers "missing" for a shader which is present is worse than no
    /// guard at all: the particle send-off would silently never run, on every
    /// device, and the plain fade would stand in for it forever.
    func testTheDissolveShaderIsFoundInTheShippedLibrary() {
        XCTAssertTrue(
            MurmurShaderSupport.particleDissolve,
            "onThisDayDissolve is not visible in the app's default.metallib. "
                + "Either OnThisDay.metal left the target, or the symbol is not "
                + "listed the way MurmurShaderSupport looks for it."
        )
    }
}

final class OnThisDayAuthorizationTests: XCTestCase {
    /// `.restricted` is not a fifth state in the UI: a managed device that
    /// cannot grant the library is, for this feature, the same door as a
    /// refusal.  Mapping it anywhere else would leave a branch with no screen.
    func testEveryPhotoKitStatusLandsOnADesignedDoor() {
        XCTAssertEqual(OnThisDayAuthorization(.notDetermined), .notDetermined)
        XCTAssertEqual(OnThisDayAuthorization(.authorized), .authorized)
        XCTAssertEqual(OnThisDayAuthorization(.limited), .limited)
        XCTAssertEqual(OnThisDayAuthorization(.denied), .denied)
        XCTAssertEqual(OnThisDayAuthorization(.restricted), .denied)
    }
}

private struct FixedAuthorizationLibrary: OnThisDayLibrary {
    let status: OnThisDayAuthorization

    init(_ status: OnThisDayAuthorization) { self.status = status }

    func authorization() async -> OnThisDayAuthorization { status }
    func requestAuthorization() async -> OnThisDayAuthorization { status }
    func candidates(around date: Date, yearsBack: Int) async -> [OnThisDayCandidate] { [] }
    func randomCandidates(count: Int, excluding: Set<String>) async -> [OnThisDayCandidate] { [] }
    func image(for candidate: OnThisDayCandidate, targetPixels: CGFloat) async -> UIImage? { nil }
}

// MARK: - The shelf

final class OnThisDayShelfTests: XCTestCase {
    /// A day that is blank in every earlier year is not an empty screen: the
    /// shelf falls back to the album, and the card says which it is.
    func testABlankDayStartsOnTheAlbum() async {
        let library = CountingLibrary(sameDay: 0, album: 20)
        let model = await OnThisDayModel(library: library)
        await model.refreshAuthorization()
        await model.load()

        let candidates = await model.candidates
        XCTAssertFalse(candidates.isEmpty)
        XCTAssertEqual(candidates.first?.origin, .elsewhere)
    }

    /// Past the day's own photos the shelf carries on rather than wrapping:
    /// swiping down from the last one used to land back on the first.
    func testTheShelfCarriesOnPastTheDaysOwnPhotos() async {
        let library = CountingLibrary(sameDay: 2, album: 20)
        let model = await OnThisDayModel(library: library)
        await model.refreshAuthorization()
        await model.load()

        for _ in 0..<4 { await model.advanceAndSettle() }
        let index = await model.index
        let candidates = await model.candidates
        XCTAssertEqual(index, 4)
        XCTAssertEqual(candidates[index].origin, .elsewhere)
    }

    /// A library with nothing left to give is asked once and then left alone;
    /// each top-up spends up to two hundred draws inside PhotoKit.
    func testAnExhaustedLibraryIsNotAskedAgain() async {
        let library = CountingLibrary(sameDay: 1, album: 0)
        let model = await OnThisDayModel(library: library)
        await model.refreshAuthorization()
        await model.load()

        for _ in 0..<5 { await model.advanceAndSettle() }
        let calls = await library.randomCalls
        XCTAssertEqual(calls, 1)
        // And the card stays where it is rather than looping.
        let index = await model.index
        XCTAssertEqual(index, 0)
    }

    /// The album never hands back a photo that is already on the shelf.
    func testTopUpsExcludeWhatIsAlreadyOnTheShelf() async {
        let library = CountingLibrary(sameDay: 2, album: 20)
        let model = await OnThisDayModel(library: library)
        await model.refreshAuthorization()
        await model.load()
        for _ in 0..<6 { await model.advanceAndSettle() }

        let identifiers = await model.candidates.map(\.id)
        XCTAssertEqual(Set(identifiers).count, identifiers.count)
    }
}

/// Counts what it is asked for, so a top-up that fires twice — or never stops
/// firing — is visible rather than merely slow.
private actor CountingLibrary: OnThisDayLibrary {
    let sameDay: Int
    let album: Int
    private(set) var randomCalls = 0

    init(sameDay: Int, album: Int) {
        self.sameDay = sameDay
        self.album = album
    }

    func authorization() async -> OnThisDayAuthorization { .authorized }
    func requestAuthorization() async -> OnThisDayAuthorization { .authorized }

    func candidates(around date: Date, yearsBack: Int) async -> [OnThisDayCandidate] {
        (0..<sameDay).map { offset in
            OnThisDayCandidate(
                id: "day-\(offset)",
                creationDate: date,
                origin: .sameDay(yearsAgo: offset + 1)
            )
        }
    }

    func randomCandidates(count: Int, excluding: Set<String>) async -> [OnThisDayCandidate] {
        randomCalls += 1
        return (0..<album)
            .map { "album-\($0)" }
            .filter { !excluding.contains($0) }
            .prefix(count)
            .map { OnThisDayCandidate(id: $0, creationDate: Date(), origin: .elsewhere) }
    }

    func image(for candidate: OnThisDayCandidate, targetPixels: CGFloat) async -> UIImage? {
        UIImage(systemName: "photo")
    }
}

private extension OnThisDayModel {
    /// `advance` hands its work to a detached task; the tests need that task
    /// finished before they can read the shelf.
    @MainActor
    func advanceAndSettle() async {
        advance()
        for _ in 0..<40 { await Task.yield() }
    }
}

/// 当年今日's archive, away from the screen: the day a room happened on is
/// what the calendar is built from, so the grouping has to be right in the
/// user's own calendar rather than UTC.
@MainActor
final class MurmurArchiveTests: XCTestCase {
    private func makeArchive() -> (MurmurArchive, URL) {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        return (MurmurArchive(store: MurmurTranscriptStore(directory: directory)), directory)
    }

    private func writeTestJPEG(size: CGSize, name: String) throws -> URL {
        let renderer = UIGraphicsImageRenderer(size: size)
        let image = renderer.image { context in
            UIColor.systemTeal.setFill()
            context.fill(CGRect(origin: .zero, size: size))
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("murmur-archive-\(name)-\(UUID().uuidString).jpg")
        try XCTUnwrap(image.jpegData(compressionQuality: 0.8)).write(to: url)
        return url
    }

    private func day(_ text: String) -> Date {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = .murmur
        formatter.timeZone = Calendar.murmur.timeZone
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.date(from: text)!
    }

    func testRowsAreFiledUnderTheDayTheyHappenedOn() async throws {
        let (archive, directory) = makeArchive()
        defer { try? FileManager.default.removeItem(at: directory) }

        // Two rooms on one day and one late the next, including a row at a
        // minute either side of midnight — the case a UTC-based grouping puts
        // on the wrong square.
        for (text, when) in [
            ("第一张", "2026-08-20 09:15"),
            ("说了一句", "2026-08-20 09:16"),
            ("第二张", "2026-08-20 23:59"),
            ("隔天那张", "2026-08-21 00:01"),
        ] {
            await archive.record(
                .init(author: .you, text: text, sentAt: day(when)), photoURL: nil
            )
        }

        XCTAssertEqual(archive.daysWithRooms.count, 2)
        XCTAssertEqual(archive.rows(on: day("2026-08-20 12:00")).map(\.text),
                       ["第一张", "说了一句", "第二张"])
        XCTAssertEqual(archive.rows(on: day("2026-08-21 12:00")).map(\.text), ["隔天那张"])
        XCTAssertEqual(archive.recentDays().first, Calendar.murmur.startOfDay(for: day("2026-08-21 00:01")))
    }

    /// The count under a day is photos, not rows: the talk about a picture is
    /// not another picture.
    func testTheDayCountIsPhotosRatherThanRows() async throws {
        let (archive, directory) = makeArchive()
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try writeTestJPEG(size: CGSize(width: 40, height: 40), name: "archive")
        defer { try? FileManager.default.removeItem(at: source) }

        await archive.record(.init(author: .you, text: "", sentAt: day("2026-08-20 09:00")), photoURL: source)
        await archive.record(.init(author: .murmur, text: "这是哪儿", sentAt: day("2026-08-20 09:01")), photoURL: nil)
        await archive.record(.init(author: .you, text: "老地方", sentAt: day("2026-08-20 09:02")), photoURL: nil)

        XCTAssertEqual(archive.rows(on: day("2026-08-20 12:00")).count, 3)
        XCTAssertEqual(archive.photoCount(on: day("2026-08-20 12:00")), 1)
    }

    func testAContinuedTurnKeepsItsRealSendTimeButStaysOnTheSelectedDay() async {
        let (archive, directory) = makeArchive()
        defer { try? FileManager.default.removeItem(at: directory) }
        let selectedDay = day("2024-03-12 09:00")
        let actualSendTime = day("2026-08-26 21:17")

        await archive.record(
            .init(
                author: .you,
                text: "后来我又去了",
                sentAt: actualSendTime,
                archiveDay: selectedDay,
                momentID: "continued-moment"
            ),
            photoURL: nil
        )

        XCTAssertEqual(archive.rows(on: selectedDay).map(\.text), ["后来我又去了"])
        XCTAssertTrue(archive.rows(on: actualSendTime).isEmpty)
        XCTAssertEqual(archive.rows.first?.sentAt, actualSendTime)

        let reloaded = MurmurArchive(store: MurmurTranscriptStore(directory: directory))
        await reloaded.load()
        XCTAssertEqual(reloaded.rows(on: selectedDay).map(\.text), ["后来我又去了"])
        XCTAssertEqual(reloaded.rows.first?.sentAt, actualSendTime)
    }

    func testMessagesWrittenBeforeArchiveDayStillDecodeAndGroupBySentAt() throws {
        let data = Data("""
        [{"id":"legacy","author":"murmur","text":"旧回答","sentAt":"2026-08-20T09:00:00Z","delivery":"sent"}]
        """.utf8)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let rows = try decoder.decode([MurmurMessage].self, from: data)

        XCTAssertNil(rows[0].archiveDay)
        XCTAssertEqual(rows[0].text, "旧回答")
    }

    func testContextComesOnlyFromTheLatestPhotoRoomAndKeepsTheNewestEightMoments() async throws {
        let (archive, directory) = makeArchive()
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = try writeTestJPEG(size: CGSize(width: 40, height: 40), name: "context")
        defer { try? FileManager.default.removeItem(at: source) }
        let selectedDay = day("2026-08-20 12:00")

        await archive.record(
            .init(author: .you, text: "", sentAt: day("2026-08-20 08:00"), momentID: "older-room"),
            photoURL: source
        )
        await archive.record(
            .init(author: .murmur, text: "旧房间", sentAt: day("2026-08-20 08:01"), momentID: "older-room"),
            photoURL: nil
        )
        await archive.record(
            .init(author: .you, text: "", sentAt: day("2026-08-20 20:00"), momentID: "moment-0"),
            photoURL: source
        )
        for index in 1...10 {
            await archive.record(
                .init(
                    author: index.isMultiple(of: 2) ? .murmur : .you,
                    text: "第 \(index) 轮",
                    sentAt: day("2026-08-20 20:\(String(format: "%02d", index))"),
                    momentID: "moment-\(index)"
                ),
                photoURL: nil
            )
        }
        // Duplicates do not consume the bounded context budget.
        await archive.record(
            .init(author: .murmur, text: "补一句", sentAt: day("2026-08-20 20:20"), momentID: "moment-10"),
            photoURL: nil
        )

        XCTAssertEqual(
            archive.contextMomentIDs(on: selectedDay),
            (3...10).map { "moment-\($0)" }
        )
    }

    /// A row with neither words nor a picture would be an empty bubble on the
    /// day screen; the copy failing is not a reason to put one there.
    func testAnEmptyRowIsNotFiled() async {
        let (archive, directory) = makeArchive()
        defer { try? FileManager.default.removeItem(at: directory) }
        await archive.record(.init(author: .you, text: ""), photoURL: nil)
        XCTAssertTrue(archive.rows.isEmpty)
    }

    /// Murmur is not localised, so the grid is pinned rather than following the
    /// device: Monday first, whatever phone this is.
    func testTheCalendarStartsOnMonday() {
        XCTAssertEqual(Calendar.murmur.firstWeekday, 2)
    }
}
