import SwiftUI
import UIKit

/// The keyboard geometry shared by every screen inside one app shell.
///
/// This is deliberately owned by `MurmurShell`, not a process-wide singleton:
/// keyboard layout guides belong to a particular window, and a late event from
/// another scene must never move this scene's composer.
@MainActor
final class MurmurKeyboardState: ObservableObject {
    /// How far the software keyboard reaches above the resting home-indicator
    /// inset. Hardware keyboards and an absent software keyboard both report 0.
    @Published private(set) var overlap: CGFloat = 0
    private let injectedOverlap: CGFloat?

    init() {
        #if DEBUG
        injectedOverlap = ProcessInfo.processInfo.arguments.contains(
            "--murmur-stub-keyboard-overlap"
        ) ? 301 : nil
        #else
        injectedOverlap = nil
        #endif
    }

    func updateFromLayoutGuide(overlap newValue: CGFloat) {
        guard injectedOverlap == nil else { return }
        // The probe only reports changes, so a value dropped here would never
        // be offered again and the layout would sit at a stale overlap for as
        // long as the keyboard stayed put.  Hold it instead of losing it.
        guard !warming else { deferredOverlap = newValue; return }
        setOverlap(newValue)
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
    /// shown — so the layout guide should not move at all.  `warming` is there
    /// for the case where it twitches anyway: a keyboard nobody asked to see
    /// must not move the composer on an idle screen.
    func warm() {
        // Never while the keyboard is up: taking first responder from the
        // composer would put the keyboard away mid-sentence.
        guard overlap == 0, !warming, let window = Self.keyWindow else { return }
        warming = true
        // A field has to be in a window to become first responder at all, and
        // at zero size it is invisible for the one turn it spends there.
        let field = UITextField(frame: .zero)
        window.addSubview(field)
        field.becomeFirstResponder()
        field.resignFirstResponder()
        field.removeFromSuperview()
        // Layout runs after this turn, so the guard outlives the borrowed
        // responder by one hop rather than ending with it.
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.warming = false
            guard let deferred = self.deferredOverlap else { return }
            self.deferredOverlap = nil
            self.setOverlap(deferred)
        }
    }

    /// Set while the warm-up holds first responder, and for the layout pass
    /// that follows it.
    private var warming = false
    private var deferredOverlap: CGFloat?

    private static var keyWindow: UIWindow? {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)
    }

    /// Deterministic geometry for UI layout tests. Production builds never
    /// carry an injected value, so focus alone cannot manufacture an overlap.
    func focusDidChange(_ focused: Bool) {
        guard let injectedOverlap else { return }
        setOverlap(focused ? injectedOverlap : 0)
    }

    private func setOverlap(_ newValue: CGFloat) {
        let sanitized = newValue.isFinite ? max(0, newValue) : 0
        guard abs(sanitized - overlap) > 0.25 else { return }
        overlap = sanitized
        #if DEBUG
        MurmurDiagnostics.recordKeyboardLayoutGuide(overlap: sanitized)
        #endif
    }
}

/// One source of truth for the clearance beneath a composer.
enum MurmurKeyboardClearance {
    /// The total clearance required by a screen that is outside the shell's
    /// own content inset, such as an archive day pushed on a NavigationStack.
    static func total(overlap: CGFloat, resting: CGFloat) -> CGFloat {
        max(sanitized(overlap), max(0, resting))
    }

    /// Extra clearance for the ordinary chat, whose shell already reserves
    /// `resting` points for the tab bar.
    static func supplemental(overlap: CGFloat, resting: CGFloat) -> CGFloat {
        max(0, sanitized(overlap) - max(0, resting))
    }

    /// Converts a full-screen keyboard-layout-guide position into the overlap
    /// that remains after the resting home-indicator inset is removed.
    static func overlap(
        containerBottom: CGFloat,
        keyboardTop: CGFloat,
        restingBottomInset: CGFloat
    ) -> CGFloat {
        guard containerBottom.isFinite, keyboardTop.isFinite else { return 0 }
        let reach = max(0, containerBottom - keyboardTop)
        return max(0, reach - max(0, restingBottomInset))
    }

    private static func sanitized(_ value: CGFloat) -> CGFloat {
        value.isFinite ? max(0, value) : 0
    }
}

/// A full-screen UIKit probe whose top marker is constrained to the system's
/// `UIKeyboardLayoutGuide`. UIKit moves that guide on the keyboard's own
/// animation clock, so SwiftUI receives the geometry that is actually on
/// screen instead of predicting it from notifications.
struct MurmurKeyboardLayoutGuideProbe: UIViewRepresentable {
    let onOverlapChange: @MainActor (CGFloat) -> Void

    func makeUIView(context: Context) -> MurmurKeyboardProbeHostView {
        let view = MurmurKeyboardProbeHostView()
        view.onOverlapChange = onOverlapChange
        return view
    }

    func updateUIView(_ uiView: MurmurKeyboardProbeHostView, context: Context) {
        uiView.onOverlapChange = onOverlapChange
    }

    static func dismantleUIView(_ uiView: MurmurKeyboardProbeHostView, coordinator: ()) {
        uiView.detachProbe()
    }
}

/// The representable only supplies window membership. The actual probe is a
/// sibling of the hosting view, constrained to the window itself: observing a
/// guide inside the SwiftUI overlay feeds the composer's lift back into the
/// next keyboard measurement when that overlay is reframed.
final class MurmurKeyboardProbeHostView: UIView {
    private let probe = MurmurKeyboardProbeView()

    var onOverlapChange: (@MainActor (CGFloat) -> Void)? {
        didSet { probe.onOverlapChange = onOverlapChange }
    }

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        backgroundColor = .clear
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        guard probe.superview !== window else { return }
        detachProbe()
        guard let window else { return }
        probe.translatesAutoresizingMaskIntoConstraints = false
        window.addSubview(probe)
        NSLayoutConstraint.activate([
            probe.topAnchor.constraint(equalTo: window.topAnchor),
            probe.bottomAnchor.constraint(equalTo: window.bottomAnchor),
            probe.leadingAnchor.constraint(equalTo: window.leadingAnchor),
            probe.trailingAnchor.constraint(equalTo: window.trailingAnchor),
        ])
    }

    func detachProbe() {
        probe.removeFromSuperview()
    }
}

final class MurmurKeyboardProbeView: UIView {
    var onOverlapChange: (@MainActor (CGFloat) -> Void)?

    private let guideMarker = UIView(frame: .zero)
    private var lastOverlap: CGFloat = -.infinity

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        accessibilityElementsHidden = true
        backgroundColor = .clear

        keyboardLayoutGuide.followsUndockedKeyboard = false
        guideMarker.translatesAutoresizingMaskIntoConstraints = false
        guideMarker.isUserInteractionEnabled = false
        guideMarker.alpha = 0
        addSubview(guideMarker)
        NSLayoutConstraint.activate([
            guideMarker.topAnchor.constraint(equalTo: keyboardLayoutGuide.topAnchor),
            guideMarker.leadingAnchor.constraint(equalTo: leadingAnchor),
            guideMarker.widthAnchor.constraint(equalToConstant: 1),
            guideMarker.heightAnchor.constraint(equalToConstant: 0),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        setNeedsLayout()
    }

    override func safeAreaInsetsDidChange() {
        super.safeAreaInsetsDidChange()
        setNeedsLayout()
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        guard let window, !window.bounds.isEmpty else { return }

        // Both ends are measured in window coordinates. UIWindow's safe area
        // remains the home-indicator inset even while the keyboard is visible.
        let keyboardTop = guideMarker.convert(.zero, to: window).y
        let overlap = MurmurKeyboardClearance.overlap(
            containerBottom: window.bounds.maxY,
            keyboardTop: keyboardTop,
            restingBottomInset: window.safeAreaInsets.bottom
        )

        guard abs(overlap - lastOverlap) > 0.25 else { return }
        lastOverlap = overlap
        onOverlapChange?(overlap)
    }
}

/// Shared transcript behavior: native scroll dismissal stays disabled so an
/// automatic scroll-to-bottom cannot cancel a keyboard that is still rising.
/// A deliberate tap or a vertical, downward drag of 18 points releases focus;
/// the drag is simultaneous, leaving the scroll view's own gesture intact.
private struct MurmurKeyboardDismissSurface: ViewModifier {
    let isFocused: Bool
    let dismiss: () -> Void
    @State private var dismissedDuringDrag = false

    func body(content: Content) -> some View {
        content
            .scrollDismissesKeyboard(.never)
            .contentShape(Rectangle())
            .simultaneousGesture(
                TapGesture()
                    .onEnded {
                        guard isFocused else { return }
                        dismiss()
                    }
            )
            .simultaneousGesture(
                DragGesture(minimumDistance: 4)
                    .onChanged { value in
                        guard isFocused, !dismissedDuringDrag else { return }
                        let vertical = value.translation.height
                        guard vertical >= 18, vertical > abs(value.translation.width) else { return }
                        dismissedDuringDrag = true
                        dismiss()
                    }
                    .onEnded { _ in
                        dismissedDuringDrag = false
                    }
            )
    }
}

extension View {
    func murmurKeyboardDismissSurface(
        isFocused: Bool,
        dismiss: @escaping () -> Void
    ) -> some View {
        modifier(MurmurKeyboardDismissSurface(isFocused: isFocused, dismiss: dismiss))
    }
}
