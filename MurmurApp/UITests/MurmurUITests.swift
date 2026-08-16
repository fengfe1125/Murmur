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
        XCTAssertTrue(app.staticTexts["今天的风"].waitForExistence(timeout: 10))

        // The transcript lives on this device on purpose, so quitting the app
        // must not be a way of losing the conversation.
        app.terminate()
        app.launchArguments = ["--murmur-ui-testing"]
        app.launch()
        XCTAssertTrue(app.staticTexts["今天的风"].waitForExistence(timeout: 10))
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
        let settings = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "设置")).firstMatch
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
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "设置")).firstMatch.exists)
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

    func testReachingForTheFieldBringsTheNewestLineBackAboveTheKeyboard() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        for line in ["一", "二", "三", "四"] {
            composer.tap()
            composer.typeText(line)
            app.buttons["send-moment"].tap()
            XCTAssertTrue(app.staticTexts[line].waitForExistence(timeout: 10))
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
            XCTAssertTrue(app.staticTexts[line].waitForExistence(timeout: 10))
        }
        let newest = app.descendants(matching: .any)["murmur-message-3"].firstMatch
        XCTAssertTrue(newest.waitForExistence(timeout: 10))

        let chat = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3))
        chat.tap()
        Thread.sleep(forTimeInterval: 1.5)
        let restingComposer = composer.frame.minY
        let restingNewest = newest.frame.maxY

        composer.tap()
        Thread.sleep(forTimeInterval: 1.5)
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
            XCTAssertTrue(app.staticTexts[line].waitForExistence(timeout: 10))
        }
        let newest = app.descendants(matching: .any)["murmur-message-19"].firstMatch
        XCTAssertTrue(newest.waitForExistence(timeout: 10))
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
        let settings = app.descendants(matching: .any)["settings-button"].firstMatch
        XCTAssertTrue(settings.waitForExistence(timeout: 5))
        guard UIDevice.current.userInterfaceIdiom == .phone else { return }
        settings.tap()
        XCTAssertTrue(app.buttons["完成"].waitForExistence(timeout: 5))
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
        XCTAssertTrue(app.staticTexts["先垫一句"].exists)

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
        return app.staticTexts["这一刻，我收到了。"]
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
}
