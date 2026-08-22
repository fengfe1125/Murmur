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

    /// Limited access is the one status that hides the entry rather than
    /// explaining itself from behind it: with a hand-picked slice of the
    /// library the feature cannot tell the truth about the day.
    func testOnlyLimitedAccessHidesTheEntry() async {
        for status in [OnThisDayAuthorization.notDetermined, .denied, .authorized] {
            let model = await OnThisDayModel(library: FixedAuthorizationLibrary(status))
            await model.refreshAuthorization()
            let visible = await model.entryVisible
            XCTAssertTrue(visible, "\(status) should keep the disc in the chrome")
        }
        let limited = await OnThisDayModel(library: FixedAuthorizationLibrary(.limited))
        await limited.refreshAuthorization()
        let visible = await limited.entryVisible
        XCTAssertFalse(visible)
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
