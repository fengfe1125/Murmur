import XCTest
import UIKit

@MainActor
final class MurmurUITests: XCTestCase {
    func testSingleMomentAndColdLaunchHasNoHistory() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--murmur-ui-testing"]
        app.launch()

        XCTAssertTrue(emptyMoment(in: app).waitForExistence(timeout: 5))
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 2))
        enterMoment(composer, "今天的风")
        app.buttons["send-moment"].tap()
        XCTAssertTrue(bubble(in: app).waitForExistence(timeout: 5))

        app.terminate()
        app.launch()
        XCTAssertTrue(emptyMoment(in: app).waitForExistence(timeout: 5))
        XCTAssertFalse(bubble(in: app).exists)
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
        app.launchArguments = ["--murmur-ui-testing"]
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

    func testVoiceOverLabelsDescribeTheWorkbench() throws {
        continueAfterFailure = false
        let app = launchApp(arguments: ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryLarge"])
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertEqual(composer.label, "这一刻的文字")
        XCTAssertEqual(app.buttons["send-moment"].label, "发送这一刻")
        XCTAssertEqual(app.descendants(matching: .any)["response-panel"].firstMatch.label, "当前回应区域")
        XCTAssertTrue(app.buttons["添加照片"].exists)
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "设置")).firstMatch.exists)
        XCTAssertTrue(emptyMoment(in: app).exists)
    }

    func testPortraitPhoneKeepsSingleColumnUntilAPhotoExists() throws {
        continueAfterFailure = false
        guard UIDevice.current.userInterfaceIdiom == .phone else { throw XCTSkip("iPhone portrait coverage") }
        XCUIDevice.shared.orientation = .portrait
        let app = launchApp()
        XCTAssertTrue(app.descendants(matching: .any)["response-panel"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.descendants(matching: .any)["visual-panel"].exists)
    }

    func testLandscapeShowsPhotoAndResponseColumns() throws {
        continueAfterFailure = false
        XCUIDevice.shared.orientation = .landscapeLeft
        defer { XCUIDevice.shared.orientation = .portrait }
        let app = launchApp()
        XCTAssertTrue(app.descendants(matching: .any)["response-panel"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["visual-panel"].firstMatch.waitForExistence(timeout: 5))
        if UIDevice.current.userInterfaceIdiom == .phone {
            XCTAssertLessThan(
                app.descendants(matching: .any)["visual-panel"].firstMatch.frame.midX,
                app.descendants(matching: .any)["response-panel"].firstMatch.frame.midX
            )
        }
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
        app.swipeUp()
        XCTAssertTrue(app.buttons["清空这一刻"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.buttons["删除账号与全部记忆"].waitForExistence(timeout: 2))
    }

    func testRegularWidthUsesWorkbenchPanels() throws {
        continueAfterFailure = false
        guard UIDevice.current.userInterfaceIdiom == .pad else { throw XCTSkip("iPad regular-width coverage") }
        let app = launchApp()
        XCTAssertTrue(app.descendants(matching: .any)["response-panel"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["visual-panel"].waitForExistence(timeout: 5))
        let response = app.descendants(matching: .any)["response-panel"].firstMatch
        let visual = app.descendants(matching: .any)["visual-panel"].firstMatch
        XCTAssertEqual(response.label, "当前回应区域")
        XCTAssertEqual(visual.label, "图片预览区域")
        let responseFrame = response.frame
        let visualFrame = visual.frame
        XCTAssertLessThan(visualFrame.midX, responseFrame.midX)
    }

    private func emptyMoment(in app: XCUIApplication) -> XCUIElement {
        app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "发来眼前的一刻")).firstMatch
    }

    private func bubble(in app: XCUIApplication) -> XCUIElement {
        let identified = app.descendants(matching: .any)["murmur-bubble-0"].firstMatch
        if identified.exists { return identified }
        return app.staticTexts["这一刻，我收到了。"]
    }

    private func enterMoment(_ composer: XCUIElement, _ text: String) {
        composer.tap()
        composer.typeText(text)
    }

    private func launchApp(arguments: [String] = []) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--murmur-ui-testing"] + arguments
        app.launch()
        return app
    }

    private func assertMinimumHitArea(_ element: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertGreaterThanOrEqual(element.frame.width, 44, file: file, line: line)
        XCTAssertGreaterThanOrEqual(element.frame.height, 44, file: file, line: line)
    }
}
