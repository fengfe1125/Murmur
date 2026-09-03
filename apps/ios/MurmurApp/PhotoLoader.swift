import CoreTransferable
import ImageIO
import PhotosUI
import UniformTypeIdentifiers
import UIKit

struct PhotoPickerFile: Transferable, Sendable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .image) { received in
            let source = received.file
            let suffix = source.pathExtension.isEmpty ? "image" : source.pathExtension
            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent("murmur-picker-\(UUID().uuidString)")
                .appendingPathExtension(suffix)
            try FileManager.default.copyItem(at: source, to: destination)
            try FileManager.default.setAttributes(
                [.protectionKey: FileProtectionType.complete],
                ofItemAtPath: destination.path
            )
            return PhotoPickerFile(url: destination)
        }
    }
}

actor PhotoLoader {
    static let maximumUploadBytes: Int64 = 25 * 1_024 * 1_024

    func load(fileURL: URL, maximumPreviewPixels: CGFloat = 1_800) async throws -> PhotoAttachment {
        try await Task.detached(priority: .userInitiated) {
            let values = try fileURL.resourceValues(forKeys: [.fileSizeKey, .contentTypeKey, .nameKey])
            let byteCount = Int64(values.fileSize ?? 0)
            guard byteCount > 0 else {
                throw MurmurFailure(code: "empty_image", message: "这张图片是空的。", retryable: false)
            }
            guard byteCount <= Self.maximumUploadBytes else {
                throw MurmurFailure(code: "image_too_large", message: "图片不能超过 25 MB。", retryable: false)
            }

            let managedURL = try Self.copyIntoManagedTemporaryLocation(fileURL)
            do {
                let image = try Self.downsample(url: managedURL, maximumPixels: maximumPreviewPixels)
                let type = values.contentType ?? UTType(filenameExtension: managedURL.pathExtension) ?? .jpeg
                return PhotoAttachment(
                    id: UUID(),
                    originalURL: managedURL,
                    preview: image,
                    filename: Self.safeFilename(values.name ?? managedURL.lastPathComponent, type: type),
                    mimeType: type.preferredMIMEType ?? "image/jpeg",
                    byteCount: byteCount
                )
            } catch {
                try? FileManager.default.removeItem(at: managedURL)
                throw error
            }
        }.value
    }

    func load(capturedImage: UIImage, maximumPreviewPixels: CGFloat = 1_800) async throws -> PhotoAttachment {
        try await loadJPEG(of: capturedImage, urlPrefix: "murmur-camera-", filenameStem: "camera",
                           maximumPreviewPixels: maximumPreviewPixels)
    }

    /// A photo sent from 当年今日.  Browsing the library never touches disk;
    /// the file appears only at the moment the person swipes a photo up into
    /// the conversation, under a prefix the cold-start sweeper knows about.
    func load(libraryImage: UIImage, maximumPreviewPixels: CGFloat = 1_800) async throws -> PhotoAttachment {
        try await loadJPEG(of: libraryImage, urlPrefix: "murmur-onthisday-", filenameStem: "onthisday",
                           maximumPreviewPixels: maximumPreviewPixels)
    }

    private func loadJPEG(
        of image: UIImage, urlPrefix: String, filenameStem: String, maximumPreviewPixels: CGFloat
    ) async throws -> PhotoAttachment {
        try await Task.detached(priority: .userInitiated) {
            guard let data = image.jpegData(compressionQuality: 0.94) else {
                throw MurmurFailure(code: "camera_encoding_failed", message: "没有保存好这张照片。", retryable: true)
            }
            guard Int64(data.count) <= Self.maximumUploadBytes else {
                throw MurmurFailure(code: "image_too_large", message: "图片不能超过 25 MB。", retryable: false)
            }
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("\(urlPrefix)\(UUID().uuidString).jpg")
            try data.write(to: url, options: [.atomic, .completeFileProtection])
            let preview = try Self.downsample(url: url, maximumPixels: maximumPreviewPixels)
            return PhotoAttachment(
                id: UUID(),
                originalURL: url,
                preview: preview,
                filename: "\(filenameStem)-\(UUID().uuidString).jpg",
                mimeType: "image/jpeg",
                byteCount: Int64(data.count)
            )
        }.value
    }

    func discard(_ attachment: PhotoAttachment?) {
        guard let attachment else { return }
        try? FileManager.default.removeItem(at: attachment.originalURL)
    }

    func discardFile(at url: URL) {
        try? FileManager.default.removeItem(at: url)
    }

    func cleanupStaleTemporaryFiles() {
        let prefixes = ["murmur-picker-", "murmur-upload-", "murmur-camera-", "murmur-onthisday-", "murmur-multipart-"]
        guard let urls = try? FileManager.default.contentsOfDirectory(
            at: FileManager.default.temporaryDirectory,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        ) else { return }
        for url in urls where prefixes.contains(where: url.lastPathComponent.hasPrefix) {
            try? FileManager.default.removeItem(at: url)
        }
    }

    private nonisolated static func copyIntoManagedTemporaryLocation(_ source: URL) throws -> URL {
        if source.lastPathComponent.hasPrefix("murmur-") && source.deletingLastPathComponent() == FileManager.default.temporaryDirectory {
            return source
        }
        let suffix = source.pathExtension.isEmpty ? "image" : source.pathExtension
        let destination = FileManager.default.temporaryDirectory
            .appendingPathComponent("murmur-upload-\(UUID().uuidString)")
            .appendingPathExtension(suffix)
        try FileManager.default.copyItem(at: source, to: destination)
        try FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.complete],
            ofItemAtPath: destination.path
        )
        return destination
    }

    /// Shared with the transcript, which reads the same files back at whatever
    /// size the screen actually needs rather than decoding originals whole.
    /// Downsample bytes already in memory.
    ///
    /// The URL form below hands CoreGraphics a path to read.  A cover fetched
    /// over the network is already Data by then, and writing it to a temporary
    /// file just to hand back a path would be a round trip for nothing.
    nonisolated static func downsample(data: Data, maximumPixels: CGFloat) throws -> UIImage {
        let options: [CFString: Any] = [kCGImageSourceShouldCache: false]
        guard let source = CGImageSourceCreateWithData(data as CFData, options as CFDictionary) else {
            throw MurmurFailure(code: "invalid_image", message: "无法读取这张图片。", retryable: false)
        }
        return try thumbnail(from: source, maximumPixels: maximumPixels)
    }

    nonisolated static func downsample(url: URL, maximumPixels: CGFloat) throws -> UIImage {
        let options: [CFString: Any] = [kCGImageSourceShouldCache: false]
        guard let source = CGImageSourceCreateWithURL(url as CFURL, options as CFDictionary) else {
            throw MurmurFailure(code: "invalid_image", message: "无法读取这张图片。", retryable: false)
        }
        return try thumbnail(from: source, maximumPixels: maximumPixels)
    }

    private nonisolated static func thumbnail(
        from source: CGImageSource, maximumPixels: CGFloat
    ) throws -> UIImage {
        let thumbnailOptions: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maximumPixels
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, thumbnailOptions as CFDictionary) else {
            throw MurmurFailure(code: "invalid_image", message: "无法生成图片预览。", retryable: false)
        }
        return UIImage(cgImage: image)
    }

    private nonisolated static func safeFilename(_ raw: String, type: UTType) -> String {
        let base = URL(fileURLWithPath: raw).deletingPathExtension().lastPathComponent
            .filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        let safeBase = base.isEmpty ? "moment" : String(base.prefix(80))
        return "\(safeBase).\(type.preferredFilenameExtension ?? "jpg")"
    }
}
