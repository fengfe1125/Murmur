#if DEBUG
import SwiftUI
import UIKit

/// A flight recorder for the keyboard, for debug builds only.
///
/// The gap between the field and the conversation reproduces on a phone and
/// never in the Simulator, and guessing at the difference from here has not
/// worked.  This writes the numbers that matter — what the keyboard said it was
/// doing, and what the layout actually did afterwards — to a file inside the
/// app container, where `devicectl device copy from` can fetch them without the
/// person having to read anything off the screen or reproduce on cue.
@MainActor
enum MurmurDiagnostics {
    /// Caches, so it is the system's to reclaim and never counts as user data.
    static let fileURL: URL = FileManager.default
        .urls(for: .cachesDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("murmur-keyboard.log")

    private static var started = false

    static func record(_ line: String) {
        let stamp = Self.formatter.string(from: Date())
        append("\(stamp)  \(line)\n")
    }

    /// Called once the transcript is on screen; logs every keyboard transition
    /// together with the safe area the layout ended up with.
    static func startRecordingKeyboard() {
        guard !started else { return }
        started = true
        append("\n=== launched \(Self.formatter.string(from: Date())) ===\n")
        let center = NotificationCenter.default
        for name in [
            UIResponder.keyboardWillShowNotification,
            UIResponder.keyboardWillChangeFrameNotification,
            UIResponder.keyboardWillHideNotification,
            UIResponder.keyboardDidHideNotification
        ] {
            center.addObserver(forName: name, object: nil, queue: .main) { note in
                let end = (note.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? NSValue)?.cgRectValue
                MainActor.assumeIsolated {
                    record("\(short(name)) end=\(brief(end)) composerTop=\(String(format: "%.0f", composerTop))")
                    trackComposer()
                    // The inset that matters is the one after the layout has
                    // settled, which is a turn later than the notification.
                    Task { @MainActor in
                        try? await Task.sleep(for: .milliseconds(450))
                        record("   settled after \(short(name)): composerTop=\(String(format: "%.0f", composerTop))")
                    }
                }
            }
        }
    }

    /// The composer's position on screen, reported by the layout itself.
    /// The window's own `safeAreaInsets.bottom` stays at the home indicator
    /// whatever the keyboard does — it was the wrong thing to measure.
    static var composerTop: CGFloat = -1 {
        didSet {
            guard tracking, composerTop != oldValue else { return }
            append("      +\(String(format: "%.0f", Date().timeIntervalSince(trackingSince) * 1000))ms composerTop=\(String(format: "%.0f", composerTop))\n")
        }
    }

    /// Endpoints tell you where the layout landed, never whether it travelled
    /// with the keyboard or arrived late.  Every position the composer takes
    /// during a transition is logged, so the curve can be compared with the
    /// keyboard's own 0.25s.
    private static var tracking = false
    private static var trackingSince = Date()

    private static func trackComposer() {
        trackingSince = Date()
        guard !tracking else { return }
        tracking = true
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(700))
            tracking = false
        }
    }

    private static func bottomInset() -> String {
        guard let window = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene })
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)
        else { return "?" }
        return String(format: "%.1f", window.safeAreaInsets.bottom)
    }

    private static func short(_ name: Notification.Name) -> String {
        name.rawValue
            .replacingOccurrences(of: "UIKeyboard", with: "")
            .replacingOccurrences(of: "Notification", with: "")
    }

    private static func brief(_ rect: CGRect?) -> String {
        guard let rect else { return "nil" }
        return String(format: "y=%.0f h=%.0f", rect.minY, rect.height)
    }

    private static let formatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss.SSS"
        return formatter
    }()

    private static func append(_ text: String) {
        // Buffered, and flushed twice a second: the keyboard transition now
        // moves the layout every frame, and an open/write/close on the main
        // thread for every one of those frames was itself enough to drop
        // them.  Losing half a second of trail when the app dies is a fair
        // price for not causing the stutter being recorded.
        buffer += text
        guard !flushScheduled else { return }
        flushScheduled = true
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(500))
            flushScheduled = false
            let chunk = buffer
            buffer = ""
            guard let data = chunk.data(using: .utf8) else { return }
            if let handle = try? FileHandle(forWritingTo: fileURL) {
                defer { try? handle.close() }
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: data)
            } else {
                try? data.write(to: fileURL)
            }
        }
    }

    private static var buffer = ""
    private static var flushScheduled = false
}
#endif
