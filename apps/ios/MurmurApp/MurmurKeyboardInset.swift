import SwiftUI
import UIKit

/// Window-local observation for player visibility and scroll diagnostics.
/// System safe areas, not this measurement, position composers.
@MainActor
final class MurmurKeyboardState: ObservableObject {
    /// How far the software keyboard reaches above the resting home-indicator
    /// inset. Hardware keyboards and an absent software keyboard both report 0.
    @Published private(set) var overlap: CGFloat = 0
    func updateFromLayoutGuide(overlap newValue: CGFloat) {
        setOverlap(newValue)
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

/// Coordinate conversion for observation; this never adds layout padding.
enum MurmurKeyboardClearance {
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
