import SwiftUI
import UIKit

/// A photo the person can open full screen: either one waiting in the composer
/// or one already sent and sitting in the transcript.
struct MurmurPhotoPreview: Identifiable, Equatable {
    let id: String
    let url: URL
}

// MARK: - Decoded image cache

/// Transcript rows are rebuilt constantly, and decoding a 12-megapixel original
/// inside `body` every time is what makes a chat list stutter.  Everything goes
/// through here instead: decoded off the main thread, at the size actually
/// being drawn, and kept until memory gets tight.
@MainActor
final class MurmurImageCache {
    static let shared = MurmurImageCache()

    /// Big enough to stay sharp on a 3x screen at the transcript's width.
    static let thumbnailPixels: CGFloat = 900
    /// Enough to survive a few steps of pinch-zoom without turning to mush.
    static let fullScreenPixels: CGFloat = 2_600

    private let cache = NSCache<NSString, UIImage>()
    private var inFlight: [String: Task<UIImage?, Never>] = [:]

    private init() {
        cache.countLimit = 60
    }

    func cached(_ url: URL, maximumPixels: CGFloat) -> UIImage? {
        cache.object(forKey: Self.key(url, maximumPixels) as NSString)
    }

    func image(for url: URL, maximumPixels: CGFloat) async -> UIImage? {
        let key = Self.key(url, maximumPixels)
        if let hit = cache.object(forKey: key as NSString) { return hit }
        if let running = inFlight[key] { return await running.value }
        let task = Task<UIImage?, Never>.detached(priority: .userInitiated) {
            try? PhotoLoader.downsample(url: url, maximumPixels: maximumPixels)
        }
        inFlight[key] = task
        let image = await task.value
        inFlight[key] = nil
        if let image { cache.setObject(image, forKey: key as NSString) }
        return image
    }

    private static func key(_ url: URL, _ maximumPixels: CGFloat) -> String {
        "\(url.lastPathComponent)@\(Int(maximumPixels))"
    }
}

// MARK: - Transcript photo

/// One photo inside a message bubble.  Sized from the image's own proportions
/// so a panorama and a portrait both look deliberate, and tappable.
struct TranscriptPhoto: View {
    let url: URL
    var maximumWidth: CGFloat = 232
    var maximumHeight: CGFloat = 300
    let onOpen: () -> Void

    @State private var image: UIImage?

    private var displaySize: CGSize {
        guard let image, image.size.width > 0, image.size.height > 0 else {
            return CGSize(width: maximumWidth, height: maximumWidth * 0.75)
        }
        let ratio = image.size.height / image.size.width
        let height = min(maximumWidth * ratio, maximumHeight)
        return CGSize(width: min(height / ratio, maximumWidth), height: height)
    }

    var body: some View {
        Button(action: onOpen) {
            ZStack {
                MurmurTheme.raisedPaper
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                } else {
                    ProgressView().tint(MurmurTheme.secondaryInk)
                }
            }
            .frame(width: displaySize.width, height: displaySize.height)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay {
                RoundedRectangle(cornerRadius: 14).stroke(MurmurTheme.rule, lineWidth: 0.5)
            }
            .contentShape(RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .task(id: url) {
            image = await MurmurImageCache.shared.image(
                for: url, maximumPixels: MurmurImageCache.thumbnailPixels
            )
        }
    }
}

// MARK: - Full screen

/// The photo on its own: pinch to zoom, drag down to put it away.
struct MurmurPhotoLightbox: View {
    let url: URL
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var image: UIImage?
    @State private var scale: CGFloat = 1
    @State private var committedScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var committedOffset: CGSize = .zero

    private var isZoomed: Bool { scale > 1.01 }

    /// Only a downward drag on an unzoomed photo dismisses, and the backdrop
    /// thins out as it goes so the gesture explains itself halfway through.
    private var backdropOpacity: Double {
        guard !isZoomed else { return 1 }
        return 1 - min(Double(max(offset.height, 0)) / 600, 0.55)
    }

    var body: some View {
        ZStack {
            Color.black
                .opacity(backdropOpacity)
                .ignoresSafeArea()
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .scaleEffect(scale)
                    .offset(offset)
                    .gesture(magnify)
                    .simultaneousGesture(drag)
                    .onTapGesture(count: 2) { toggleZoom() }
                    .accessibilityLabel("照片")
            } else {
                ProgressView().tint(.white)
            }
        }
        .overlay(alignment: .topLeading) {
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 34, height: 34)
                    // Light, because the backdrop it sits on is black.
                    .background(.white.opacity(0.18), in: Circle())
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .padding(.leading, 8)
            .accessibilityLabel("关闭照片")
            .accessibilityIdentifier("close-photo")
        }
        .statusBarHidden()
        .task(id: url) {
            // Whatever the transcript already decoded shows immediately; the
            // sharper copy replaces it a moment later.
            image = MurmurImageCache.shared.cached(url, maximumPixels: MurmurImageCache.thumbnailPixels)
            let full = await MurmurImageCache.shared.image(
                for: url, maximumPixels: MurmurImageCache.fullScreenPixels
            )
            if let full { image = full }
        }
    }

    private var magnify: some Gesture {
        MagnifyGesture()
            .onChanged { value in
                scale = min(max(committedScale * value.magnification, 0.7), 6)
            }
            .onEnded { _ in
                if scale <= 1 {
                    reset()
                } else {
                    committedScale = scale
                }
            }
    }

    private var drag: some Gesture {
        DragGesture()
            .onChanged { value in
                if isZoomed {
                    offset = CGSize(
                        width: committedOffset.width + value.translation.width,
                        height: committedOffset.height + value.translation.height
                    )
                } else {
                    offset = CGSize(width: 0, height: max(0, value.translation.height))
                }
            }
            .onEnded { value in
                if isZoomed {
                    committedOffset = offset
                } else if value.translation.height > 120 || value.predictedEndTranslation.height > 420 {
                    dismiss()
                } else {
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
                        offset = .zero
                    }
                }
            }
    }

    private func toggleZoom() {
        withAnimation(reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 0.85)) {
            if isZoomed {
                reset()
            } else {
                scale = 2.4
                committedScale = 2.4
            }
        }
    }

    private func reset() {
        scale = 1
        committedScale = 1
        offset = .zero
        committedOffset = .zero
    }
}
