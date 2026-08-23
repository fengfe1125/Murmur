import XCTest
import UIKit

@MainActor
final class MurmurUITests: XCTestCase {
    func testAMomentSurvivesAColdLaunch() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        enterMoment(composer, "今天的风")
        app.buttons["send-moment"].tap()
        XCTAssertTrue(line("今天的风", in: app).waitForExistence(timeout: 10))

        // The transcript lives on this device on purpose, so quitting the app
        // must not be a way of losing the conversation.
        app.terminate()
        app.launchArguments = ["--murmur-ui-testing"]
        app.launch()
        XCTAssertTrue(line("今天的风", in: app).waitForExistence(timeout: 10))
    }

    func testLandscapeComposerRemainsHittableAndCanSend() throws {
        continueAfterFailure = false
        XCUIDevice.shared.orientation = .landscapeLeft
        defer { XCUIDevice.shared.orientation = .portrait }
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertEqual(composer.label, "这一刻的文字")
        enterMoment(composer, "横屏这一刻")
        let send = app.buttons["send-moment"]
        XCTAssertTrue(send.waitForExistence(timeout: 2))
        XCTAssertTrue(send.isHittable)
        assertMinimumHitArea(send)
        send.tap()
        XCTAssertTrue(bubble(in: app).waitForExistence(timeout: 5))
    }

    func testDarkAccessibilityXXXLKeepsPrimaryControlsReachable() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--murmur-ui-testing", "--murmur-reset-transcript"]
        app.launchEnvironment["AppleInterfaceStyle"] = "Dark"
        app.launchEnvironment["UIPreferredContentSizeCategoryName"] = "UICTContentSizeCategoryAccessibilityExtraExtraExtraLarge"
        app.launch()

        let composer = app.textFields["moment-composer"]
        let addPhoto = app.buttons["添加照片"]
        let settings = app.buttons["tab-me"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertTrue(addPhoto.waitForExistence(timeout: 3))
        XCTAssertTrue(settings.waitForExistence(timeout: 3))
        assertMinimumHitArea(addPhoto)
        assertMinimumHitArea(settings)
        enterMoment(composer, "大字也能到达")
        let send = app.buttons["send-moment"]
        XCTAssertTrue(send.isHittable)
        assertMinimumHitArea(send)
        send.tap()
        XCTAssertTrue(bubble(in: app).waitForExistence(timeout: 5))
    }

    func testKeyboardReturnSubmitsMoment() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        enterMoment(composer, "键盘提交")
        composer.typeText("\n")
        XCTAssertTrue(bubble(in: app).waitForExistence(timeout: 5))
    }

    func testVoiceOverLabelsDescribeTheTranscript() throws {
        continueAfterFailure = false
        let app = launchApp(arguments: ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryLarge"])
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertEqual(composer.label, "这一刻的文字")
        XCTAssertEqual(app.buttons["send-moment"].label, "发送这一刻")
        XCTAssertTrue(app.buttons["添加照片"].exists)
        // The three stops are named, and the one you are on says so.
        XCTAssertEqual(app.buttons["tab-chat"].label, "聊天")
        XCTAssertEqual(app.buttons["tab-onThisDay"].label, "当年今日")
        XCTAssertEqual(app.buttons["tab-me"].label, "我的")
        XCTAssertTrue(app.buttons["tab-chat"].isSelected)
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "发来眼前的一刻")).firstMatch.exists
        )
    }

    func testComposerStaysUsableWhileMurmurIsAnswering() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        enterMoment(composer, "第一句")
        app.buttons["send-moment"].tap()

        // No waiting for the reply: the field has to accept the next line
        // straight away, and the second send has to go through.
        XCTAssertTrue(composer.isEnabled)
        composer.tap()
        composer.typeText("第二句")
        app.buttons["send-moment"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["murmur-message-3"].firstMatch.waitForExistence(timeout: 10)
        )
    }

    func testSendingLeavesTheKeyboardUpForTheNextLine() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        composer.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))

        // Typing without tapping the field again is the whole point: `typeText`
        // on an element that lost keyboard focus fails, so these two sends in a
        // row are the guard against the composer resigning on submit.
        for line in ["第一句", "第二句"] {
            composer.typeText(line)
            app.buttons["send-moment"].tap()
            XCTAssertTrue(self.line(line, in: app).waitForExistence(timeout: 10))
            XCTAssertTrue(app.keyboards.firstMatch.exists, "sending 「\(line)」 put the keyboard away")
        }
    }

    func testReachingForTheFieldBringsTheNewestLineBackAboveTheKeyboard() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        for line in ["一", "二", "三", "四"] {
            composer.tap()
            composer.typeText(line)
            app.buttons["send-moment"].tap()
            XCTAssertTrue(self.line(line, in: app).waitForExistence(timeout: 10))
        }
        let newest = app.descendants(matching: .any)["murmur-message-7"].firstMatch
        XCTAssertTrue(newest.waitForExistence(timeout: 10))

        // Scrolling back through history and then reaching for the field has to
        // bring the conversation down again every time, not just the first.
        // A tap on a field that is already first responder changes no focus and
        // raises no keyboard notification, which is how this used to be missed.
        for round in 1...2 {
            app.swipeDown()
            Thread.sleep(forTimeInterval: 1)
            composer.tap()
            Thread.sleep(forTimeInterval: 1.5)
            XCTAssertLessThanOrEqual(
                newest.frame.maxY, composer.frame.minY,
                "the newest line ended up behind the composer on round \(round)"
            )
        }
    }

    func testTappingTheConversationPutsTheKeyboardAwayWithoutLeavingAGap() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        for line in ["一句", "两句"] {
            composer.tap()
            composer.typeText(line)
            app.buttons["send-moment"].tap()
            XCTAssertTrue(self.line(line, in: app).waitForExistence(timeout: 10))
        }
        let newest = app.descendants(matching: .any)["murmur-message-3"].firstMatch
        XCTAssertTrue(newest.waitForExistence(timeout: 10))

        let chat = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3))
        chat.tap()
        Thread.sleep(forTimeInterval: 1.5)
        let restingComposer = composer.frame.minY
        let restingNewest = newest.frame.maxY

        // Asking for the keyboard back, rather than a first raise: it is being
        // put away one step above, and how long iOS takes to bring it out again
        // is not this test's business.  Waiting for it — instead of assuming a
        // budget — is what keeps the measurement below about the layout.
        composer.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 8),
                      "the keyboard did not come back when the field was reached for")
        Thread.sleep(forTimeInterval: 1)
        XCTAssertLessThan(composer.frame.minY, restingComposer, "the keyboard did not raise the field")

        chat.tap()
        Thread.sleep(forTimeInterval: 1.5)
        XCTAssertFalse(app.keyboards.firstMatch.exists, "tapping the conversation did not dismiss the keyboard")
        // Dismissing through the responder chain instead of the composer's own
        // focus left SwiftUI's keyboard inset applied: the field stayed hoisted
        // over a blank strip the height of the keyboard that had just left.
        XCTAssertEqual(composer.frame.minY, restingComposer, accuracy: 1,
                       "the composer did not return to its resting position")
        // The bug being guarded is a strip the height of a keyboard — some
        // 300pt.  A short conversation settles within a few points of where it
        // started, which is a matter of where the scroll lands, not a gap.
        XCTAssertEqual(newest.frame.maxY, restingNewest, accuracy: 20,
                       "the conversation did not settle back against the composer")
    }

    func testDismissingTheKeyboardBringsALongConversationBackDown() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        // Enough rows that the transcript actually scrolls: with a short one
        // the keyboard never moves the content offset, so the gap this guards
        // against has nowhere to hide.
        for index in 1...10 {
            let line = "moment \(index)"
            composer.tap()
            composer.typeText(line)
            app.buttons["send-moment"].tap()
            XCTAssertTrue(self.line(line, in: app).waitForExistence(timeout: 10))
        }
        let newest = app.descendants(matching: .any)["murmur-message-19"].firstMatch
        XCTAssertTrue(newest.waitForExistence(timeout: 10))
        // Rest has to be asked for now: the keyboard survives a send, so the
        // loop above leaves it up.  Put it away before reading the gap this
        // whole test is measured against.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)).tap()
        Thread.sleep(forTimeInterval: 1.5)
        let restGap = composer.frame.minY - newest.frame.maxY

        composer.tap()
        Thread.sleep(forTimeInterval: 1.5)
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        XCTAssertLessThanOrEqual(newest.frame.maxY, composer.frame.minY + 1)

        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)).tap()
        Thread.sleep(forTimeInterval: 2)
        XCTAssertFalse(app.keyboards.firstMatch.exists, "tapping the conversation did not dismiss the keyboard")
        // The keyboard took roughly 300pt with it; if the conversation does
        // not follow back down, the newest line is left floating above the
        // composer with bare paper between them.
        let settledGap = composer.frame.minY - newest.frame.maxY
        XCTAssertEqual(settledGap, restGap, accuracy: 24,
                       "the conversation did not come back down with the keyboard (rest \(restGap), settled \(settledGap))")
    }

    func testSettingsExposesNotificationToggleAndFrequency() throws {
        continueAfterFailure = false
        let app = launchApp()
        let settings = app.buttons["tab-me"]
        XCTAssertTrue(settings.waitForExistence(timeout: 5))
        guard UIDevice.current.userInterfaceIdiom == .phone else { return }
        settings.tap()
        XCTAssertTrue(
            app.staticTexts["允许通知"].waitForExistence(timeout: 3)
                || app.switches["允许通知"].waitForExistence(timeout: 2)
                || app.descendants(matching: .any)["notification-toggle"].firstMatch.waitForExistence(timeout: 2)
        )
        XCTAssertTrue(
            app.staticTexts["每天最多"].waitForExistence(timeout: 2)
                || app.buttons["每天最多"].waitForExistence(timeout: 2)
        )
        // Which build is installed has to be readable from inside the app;
        // a side-loaded build carries no version number that ever changes.
        XCTAssertTrue(app.descendants(matching: .any)["build-stamp"].firstMatch.exists)
        app.swipeUp()
        XCTAssertTrue(app.buttons["清空这一刻"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.buttons["清空聊天记录"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.buttons["删除账号与全部记忆"].waitForExistence(timeout: 2))
    }

    func testAddPhotoMenuRisesWithoutDimmingTheTranscript() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        enterMoment(composer, "先垫一句")
        app.buttons["send-moment"].tap()
        XCTAssertTrue(bubble(in: app).waitForExistence(timeout: 10))

        app.buttons["添加照片"].tap()
        let menu = app.descendants(matching: .any)["photo-source-menu"].firstMatch
        XCTAssertTrue(menu.waitForExistence(timeout: 3))
        // It rises out of the composer rather than dropping over the top of it.
        XCTAssertGreaterThan(app.textFields["moment-composer"].frame.minY, menu.frame.minY)
        XCTAssertTrue(line("先垫一句", in: app).exists)

        // A tap anywhere off the menu puts it away again.  It has to go through
        // a coordinate rather than an element: the invisible catcher that
        // closes the menu sits over the transcript while the menu is open.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)).tap()
        XCTAssertTrue(menu.waitForNonExistence(timeout: 3))
    }

    func testRepliedMomentStaysInTheTranscript() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        enterMoment(composer, "留在记录里")
        app.buttons["send-moment"].tap()
        XCTAssertTrue(bubble(in: app).waitForExistence(timeout: 10))
        // The outgoing line stays put once the reply lands beneath it.
        XCTAssertTrue(app.descendants(matching: .any)["murmur-message-0"].firstMatch.exists)
    }

    func testAFailedSendIsMarkedOnItsOwnBubbleAndCanBeSentAgain() throws {
        continueAfterFailure = false
        let app = launchApp(arguments: ["--murmur-fail-first-send"])
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        enterMoment(composer, "没发出去的一句")
        app.buttons["send-moment"].tap()

        // The failure belongs to the bubble: a coral mark beside it with a full
        // 44pt to press, and the row itself says what happened.
        let resend = app.buttons["resend-moment"]
        XCTAssertTrue(resend.waitForExistence(timeout: 15))

        // Everything below is about where the mark and its question sit, and
        // the keyboard now survives a send — so put it away first and let the
        // conversation come to rest.  Left up, the tap that dismisses the
        // question dismisses the keyboard too, and the next tap on the mark
        // lands where the mark was 300pt ago.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.2)).tap()
        Thread.sleep(forTimeInterval: 1.5)
        assertMinimumHitArea(resend)
        let row = app.descendants(matching: .any)["murmur-message-0"].firstMatch
        XCTAssertTrue(row.label.contains("没发出去的一句"))
        XCTAssertTrue(row.label.contains("发送失败"))
        XCTAssertTrue(row.label.contains("可以重新发送"))

        // And nothing takes over the strip above the composer to say it — that
        // line is for a draft that could not be prepared, and this is not one.
        XCTAssertFalse(app.descendants(matching: .any)["draft-error"].firstMatch.exists)

        // The mark asks before it sends, and the question stands on the mark
        // rather than somewhere near the row.  This is the assertion the whole
        // hand-placed card exists for: `popover` and `confirmationDialog` both
        // anchored to the row instead, which put the card level with the top of
        // a tall photo — a good 60pt clear of the mark — so a loose bound here
        // would not have caught it.
        resend.tap()
        let card = app.descendants(matching: .any)["resend-question"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        // The card's foot is 8pt above the mark; what is measured here is its
        // accessibility frame, which is the content box inside the card's 16pt
        // padding, so a correct placement reads as ~24.  The anchoring this
        // replaced put it ~70 clear of the mark, which is what the bound has to
        // separate — not a hair's breadth either side of 24.
        XCTAssertLessThan(
            resend.frame.minY - card.frame.maxY, 32,
            "the question did not come to rest on the mark"
        )
        XCTAssertGreaterThan(resend.frame.minY - card.frame.maxY, 0, "the question covered the mark")

        // Backing out of it leaves the row exactly as it was.  Dismissing goes
        // through a tap away rather than a cancel button, the way the
        // add-photo menu closes.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.25)).tap()
        XCTAssertTrue(card.waitForNonExistence(timeout: 5))
        XCTAssertTrue(resend.exists)
        // The mark has to be reachable the moment the card is gone, not once
        // some invisible catcher has finished animating off it.
        XCTAssertTrue(resend.isHittable)

        // Going through with it sends that same row again rather than adding a
        // second copy of it.
        resend.tap()
        let confirm = app.buttons["confirm-resend"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        let reply = app.descendants(matching: .any)["murmur-message-1"].firstMatch
        XCTAssertTrue(reply.waitForExistence(timeout: 15))
        XCTAssertTrue(resend.waitForNonExistence(timeout: 5))
        XCTAssertFalse(
            app.descendants(matching: .any)["murmur-message-0"].firstMatch.label.contains("发送失败")
        )
        XCTAssertEqual(
            app.descendants(matching: .any).matching(
                NSPredicate(format: "label CONTAINS %@", "没发出去的一句")
            ).count,
            1
        )
    }

    func testAPickedPhotoWaitsInTheComposerAndCanBeTakenBackOff() throws {
        continueAfterFailure = false
        let app = launchApp()
        XCTAssertTrue(app.textFields["moment-composer"].waitForExistence(timeout: 5))
        try pickFirstLibraryPhoto(in: app)

        // It waits inside the composer at thumbnail size, low on the screen,
        // rather than spreading itself across the transcript.
        let draft = app.buttons["draft-photo"]
        XCTAssertTrue(draft.waitForExistence(timeout: 20))
        XCTAssertLessThan(draft.frame.height, 120)
        XCTAssertGreaterThan(draft.frame.minY, app.windows.firstMatch.frame.height * 0.5)

        // Tapping it is how you see it properly; closing comes back here.
        draft.tap()
        let close = app.buttons["close-photo"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        XCTAssertTrue(draft.waitForExistence(timeout: 5))

        // And the cross takes it back off.
        app.buttons["remove-draft-photo"].tap()
        XCTAssertTrue(draft.waitForNonExistence(timeout: 5))
    }

    func testASentPhotoOpensAgainFromTheTranscript() throws {
        continueAfterFailure = false
        let app = launchApp()
        XCTAssertTrue(app.textFields["moment-composer"].waitForExistence(timeout: 5))
        try pickFirstLibraryPhoto(in: app)
        XCTAssertTrue(app.buttons["draft-photo"].waitForExistence(timeout: 20))

        app.buttons["send-moment"].tap()
        XCTAssertTrue(bubble(in: app).waitForExistence(timeout: 20))

        let sent = app.descendants(matching: .any)["murmur-message-0"].firstMatch
        XCTAssertTrue(sent.exists)
        sent.tap()
        XCTAssertTrue(app.buttons["close-photo"].waitForExistence(timeout: 5))
    }

    private func pickFirstLibraryPhoto(in app: XCUIApplication) throws {
        app.buttons["添加照片"].tap()
        XCTAssertTrue(app.buttons["从照片中选择"].waitForExistence(timeout: 3))
        app.buttons["从照片中选择"].tap()
        let firstPhoto = app.images.matching(identifier: "PXGGridLayout-Info").firstMatch
        guard firstPhoto.waitForExistence(timeout: 20) else {
            throw XCTSkip("This simulator's photo library is empty")
        }
        // The grid cells report as not hittable behind the picker's own
        // chrome, so the tap goes through a coordinate.
        Thread.sleep(forTimeInterval: 1)
        firstPhoto.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
    }

    private func bubble(in app: XCUIApplication) -> XCUIElement {
        // The transcript numbers every row, so Murmur's first reply is the row
        // after the outgoing message rather than a bubble index of its own.
        let identified = app.descendants(matching: .any)["murmur-message-1"].firstMatch
        if identified.exists { return identified }
        return line("这一刻，我收到了。", in: app)
    }

    /// The transcript row carrying this text.
    ///
    /// A message is one accessibility element with a spoken label — 「你说：…」 —
    /// so the raw line is not an element of its own to look up.  Matching on
    /// the label is what survives that, and it is also what a person hears.
    private func line(_ text: String, in app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", text))
            .firstMatch
    }

    private func enterMoment(_ composer: XCUIElement, _ text: String) {
        composer.tap()
        composer.typeText(text)
    }

    /// Every test but the cold-launch one starts from an empty transcript:
    /// the history is persisted now, so without the reset each test would read
    /// whatever the one before it happened to say.
    private func launchApp(arguments: [String] = []) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--murmur-ui-testing", "--murmur-reset-transcript"] + arguments
        app.launch()
        return app
    }

    private func assertMinimumHitArea(_ element: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertGreaterThanOrEqual(element.frame.width, 44, file: file, line: line)
        XCTAssertGreaterThanOrEqual(element.frame.height, 44, file: file, line: line)
    }

    /// A photo waiting in the draft must not lift the composer off the
    /// keyboard.  The tile floats over the transcript rather than growing the
    /// inset bar — growing the bar joined the safe-area accounting the
    /// keyboard lift lives in, and SwiftUI added the overflow back on top,
    /// leaving a band of paper between field and keyboard.  The field-to-keyboard
    /// distance without a photo measures ~70pt here; the regression took it
    /// past 115.
    func testDraftPhotoDoesNotLiftComposerOffTheKeyboard() throws {
        let app = launchApp(arguments: ["--murmur-stub-photo"])
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["draft-photo"].waitForExistence(timeout: 10))

        composer.tap()
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 1.0)
        XCTAssertLessThan(keyboard.frame.minY - composer.frame.maxY, 95)
    }

    // MARK: - 当年今日

    /// The four authorization states each have their own UI, and the stub
    /// drives them without the system photo library: a wrong PHFetchOptions
    /// predicate or a missing branch here fails silently on a real phone, so
    /// these are the only honest checks.
    func testOnThisDayAsksInContextBeforeReadingTheLibrary() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday-ask"])
        openOnThisDay(in: app)
        XCTAssertTrue(app.buttons["onthisday-allow"].waitForExistence(timeout: 5))
        app.buttons["onthisday-allow"].tap()
        XCTAssertTrue(onThisDayPhoto(in: app).waitForExistence(timeout: 5))
    }

    func testOnThisDayDeniedStateOffersSystemSettings() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday-denied"])
        openOnThisDay(in: app)
        XCTAssertTrue(app.buttons["onthisday-open-settings"].waitForExistence(timeout: 5))
    }

    /// The browser can no longer absent itself the way the old disc did — the
    /// calendar tab is always there — so limited access says what it is rather
    /// than letting the day look empty.
    func testLimitedAccessSaysSoInsteadOfLookingEmpty() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday-limited"])
        openOnThisDay(in: app)
        XCTAssertTrue(app.buttons["onthisday-open-settings"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["只能看到你选的那几张"].exists)
    }

    /// A blank day is not a dead end any more: with nothing from this day in
    /// any earlier year, the shelf starts on album photos and the card says so
    /// rather than dressing an ordinary Tuesday up as an anniversary.
    func testBlankDayFallsBackToTheAlbum() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday-empty"])
        openOnThisDay(in: app)
        XCTAssertTrue(onThisDayPhoto(in: app).waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["相册里翻到的"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["相册里还没有照片。"].exists)
    }

    /// The one honest dead end left: full access, and nothing anywhere.
    func testAnEmptyLibrarySaysSo() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday-barren"])
        openOnThisDay(in: app)
        XCTAssertTrue(app.staticTexts["相册里还没有照片。"].waitForExistence(timeout: 5))
    }

    /// Past the last photo from this day the shelf carries on with the album
    /// instead of looping back to the first card.
    func testTheShelfCarriesOnPastTheDaysOwnPhotos() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday"])
        openOnThisDay(in: app)
        let photo = readyOnThisDayPhoto(in: app)
        XCTAssertTrue(app.staticTexts["去年的今天"].waitForExistence(timeout: 5))

        // Three photos from this day, then the album.  A shelf that wrapped
        // would be back on 去年的今天 by the fourth swipe.
        for _ in 0..<3 { photo.swipeDown() }
        XCTAssertTrue(app.staticTexts["相册里翻到的"].waitForExistence(timeout: 5))
    }

    /// One photo at a time: down moves to the next year, up carries the photo
    /// into its own room.  Both directions are gated — |dy| > 60pt and clearly
    /// vertical — which the stubbed shelf makes repeatable.
    func testOnThisDaySwipesBetweenYearsAndOpensTheRoom() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday"])
        openOnThisDay(in: app)
        let photo = readyOnThisDayPhoto(in: app)
        XCTAssertTrue(app.staticTexts["去年的今天"].waitForExistence(timeout: 5))

        photo.swipeDown()
        XCTAssertTrue(app.staticTexts["2 年前的今天"].waitForExistence(timeout: 5))

        openPhotoRoom(in: app)
        // And nowhere near the conversation: the composer never sees it.
        XCTAssertFalse(app.buttons["draft-photo"].exists)
    }

    // MARK: - 照片房间

    /// The room's whole point: the server reads the photo, says what it thinks
    /// the person came to say, and offers three ways in.  Picking one puts it
    /// in the field rather than sending it — the person still decides.
    func testThePhotoRoomShowsTheReadingAndItsThreeOpeners() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday"])
        openOnThisDay(in: app)
        openPhotoRoom(in: app)

        XCTAssertTrue(line("这是……刚下过雨？", in: app).waitForExistence(timeout: 10))
        let opener = app.buttons["opener-0"]
        XCTAssertTrue(opener.waitForExistence(timeout: 5))
        opener.tap()

        let composer = app.textFields["photo-room-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertEqual(composer.value as? String, "那天的天气")
        // Picked up, not sent: nothing has left yet.
        XCTAssertTrue(app.buttons["opener-0"].exists)
    }

    /// Saying something closes the doors and gets an answer back — and none of
    /// it leaks into the conversation, which is the whole point of the archive.
    func testSayingSomethingInTheRoomAnswersAndStaysOutOfTheChat() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday"])
        openOnThisDay(in: app)
        openPhotoRoom(in: app)

        // Through an opener rather than the keyboard: it is the way in the
        // screen is built around, and it keeps the test off a software
        // keyboard whose appearance is its own source of flake.
        let opener = app.buttons["opener-0"]
        XCTAssertTrue(opener.waitForExistence(timeout: 10))
        opener.tap()
        app.buttons["photo-room-send"].tap()

        XCTAssertTrue(line("这一刻，我收到了。", in: app).waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["opener-0"].exists)
        app.buttons["close-photo-room"].tap()
        // Wait for the cover to be gone before looking for the room's words,
        // or this only asks whether the fade had finished.
        let roomGone = expectation(
            for: NSPredicate(format: "exists == false"),
            evaluatedWith: app.buttons["close-photo-room"]
        )
        wait(for: [roomGone], timeout: 5)
        // Back on 当年今日's calendar, and today now carries a mark.
        XCTAssertTrue(app.buttons["onthisday-entry"].waitForExistence(timeout: 5))
        // The conversation is untouched: still on its empty state, with no row
        // of the room's anywhere in it.
        app.buttons["tab-chat"].tap()
        XCTAssertTrue(app.staticTexts["发来眼前的一刻。"].waitForExistence(timeout: 5))
        XCTAssertFalse(line("那天的天气", in: app).exists)
    }

    /// A reading that never lands says why and offers the same upload again,
    /// instead of leaving the room staring at a photo in silence.
    func testAFailedReadingOffersToTryAgain() throws {
        let app = launchApp(arguments: [
            "--murmur-stub-onthisday", "--murmur-stub-reading-fails"
        ])
        openOnThisDay(in: app)
        openPhotoRoom(in: app)

        XCTAssertTrue(app.buttons["retry-photo-room"].waitForExistence(timeout: 10))
        // And it says why, rather than offering a button with no reason on it.
        XCTAssertTrue(line("暂时没有连上 Murmur。", in: app).exists)
    }

    /// Closing the sheet while the photo is dissolving calls the send off.
    /// The dissolve runs for most of a second and the close button stays live
    /// for all of it, so the wait has to be cancellable: a moment created
    /// behind someone who just tapped 关闭 is the one failure this feature
    /// cannot have.  `--murmur-slow-dissolve` widens the window so the tap is
    /// not racing the animation.
    func testClosingDuringTheDissolveCallsTheSendOff() throws {
        let app = launchApp(arguments: ["--murmur-stub-onthisday", "--murmur-slow-dissolve"])
        openOnThisDay(in: app)
        let photo = readyOnThisDayPhoto(in: app)

        photo.swipeUp()
        app.buttons["close-onthisday"].tap()

        // Long enough to outlast the widened dissolve: if the wait still fired
        // its send, the room would have opened inside this window.
        XCTAssertFalse(
            app.descendants(matching: .any)["photo-room-photo"].waitForExistence(timeout: 6)
        )
    }

    /// Three screens are alive at once so each keeps its place, which makes
    /// "off screen" a claim that has to be checked rather than assumed: a
    /// VoiceOver reader on 聊天 must not be able to swipe into 设置's switches.
    func testTheTabsNotOnScreenAreOutOfReach() throws {
        let app = launchApp()
        XCTAssertTrue(app.buttons["tab-chat"].waitForExistence(timeout: 5))
        // Two things that exist only in 我的 and only in 当年今日.  Both are
        // near the top of their screen, so "not there" is about reachability
        // rather than about a Form row that has not been scrolled to yet.
        XCTAssertFalse(app.descendants(matching: .any)["build-stamp"].firstMatch.exists)
        XCTAssertFalse(app.buttons["onthisday-entry"].exists)

        app.buttons["tab-me"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["build-stamp"].firstMatch.waitForExistence(timeout: 5)
        )
        // The composer belongs to 聊天 and is now the one out of reach.
        XCTAssertFalse(app.textFields["moment-composer"].exists)
    }

    /// The pill is one view that travels rather than three that blink, which
    /// is a claim about drawing.  What a test can hold is the state underneath
    /// it: exactly one stop is selected, and it is the one that was pressed.
    func testPressingAStopMovesTheSelectionToIt() throws {
        let app = launchApp()
        XCTAssertTrue(app.buttons["tab-chat"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["tab-chat"].isSelected)
        XCTAssertFalse(app.buttons["tab-onThisDay"].isSelected)

        app.buttons["tab-onThisDay"].tap()
        XCTAssertTrue(app.buttons["onthisday-entry"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["tab-onThisDay"].isSelected)
        XCTAssertFalse(app.buttons["tab-chat"].isSelected)
        XCTAssertFalse(app.buttons["tab-me"].isSelected)
    }

    /// The photo card surfaces as an image element once its picture is in;
    /// matching any type keeps the test out of SwiftUI's element-type choices.
    private func onThisDayPhoto(in app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any)["onthisday-photo"]
    }

    /// The card exists a beat before its pixels do, and 上滑 is not an offer
    /// until they are in — the gesture is silently ignored, which reads in a
    /// test as "the room never opened".  The card says which state it is in;
    /// this waits for it to stop saying 正在载入.
    @discardableResult
    private func readyOnThisDayPhoto(in app: XCUIApplication) -> XCUIElement {
        let photo = onThisDayPhoto(in: app)
        XCTAssertTrue(photo.waitForExistence(timeout: 5))
        let loaded = expectation(
            for: NSPredicate(format: "NOT (label CONTAINS %@)", "正在载入"),
            evaluatedWith: photo
        )
        wait(for: [loaded], timeout: 10)
        return photo
    }

    /// 当年今日, opened the way a person opens it: the tab is a calendar, and
    /// the browser is behind the card under it.
    private func openOnThisDay(in app: XCUIApplication) {
        let tab = app.buttons["tab-onThisDay"]
        XCTAssertTrue(tab.waitForExistence(timeout: 5))
        tab.tap()
        let entry = app.buttons["onthisday-entry"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
    }

    /// The room, opened the way a person opens it.
    private func openPhotoRoom(in app: XCUIApplication) {
        readyOnThisDayPhoto(in: app).swipeUp()
        XCTAssertTrue(
            app.descendants(matching: .any)["photo-room-photo"].waitForExistence(timeout: 10)
        )
    }

}
