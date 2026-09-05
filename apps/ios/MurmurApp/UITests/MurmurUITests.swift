import XCTest
import UIKit

@MainActor
final class MurmurUITests: XCTestCase {
    /// 聊天页只留一张小卡片：暂停和下一首在手边，其余的在那一整屏里。
    func testTheChatCardPausesAndSkipsWithoutLeavingTheConversation() throws {
        continueAfterFailure = false
        let app = launchApp(arguments: ["--murmur-stub-netease-playing"])
        let status = app.staticTexts["已连接，正在一起听"]
        XCTAssertTrue(status.waitForExistence(timeout: 5))

        let pause = app.buttons["暂停一起听"]
        let next = app.buttons["下一首"]
        XCTAssertTrue(pause.exists)
        XCTAssertTrue(next.exists)
        assertMinimumHitArea(settled(pause))
        assertMinimumHitArea(settled(next))
        // 那个省略号弹层没有了，它的四行都搬进了 tab。
        XCTAssertFalse(app.buttons["更多一起听操作"].exists)
        // 卡片是小的：它不该霸占整条顶栏。
        XCTAssertLessThan(pause.frame.maxX, app.windows.firstMatch.frame.width - 40)
        // 聊天还在下面，没有被顶掉。
        XCTAssertTrue(app.textFields["moment-composer"].exists)

        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Listen Together · Chat card"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// 点卡片本体就是进「一起听」，两边说的是同一首歌。
    func testTappingTheCardOpensTheListenTogetherTab() throws {
        continueAfterFailure = false
        let app = launchApp(arguments: ["--murmur-stub-netease-playing"])
        XCTAssertTrue(app.staticTexts["已连接，正在一起听"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["tab-chat"].isSelected)

        app.buttons["打开一起听"].firstMatch.tap()
        XCTAssertTrue(app.buttons["tab-listenTogether"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["tab-listenTogether"].isSelected)
        // 那四个搬过来的动作，现在都在一屏之内够得着。
        for item in ["上一首", "下一首", "在网易云打开", "结束一起听"] {
            XCTAssertTrue(app.buttons[item].waitForExistence(timeout: 3), item)
        }
        XCTAssertTrue(app.staticTexts["花海"].exists)
    }

    func testListenTogetherWaitingPausedAndSyncingStatesAreDeterministic() throws {
        for (argument, text, action) in [
            ("--murmur-stub-netease-waiting", "等待加入 / 点此打开网易云邀请", "打开网易云一起听邀请"),
            ("--murmur-stub-netease-paused", "房间仍保持连接", "继续一起听"),
            ("--murmur-stub-netease-syncing", "暂停同步中…", "正在同步一起听操作"),
        ] {
            let app = launchApp(arguments: [argument])
            XCTAssertTrue(app.buttons["tab-listenTogether"].waitForExistence(timeout: 5))
            app.buttons["tab-listenTogether"].tap()
            XCTAssertTrue(app.staticTexts[text].waitForExistence(timeout: 5), argument)
            let control = app.buttons[action]
            XCTAssertTrue(control.exists, argument)
            assertMinimumHitArea(control)
            app.terminate()
        }
    }

    func testListenTogetherCommandFailureStaysOnTheScreenAndCanRetry() throws {
        continueAfterFailure = false
        let app = launchApp(arguments: ["--murmur-stub-netease-command-fails"])
        XCTAssertTrue(app.buttons["tab-listenTogether"].waitForExistence(timeout: 5))
        app.buttons["tab-listenTogether"].tap()
        let pause = app.buttons["暂停一起听"]
        XCTAssertTrue(pause.waitForExistence(timeout: 5))
        pause.tap()
        XCTAssertTrue(app.staticTexts["这次没有同步成功"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["重试上一次一起听操作"].exists)
        XCTAssertFalse(app.alerts["网易云音乐"].exists)
    }

    func testListenTogetherTerminalAndOfflineStatesStayDistinct() throws {
        var app = launchApp(arguments: ["--murmur-stub-netease-room-failed"])
        XCTAssertTrue(app.buttons["tab-listenTogether"].waitForExistence(timeout: 5))
        app.buttons["tab-listenTogether"].tap()
        XCTAssertTrue(app.staticTexts["邀请已过期，点此重试"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["重新创建一起听邀请"].exists)
        // 邀请过期时切歌切给谁听？这两颗键不该在。
        XCTAssertFalse(app.buttons["上一首"].exists)
        XCTAssertFalse(app.buttons["下一首"].exists)
        app.terminate()

        app = launchApp(arguments: ["--murmur-stub-netease-offline"])
        XCTAssertTrue(app.buttons["tab-listenTogether"].waitForExistence(timeout: 5))
        app.buttons["tab-listenTogether"].tap()
        XCTAssertTrue(app.staticTexts["Murmur 连接异常"].waitForExistence(timeout: 5))
        let disabled = app.buttons["Murmur 连接异常，控制暂不可用"]
        XCTAssertTrue(disabled.exists)
        XCTAssertFalse(disabled.isEnabled)
    }

    func testNeteaseShareConfirmationUsesTrustedPreviewAndDoesNotStartARoom() throws {
        continueAfterFailure = false
        let app = launchApp(arguments: ["--murmur-stub-netease-share"])
        let title = app.staticTexts["把这首歌发给 Murmur？"]
        let review = app.buttons["在网易云里核对"]
        let cancel = app.buttons["取消"]
        let confirm = app.buttons["confirm-netease-share"]
        let window = app.windows.firstMatch

        XCTAssertTrue(title.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["花海"].exists)
        XCTAssertTrue(app.staticTexts["周杰伦"].exists)
        XCTAssertTrue(review.exists)
        XCTAssertTrue(cancel.exists)
        XCTAssertTrue(confirm.exists)
        XCTAssertFalse(app.staticTexts["等待加入 / 点此打开网易云邀请"].exists)

        let screen = window.frame
        XCTAssertTrue(
            waitForElementFrame(title, timeout: 5) {
                $0.minY >= screen.height * 0.57 && $0.minY <= screen.height * 0.75
            },
            "the confirmation sheet did not settle into its compact detent"
        )
        XCTAssertGreaterThanOrEqual(
            title.frame.minY,
            screen.height * 0.57,
            "the compact confirmation sheet started too high"
        )
        XCTAssertLessThanOrEqual(
            screen.maxY - confirm.frame.maxY,
            64,
            "the confirmation sheet left a large empty tail below its actions"
        )
        XCTAssertTrue(review.isHittable)
        XCTAssertTrue(screen.contains(review.frame))
        for control in [cancel, confirm] {
            XCTAssertTrue(control.isHittable)
            XCTAssertTrue(screen.contains(control.frame))
        }

        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "NetEase · Share Confirmation"
        attachment.lifetime = .keepAlways
        add(attachment)

        confirm.tap()
        XCTAssertTrue(title.waitForNonExistence(timeout: 5))
        let sentCard = app.descendants(matching: .any)["murmur-message-0"].firstMatch
        XCTAssertTrue(sentCard.waitForExistence(timeout: 5))
        XCTAssertTrue(sentCard.label.contains("花海"))
        XCTAssertTrue(sentCard.label.contains("周杰伦"))
        XCTAssertFalse(app.staticTexts["等待加入 / 点此打开网易云邀请"].exists)

        let sentAttachment = XCTAttachment(screenshot: app.screenshot())
        sentAttachment.name = "NetEase · Sent Vinyl Card"
        sentAttachment.lifetime = .keepAlways
        add(sentAttachment)
    }

    func testNeteaseShareConfirmationDarkAccessibilityXXXLKeepsActionsReachable() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = [
            "--murmur-ui-testing",
            "--murmur-reset-transcript",
            "--murmur-stub-keyboard-overlap",
            "--murmur-stub-netease-share",
            "--murmur-ui-test-dark",
        ]
        app.launchEnvironment["AppleInterfaceStyle"] = "Dark"
        app.launchEnvironment["UIPreferredContentSizeCategoryName"] =
            "UICTContentSizeCategoryAccessibilityExtraExtraExtraLarge"
        app.launch()

        let title = app.staticTexts["把这首歌发给 Murmur？"]
        let track = app.staticTexts["花海"]
        let artist = app.staticTexts["周杰伦"]
        let cancel = app.buttons["取消"]
        let confirm = app.buttons["confirm-netease-share"]
        let screen = app.windows.firstMatch.frame

        XCTAssertTrue(title.waitForExistence(timeout: 5))
        XCTAssertTrue(track.exists)
        XCTAssertTrue(artist.exists)
        for element in [title, track, artist] {
            XCTAssertGreaterThanOrEqual(element.frame.minX, screen.minX)
            XCTAssertLessThanOrEqual(element.frame.maxX, screen.maxX)
        }

        // Exercise the sheet's own ScrollView even when XCTest reports an
        // offscreen descendant as hittable.
        app.swipeUp()
        for _ in 0..<4 where !confirm.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(cancel.isHittable)
        XCTAssertTrue(confirm.isHittable)
        for control in [cancel, confirm] {
            XCTAssertTrue(screen.contains(control.frame))
        }

        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "NetEase · Share Confirmation · Dark Accessibility XXXL"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testListenTogetherDarkAccessibilityXXXLKeepsControlsReachable() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = [
            "--murmur-ui-testing",
            "--murmur-reset-transcript",
            "--murmur-stub-keyboard-overlap",
            "--murmur-stub-netease-playing",
            "--murmur-ui-test-dark",
        ]
        app.launchEnvironment["AppleInterfaceStyle"] = "Dark"
        app.launchEnvironment["UIPreferredContentSizeCategoryName"] =
            "UICTContentSizeCategoryAccessibilityExtraExtraExtraLarge"
        app.launch()

        // 聊天页那半：这是最容易破的几何——两行字加两个 44pt 的目标，在 XXXL
        // 下必须仍然完整地待在窗口里。
        let pause = app.buttons["暂停一起听"]
        let next = app.buttons["下一首"]
        XCTAssertTrue(pause.waitForExistence(timeout: 5))
        XCTAssertTrue(next.exists)
        XCTAssertTrue(pause.isHittable)
        XCTAssertTrue(next.isHittable)
        assertMinimumHitArea(settled(pause))
        assertMinimumHitArea(settled(next))
        XCTAssertTrue(app.windows.firstMatch.frame.contains(pause.frame))
        XCTAssertTrue(app.windows.firstMatch.frame.contains(next.frame))

        add({
            let shot = XCTAttachment(screenshot: app.screenshot())
            shot.name = "Listen Together · Chat card · Dark AX XXXL"
            shot.lifetime = .keepAlways
            return shot
        }())

        // tab 那半：四格之后每格更窄，标签在 XXXL 下不许把整条 bar 撑破。
        let stop = app.buttons["tab-listenTogether"]
        XCTAssertTrue(stop.exists)
        assertMinimumHitArea(stop)
        XCTAssertTrue(app.windows.firstMatch.frame.contains(stop.frame))
        stop.tap()

        let primary = app.buttons["暂停一起听"]
        XCTAssertTrue(primary.waitForExistence(timeout: 5))
        assertMinimumHitArea(primary)
        XCTAssertTrue(app.windows.firstMatch.frame.contains(primary.frame))
        let end = app.buttons["结束一起听"]
        XCTAssertTrue(end.exists)
        XCTAssertTrue(app.windows.firstMatch.frame.contains(end.frame))

        add({
            let shot = XCTAttachment(screenshot: app.screenshot())
            shot.name = "Listen Together · Tab · Dark AX XXXL"
            shot.lifetime = .keepAlways
            return shot
        }())
    }

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
        app.launchArguments = ["--murmur-ui-testing", "--murmur-stub-keyboard-overlap"]
        app.launch()
        XCTAssertTrue(line("今天的风", in: app).waitForExistence(timeout: 10))
    }

    func testReturningToChatRepeatedlyShowsTheLatestLongTranscriptWithoutScrolling() throws {
        continueAfterFailure = false
        let app = launchApp(arguments: ["--murmur-seed-long-transcript"])
        let newest = line("回到聊天应该立刻看见我", in: app)
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        assertLatestTranscriptRowIsVisible(newest, above: composer, in: app, cycle: 0)

        for cycle in 1...30 {
            if cycle.isMultiple(of: 2) {
                app.buttons["tab-me"].tap()
                XCTAssertTrue(
                    app.descendants(matching: .any)["build-stamp"].firstMatch.waitForExistence(timeout: 3),
                    "cycle \(cycle): 我的 did not appear"
                )
            } else {
                app.buttons["tab-onThisDay"].tap()
                XCTAssertTrue(
                    app.buttons["onthisday-entry"].waitForExistence(timeout: 3),
                    "cycle \(cycle): 当年今日 did not appear"
                )
            }

            app.buttons["tab-chat"].tap()
            XCTAssertTrue(composer.waitForExistence(timeout: 3), "cycle \(cycle): chat did not appear")
            assertLatestTranscriptRowIsVisible(newest, above: composer, in: app, cycle: cycle)
        }
    }

    func testLandscapeComposerRemainsHittableAndCanSend() throws {
        continueAfterFailure = false
        XCUIDevice.shared.orientation = .landscapeLeft
        defer { XCUIDevice.shared.orientation = .portrait }
        // The deterministic 301pt geometry models a portrait software
        // keyboard. This compatibility test deliberately rotates the device,
        // so let UIKeyboardLayoutGuide provide the real landscape height.
        let app = launchApp(stubKeyboard: false)
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertEqual(composer.label, "这一刻的文字")
        enterMoment(composer, "横屏这一刻")
        let send = app.buttons["send-moment"]
        XCTAssertTrue(send.waitForExistence(timeout: 2))
        XCTAssertTrue(
            send.isHittable,
            "send=\(send.frame) composer=\(composer.frame) window=\(app.windows.firstMatch.frame)"
        )
        assertMinimumHitArea(send)
        send.tap()
        XCTAssertTrue(bubble(in: app).waitForExistence(timeout: 5))
    }

    func testDarkAccessibilityXXXLKeepsPrimaryControlsReachable() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = [
            "--murmur-ui-testing",
            "--murmur-reset-transcript",
            "--murmur-stub-keyboard-overlap",
        ]
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
        // The four stops are named, and the one you are on says so.
        XCTAssertEqual(app.buttons["tab-chat"].label, "聊天")
        XCTAssertEqual(app.buttons["tab-listenTogether"].label, "一起听")
        XCTAssertEqual(app.buttons["tab-onThisDay"].label, "当年今日")
        XCTAssertEqual(app.buttons["tab-me"].label, "我的")
        XCTAssertTrue(app.buttons["tab-chat"].isSelected)
        // The order is a design decision, and nothing else would catch a
        // reshuffle: 聊天 · 一起听 · 当年今日 · 我的, left to right.
        let stops = ["tab-chat", "tab-listenTogether", "tab-onThisDay", "tab-me"]
        let xs = stops.map { app.buttons[$0].frame.minX }
        XCTAssertEqual(xs, xs.sorted(), "tab 栏的顺序变了")
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
        let restingComposerY = composer.frame.minY
        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))

        // Typing without tapping the field again is the whole point: `typeText`
        // on an element that lost keyboard focus fails, so these two sends in a
        // row are the guard against the composer resigning on submit.
        for line in ["第一句", "第二句"] {
            composer.typeText(line)
            app.buttons["send-moment"].tap()
            XCTAssertTrue(self.line(line, in: app).waitForExistence(timeout: 10))
            XCTAssertTrue(
                waitForComposerToRise(composer, from: restingComposerY, timeout: 3),
                "sending 「\(line)」 put the keyboard away"
            )
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
        let initialRestingComposerY = composer.frame.minY
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
        XCTAssertTrue(waitForComposerToRest(composer, at: initialRestingComposerY, timeout: 5))
        let restingComposer = composer.frame.minY
        let restingNewest = newest.frame.maxY

        // Asking for the keyboard back, rather than a first raise: it is being
        // put away one step above, and how long iOS takes to bring it out again
        // is not this test's business.  Waiting for it — instead of assuming a
        // budget — is what keeps the measurement below about the layout.
        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposer, timeout: 5),
                      "the keyboard did not come back when the field was reached for")
        XCTAssertLessThan(composer.frame.minY, restingComposer, "the keyboard did not raise the field")

        chat.tap()
        XCTAssertTrue(
            waitForComposerToRest(composer, at: restingComposer, timeout: 5),
            "tapping the conversation did not dismiss the keyboard"
        )
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

    func testDownwardDragDismissesTheChatKeyboardAndItCanReturn() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        let restingComposerY = composer.frame.minY
        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))

        let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.28))
        let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.38))
        start.press(forDuration: 0.05, thenDragTo: end)
        XCTAssertTrue(
            waitForComposerToRest(composer, at: restingComposerY, timeout: 5),
            "a deliberate downward transcript drag did not dismiss the keyboard"
        )

        composer.tap()
        XCTAssertTrue(
            waitForComposerToRise(composer, from: restingComposerY, timeout: 5),
            "the keyboard did not return after drag dismissal"
        )
    }

    func testSystemKeyboardGeometryTracksDismissAndRefocusWithoutABlankBand() throws {
        continueAfterFailure = false
        // This integration test needs an attached Simulator window with its
        // software keyboard enabled. Headless XCTest can focus an off-screen
        // virtual keyboard; the frame assertions intentionally reject that.
        let app = launchApp(stubKeyboard: false)
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        let restingComposerY = composer.frame.minY

        composer.tap()
        composer.typeText("系统键盘")
        XCTAssertTrue(waitForSoftwareKeyboard(in: app, visible: true, timeout: 5))
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))
        let gap = app.keyboards.firstMatch.frame.minY - composer.frame.maxY
        XCTAssertGreaterThanOrEqual(gap, 0)
        // XCTest's Keyboard frame starts below the 44pt prediction strip.
        // Together with the field's inner padding the normal distance is
        // about 70pt; the screenshot's actual pill-to-keyboard gap is 10pt.
        XCTAssertLessThan(gap, 90, "the composer left a blank band above the system keyboard")

        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)).tap()
        XCTAssertTrue(waitForSoftwareKeyboard(in: app, visible: false, timeout: 5))
        XCTAssertTrue(waitForComposerToRest(composer, at: restingComposerY, timeout: 5))

        composer.tap()
        XCTAssertTrue(waitForSoftwareKeyboard(in: app, visible: true, timeout: 5))
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))
    }

    func testDismissingTheKeyboardBringsALongConversationBackDown() throws {
        continueAfterFailure = false
        let app = launchApp()
        let composer = app.textFields["moment-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        let initialRestingComposerY = composer.frame.minY
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
        XCTAssertTrue(waitForComposerToRest(composer, at: initialRestingComposerY, timeout: 5))
        let restGap = composer.frame.minY - newest.frame.maxY

        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: initialRestingComposerY, timeout: 5))
        XCTAssertLessThanOrEqual(newest.frame.maxY, composer.frame.minY + 1)

        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)).tap()
        XCTAssertTrue(
            waitForComposerToRest(composer, at: initialRestingComposerY, timeout: 5),
            "tapping the conversation did not dismiss the keyboard"
        )
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

    /// Layout tests inject a known software-keyboard overlap and wait for the
    /// composer's actual accessibility frame. This cannot pass merely because
    /// XCTest left an off-screen Keyboard element in its tree.
    private func waitForComposerToRise(
        _ composer: XCUIElement,
        from restingY: CGFloat,
        timeout: TimeInterval
    ) -> Bool {
        waitForComposer(composer, timeout: timeout) {
            $0 < restingY - 100
        }
    }

    private func waitForComposerToRest(
        _ composer: XCUIElement,
        at restingY: CGFloat,
        timeout: TimeInterval
    ) -> Bool {
        waitForComposer(composer, timeout: timeout) {
            abs($0 - restingY) <= 2
        }
    }

    private func waitForComposer(
        _ composer: XCUIElement,
        timeout: TimeInterval,
        positionMatches: @escaping (CGFloat) -> Bool
    ) -> Bool {
        let predicate = NSPredicate { _, _ in
            composer.exists && positionMatches(composer.frame.minY)
        }
        let result = XCTWaiter.wait(
            for: [XCTNSPredicateExpectation(predicate: predicate, object: nil)],
            timeout: timeout
        )
        return result == .completed
    }

    private func waitForElementFrame(
        _ element: XCUIElement,
        timeout: TimeInterval,
        frameMatches: @escaping (CGRect) -> Bool
    ) -> Bool {
        let predicate = NSPredicate { _, _ in
            element.exists && frameMatches(element.frame)
        }
        return XCTWaiter.wait(
            for: [XCTNSPredicateExpectation(predicate: predicate, object: nil)],
            timeout: timeout
        ) == .completed
    }

    private func assertLatestTranscriptRowIsVisible(
        _ row: XCUIElement,
        above composer: XCUIElement,
        in app: XCUIApplication,
        cycle: Int,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let deadline = Date().addingTimeInterval(2)
        var visible = false
        repeat {
            let frame = row.frame
            let window = app.windows.firstMatch.frame
            visible = row.exists
                && !frame.isEmpty
                && frame.intersection(window).height > 1
                && frame.maxY <= composer.frame.minY + 1
            if visible { break }
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        } while Date() < deadline
        XCTAssertTrue(
            visible,
            "cycle \(cycle): latest row was not visible before any transcript gesture; row=\(row.frame) composer=\(composer.frame) window=\(app.windows.firstMatch.frame)",
            file: file,
            line: line
        )
    }

    private func waitForSoftwareKeyboard(
        in app: XCUIApplication,
        visible: Bool,
        timeout: TimeInterval
    ) -> Bool {
        let predicate = NSPredicate { _, _ in
            let screen = app.windows.firstMatch.frame
            let isOnscreen = app.keyboards.allElementsBoundByIndex.contains { keyboard in
                let frame = keyboard.frame
                return frame.minY.isFinite && !frame.isEmpty
                    && frame.intersection(screen).height > 1
            }
            return isOnscreen == visible
        }
        return XCTWaiter.wait(
            for: [XCTNSPredicateExpectation(predicate: predicate, object: nil)],
            timeout: timeout
        ) == .completed
    }

    /// Every test but the cold-launch one starts from an empty transcript:
    /// the history is persisted now, so without the reset each test would read
    /// whatever the one before it happened to say.
    private func launchApp(
        arguments: [String] = [],
        stubKeyboard: Bool = true
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "--murmur-ui-testing",
            "--murmur-reset-transcript",
        ] + (stubKeyboard ? ["--murmur-stub-keyboard-overlap"] : []) + arguments
        app.launch()
        return app
    }

    /// 量之前先等它不再动。
    ///
    /// 小卡片是从那颗 44pt 圆盘弹开成一张卡的（`ListenTogetherCard`），而
    /// `waitForExistence` 在弹簧还在走的时候就返回了。这不是尺寸本身的问题，
    /// 只是把动画时机从这条断言里排除掉，免得下次量到一个半路上的数字还得
    /// 重新查一遍。
    @discardableResult
    private func settled(_ element: XCUIElement, timeout: TimeInterval = 3) -> XCUIElement {
        var last = element.frame
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
            let now = element.frame
            if now == last { return element }
            last = now
        }
        return element
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

        let restingComposerY = composer.frame.minY
        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))
        XCTAssertEqual(
            restingComposerY - composer.frame.minY,
            229,
            accuracy: 12,
            "a draft photo added extra keyboard clearance"
        )
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
        XCTAssertTrue(
            app.buttons[archiveDayIdentifier(daysAgo: 0)].waitForExistence(timeout: 5)
        )
        // The conversation is untouched: still on its empty state, with no row
        // of the room's anywhere in it.
        app.buttons["tab-chat"].tap()
        XCTAssertTrue(app.staticTexts["发来眼前的一刻。"].waitForExistence(timeout: 5))
        XCTAssertFalse(line("那天的天气", in: app).exists)
    }

    func testTodaysArchivedRoomCanContinueTwiceWithoutReplacingTheFirstReply() throws {
        let app = launchApp(arguments: ["--murmur-seed-today-archive"])
        let tab = app.buttons["tab-onThisDay"]
        XCTAssertTrue(tab.waitForExistence(timeout: 5))
        tab.tap()
        let day = app.buttons[archiveDayIdentifier(daysAgo: 0)]
        XCTAssertTrue(day.waitForExistence(timeout: 8))
        day.tap()

        let composer = app.textFields["archive-day-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        let restingComposerY = composer.frame.minY
        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))
        composer.typeText("日期续聊第一句")
        app.buttons["archive-day-send"].tap()
        XCTAssertTrue(line("接住第一句", in: app).waitForExistence(timeout: 10))
        XCTAssertTrue(
            waitForComposerToRise(composer, from: restingComposerY, timeout: 3),
            "sending an archived line resigned its composer"
        )

        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)).tap()
        XCTAssertTrue(waitForComposerToRest(composer, at: restingComposerY, timeout: 5))

        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))
        composer.typeText("日期续聊第二句")
        app.buttons["archive-day-send"].tap()
        XCTAssertTrue(line("接住第二句", in: app).waitForExistence(timeout: 10))
        XCTAssertTrue(line("接住第一句", in: app).exists)
    }

    func testAHistoricalArchivedDayCanContinueAndKeepsItsEarlierAnswer() throws {
        let app = launchApp(arguments: ["--murmur-seed-historical-archive"])
        let tab = app.buttons["tab-onThisDay"]
        XCTAssertTrue(tab.waitForExistence(timeout: 5))
        tab.tap()
        let day = app.buttons[archiveDayIdentifier(daysAgo: 2)]
        XCTAssertTrue(day.waitForExistence(timeout: 8))
        day.tap()

        XCTAssertTrue(line("这是一条旧日期里的回答", in: app).waitForExistence(timeout: 5))
        let composer = app.textFields["archive-day-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        let restingComposerY = composer.frame.minY
        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))

        let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.30))
        let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.42))
        start.press(forDuration: 0.05, thenDragTo: end)
        XCTAssertTrue(
            waitForComposerToRest(composer, at: restingComposerY, timeout: 5),
            "a downward drag did not dismiss the historical-day keyboard"
        )

        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))
        composer.typeText("日期续聊第一句")
        app.buttons["archive-day-send"].tap()

        XCTAssertTrue(line("接住第一句", in: app).waitForExistence(timeout: 10))
        XCTAssertTrue(line("这是一条旧日期里的回答", in: app).exists)
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 3))

        let back = app.navigationBars.buttons.firstMatch
        XCTAssertTrue(back.waitForExistence(timeout: 3))
        back.tap()
        XCTAssertTrue(app.buttons["onthisday-entry"].waitForExistence(timeout: 5))
        XCTAssertFalse(composer.exists, "leaving an archive day retained its composer")
    }

    func testHistoricalContinuationFailureRestoresTheDraft() throws {
        let app = launchApp(arguments: [
            "--murmur-seed-historical-archive", "--murmur-fail-first-send"
        ])
        let tab = app.buttons["tab-onThisDay"]
        XCTAssertTrue(tab.waitForExistence(timeout: 5))
        tab.tap()
        let day = app.buttons[archiveDayIdentifier(daysAgo: 2)]
        XCTAssertTrue(day.waitForExistence(timeout: 8))
        day.tap()

        let composer = app.textFields["archive-day-composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        let restingComposerY = composer.frame.minY
        composer.tap()
        XCTAssertTrue(waitForComposerToRise(composer, from: restingComposerY, timeout: 5))
        composer.typeText("失败后还在")
        app.buttons["archive-day-send"].tap()

        XCTAssertTrue(line("暂时没有连上 Murmur。", in: app).waitForExistence(timeout: 10))
        XCTAssertEqual(composer.value as? String, "失败后还在")
        XCTAssertTrue(
            waitForComposerToRise(composer, from: restingComposerY, timeout: 3),
            "a failed continuation did not preserve focus"
        )
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
        // 一起听 is the fourth screen and obeys the same rule.  Asserted on
        // the words it actually draws: an identifier on a container may not
        // surface as a queryable element, and "the identifier is absent" would
        // then pass whether or not the screen is there.
        XCTAssertFalse(app.staticTexts["一起听还没有对你开放"].exists)

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
        XCTAssertFalse(app.buttons["tab-listenTogether"].isSelected)

        app.buttons["tab-onThisDay"].tap()
        XCTAssertTrue(app.buttons["onthisday-entry"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["tab-onThisDay"].isSelected)
        XCTAssertFalse(app.buttons["tab-chat"].isSelected)
        XCTAssertFalse(app.buttons["tab-me"].isSelected)
        XCTAssertFalse(app.buttons["tab-listenTogether"].isSelected)

        app.buttons["tab-listenTogether"].tap()
        XCTAssertTrue(app.staticTexts["一起听"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["tab-listenTogether"].isSelected)
        XCTAssertFalse(app.buttons["tab-onThisDay"].isSelected)
    }

    func testTheListenTogetherTabSaysSoWhenTheServerHasNotOpenedIt() throws {
        // No `--murmur-stub-netease-*` argument, so the stub leaves the room
        // experiment off.  The stop is still there — it does not appear and
        // disappear under people — and it explains itself instead of showing
        // controls that cannot work.
        let app = launchApp()
        XCTAssertTrue(app.buttons["tab-listenTogether"].waitForExistence(timeout: 5))
        app.buttons["tab-listenTogether"].tap()
        XCTAssertTrue(
            app.staticTexts["一起听还没有对你开放"].waitForExistence(timeout: 5)
        )
        XCTAssertFalse(app.buttons["暂停一起听"].exists)
        XCTAssertFalse(app.buttons["结束一起听"].exists)
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

    private func archiveDayIdentifier(daysAgo: Int) -> String {
        let calendar = Calendar(identifier: .gregorian)
        let date = calendar.date(byAdding: .day, value: -daysAgo, to: Date()) ?? Date()
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return "archive-day-\(formatter.string(from: date))"
    }

}
