import SwiftUI
import UIKit

/// How far the keyboard reaches into the screen, published on the keyboard's
/// own clock.
///
/// SwiftUI will do this by itself, and for one plain field it does it well.  It
/// does not survive this screen.  The composer sits in a `safeAreaInset` under a
/// scroll view that re-anchors itself whenever it is resized, so a keyboard
/// produces two motions — the inset SwiftUI applies, and the scroll correction
/// the anchor makes — and SwiftUI times them against its own idea of the
/// transition rather than the keyboard's.  Recorded at ten times slow motion,
/// the composer reached its resting place while the keyboard was still halfway
/// down the screen, and what showed between them was a band of bare paper the
/// height of the keyboard that had not left yet.  That band is the gap.
///
/// So the inset is taken away from SwiftUI (`ignoresSafeArea(.keyboard)`) and
/// driven from `keyboardWillChangeFrame` instead, with the duration and curve
/// the notification carries.  One clock, and nothing can outrun the keyboard.
@MainActor
final class MurmurKeyboardInset: ObservableObject {
    static let shared = MurmurKeyboardInset()

    /// What the keyboard takes from the bottom of the window, over and above
    /// the home indicator the composer already clears at rest.
    @Published private(set) var overlap: CGFloat = 0

    /// The window's resting bottom inset — the home indicator.  Sampled while
    /// the keyboard is away, because iOS drops it to zero while the keyboard is
    /// up, and reading it then would subtract nothing and leave the composer a
    /// home indicator too high.
    private var restingBottomInset: CGFloat = 0

    private init() {
        let center = NotificationCenter.default
        for name in [
            UIResponder.keyboardWillChangeFrameNotification,
            UIResponder.keyboardWillHideNotification
        ] {
            let hiding = name == UIResponder.keyboardWillHideNotification
            center.addObserver(forName: name, object: nil, queue: .main) { note in
                // The parts that matter are read here, on the posting thread,
                // so nothing non-Sendable crosses into the actor.
                let info = note.userInfo
                let end = (info?[UIResponder.keyboardFrameEndUserInfoKey] as? NSValue)?.cgRectValue
                let duration = info?[UIResponder.keyboardAnimationDurationUserInfoKey] as? Double
                let curve = info?[UIResponder.keyboardAnimationCurveUserInfoKey] as? Int
                MainActor.assumeIsolated {
                    Self.shared.apply(end: end, duration: duration, curve: curve, hiding: hiding)
                }
            }
        }
    }

    /// Ask for the keyboard once, before anyone reaches for the field.
    ///
    /// The keyboard lives in another process, and the first field in a session
    /// to ask for it waits while that process — and the input method inside
    /// it, which for Pinyin is not small — is loaded.  That wait is the
    /// "sometimes" in a keyboard that usually arrives at once: the first tap
    /// after launch pays it, and so does the first tap after iOS has reclaimed
    /// the keyboard behind a backgrounded app.  Nothing else on this screen
    /// can shorten it; the only thing that helps is having asked already.
    ///
    /// A field that takes first responder and gives it straight back inside
    /// one runloop turn asks for all of that without a keyboard ever being
    /// shown.
    func warm() {
        // Never while the keyboard is up: taking first responder from the
        // composer would put the keyboard away mid-sentence.
        guard overlap == 0, !warming, let window = Self.keyWindow else { return }
        warming = true
        defer { warming = false }
        // A field has to be in a window to become first responder at all, and
        // at zero size it is invisible for the one turn it spends there.
        let field = UITextField(frame: .zero)
        window.addSubview(field)
        field.becomeFirstResponder()
        field.resignFirstResponder()
        field.removeFromSuperview()
    }

    /// Set while the warm-up holds first responder.  Anything the keyboard
    /// says in that turn is about a keyboard nobody asked to see, and moving
    /// the composer for it would be a twitch on an idle screen.
    private var warming = false

    private func apply(end: CGRect?, duration: Double?, curve: Int?, hiding: Bool) {
        guard !warming, let end, let window = Self.keyWindow else { return }

        if overlap == 0 {
            restingBottomInset = window.safeAreaInsets.bottom
        }

        // The end frame arrives in screen coordinates, and the keyboard is not
        // always a slab across the bottom: floating and split keyboards, and a
        // second window on iPad, all reach less far or not at all.  What the
        // layout needs is the overlap with this window, never `end.height`.
        let inWindow = window.convert(end, from: nil)
        let reach = hiding ? 0 : max(0, window.bounds.maxY - inWindow.minY)
        // The composer already sits above the home indicator, so only the part
        // of the keyboard beyond it is new room to make.
        let target = max(0, reach - restingBottomInset)
        guard target != overlap else { return }

        // Not `withAnimation`.  SwiftUI's clock runs late here — the scroll
        // view it resizes is only handed the new frame seconds later, and
        // sometimes never (the keyboard log shows the composer held 43ms
        // behind a keyboard that arrived 6ms after the notification).  A
        // `CADisplayLink` is the display's own clock, the same one the
        // keyboard slides on, so the inset lands in the same frame the
        // keyboard does — and the `onScrollGeometryChange` pass it triggers
        // is what carries the transcript along, in the same frame too.
        animate(to: target, duration: duration ?? 0.25, curve: curve ?? 7)
    }

    // MARK: - Display-linked driver

    private var driver: CADisplayLink?
    private var driverStart = CACurrentMediaTime()
    private var driverDuration = 0.25
    private var driverFrom: CGFloat = 0
    private var driverTo: CGFloat = 0
    private var driverCP = (0.17, 0.17, 0.0, 1.0)

    private func animate(to target: CGFloat, duration: Double, curve: Int) {
        // A notification that lands mid-flight starts from wherever the last
        // one had got to, not from its own beginning.
        driver?.invalidate()
        driverStart = CACurrentMediaTime()
        driverDuration = max(duration, 0.01)
        driverFrom = overlap
        driverTo = target
        driverCP = Self.controlPoints(curve: curve)

        let link = CADisplayLink(target: self, selector: #selector(tick(_:)))
        link.add(to: .main, forMode: .common)
        driver = link
        // The display has already drawn this frame by the time the link arms,
        // so the first callback is a frame away; land the first step now or
        // the inset visibly sits out the opening frame.
        setOverlap(at: 0)
    }

    @objc private func tick(_ link: CADisplayLink) {
        let elapsed = CACurrentMediaTime() - driverStart
        let progress = min(max(elapsed / driverDuration, 0), 1)
        setOverlap(at: progress)
        if progress >= 1 { stopDriver() }
    }

    private func setOverlap(at progress: Double) {
        let solved = Self.solveUnitBezier(
            x: progress,
            x1: driverCP.0, y1: driverCP.1, x2: driverCP.2, y2: driverCP.3
        )
        overlap = driverFrom + (driverTo - driverFrom) * solved
    }

    private func stopDriver() {
        driver?.invalidate()
        driver = nil
        overlap = driverTo
    }

    /// The keyboard animates on curve 7, which is private and is not any of
    /// the four `UIView.AnimationCurve` cases.  UIKit's own shape for it is a
    /// hard ease-out — ninety percent of the way in half the time — and the
    /// composer visibly sprinting ahead of the keyboard and then crawling is
    /// what that reads as.  The default here is much closer to even: linear
    /// through the first two thirds with a short settle at the end.  The
    /// public curves are spelled out for the rare notification that carries
    /// one.
    private static func controlPoints(curve: Int) -> (Double, Double, Double, Double) {
        switch curve {
        case 0: (0.42, 0, 0.58, 1)      // easeInOut
        case 1: (0.42, 0, 1, 1)         // easeIn
        case 2: (0, 0, 0.58, 1)         // easeOut
        case 3: (0, 0, 1, 1)            // linear
        default: (0.35, 0.35, 0.6, 1)   // the keyboard's slot, nearly even
        }
    }

    /// Evaluate a cubic-bezier easing curve: given x, solve the curve for the
    /// parameter and return y.  Newton's method with a bisection fallback,
    /// the way every browser does it.
    private static func solveUnitBezier(x: Double, x1: Double, y1: Double, x2: Double, y2: Double) -> Double {
        guard x > 0, x < 1 else { return x }
        var t = x
        for _ in 0..<8 {
            let err = bezier(t, x1, x2) - x
            if abs(err) < 1e-6 { return bezier(t, y1, y2) }
            let slope = bezierDerivative(t, x1, x2)
            if abs(slope) < 1e-6 { break }
            t = min(max(t - err / slope, 0), 1)
        }
        var lo = 0.0, hi = 1.0
        t = x
        while hi - lo > 1e-6 {
            if bezier(t, x1, x2) < x { lo = t } else { hi = t }
            t = (lo + hi) / 2
        }
        return bezier(t, y1, y2)
    }

    private static func bezier(_ t: Double, _ a1: Double, _ a2: Double) -> Double {
        let u = 1 - t
        return 3 * u * u * t * a1 + 3 * u * t * t * a2 + t * t * t
    }

    private static func bezierDerivative(_ t: Double, _ a1: Double, _ a2: Double) -> Double {
        let u = 1 - t
        return 3 * u * u * a1 + 6 * u * t * (a2 - a1) + 3 * t * t * (1 - a2)
    }

    private static var keyWindow: UIWindow? {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)
    }
}
