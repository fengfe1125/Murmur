import XCTest
import UIKit
@testable import Murmur

final class PhotoLoaderTests: XCTestCase {
    func testBootstrapCleanupOnlyRemovesMurmurTemporaryFiles() async throws {
        let root = FileManager.default.temporaryDirectory
        let murmurFiles = ["murmur-picker-test", "murmur-upload-test", "murmur-camera-test", "murmur-multipart-test"]
            .map(root.appendingPathComponent)
        let unrelated = root.appendingPathComponent("unrelated-photo-loader-test")
        for file in murmurFiles { try Data("private".utf8).write(to: file, options: .atomic) }
        try Data("keep".utf8).write(to: unrelated, options: .atomic)
        defer { try? FileManager.default.removeItem(at: unrelated) }

        let loader = PhotoLoader()
        await loader.cleanupStaleTemporaryFiles()

        for file in murmurFiles { XCTAssertFalse(FileManager.default.fileExists(atPath: file.path)) }
        XCTAssertTrue(FileManager.default.fileExists(atPath: unrelated.path))
    }

    func testLargePhotoIsDownsampledOffTheOriginalDimensions() async throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 4_000, height: 3_000))
        let image = renderer.image { context in
            UIColor.systemGreen.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 4_000, height: 3_000))
        }
        let source = FileManager.default.temporaryDirectory.appendingPathComponent("photo-loader-test.jpg")
        try XCTUnwrap(image.jpegData(compressionQuality: 0.9)).write(to: source, options: .atomic)
        defer { try? FileManager.default.removeItem(at: source) }

        let loader = PhotoLoader()
        let attachment = try await loader.load(fileURL: source, maximumPreviewPixels: 1_200)
        defer { Task { await loader.discard(attachment) } }

        XCTAssertLessThanOrEqual(max(attachment.preview.size.width, attachment.preview.size.height), 1_200)
        XCTAssertTrue(FileManager.default.fileExists(atPath: attachment.originalURL.path))
    }

    func testImageOverTwentyFiveMegabytesIsRejected() async throws {
        let source = FileManager.default.temporaryDirectory
            .appendingPathComponent("photo-loader-oversize-\(UUID().uuidString).jpg")
        FileManager.default.createFile(atPath: source.path, contents: Data([0xFF, 0xD8, 0xFF]))
        let handle = try FileHandle(forWritingTo: source)
        try handle.seekToEnd()
        try handle.write(contentsOf: Data(count: Int(PhotoLoader.maximumUploadBytes) - 2))
        try handle.close()
        defer { try? FileManager.default.removeItem(at: source) }

        let loader = PhotoLoader()
        do {
            _ = try await loader.load(fileURL: source)
            XCTFail("oversized image should be rejected")
        } catch let failure as MurmurFailure {
            XCTAssertEqual(failure.code, "image_too_large")
            XCTAssertFalse(failure.retryable)
        }
    }

    func testThirtySequentialLoadsLeaveNoManagedFiles() async throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 64, height: 64))
        let image = renderer.image { context in
            UIColor.systemGreen.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 64, height: 64))
        }
        let source = FileManager.default.temporaryDirectory
            .appendingPathComponent("photo-loader-repeat-\(UUID().uuidString).jpg")
        try XCTUnwrap(image.jpegData(compressionQuality: 0.8)).write(to: source, options: .atomic)
        defer { try? FileManager.default.removeItem(at: source) }

        let loader = PhotoLoader()
        var originals: [URL] = []
        for _ in 0..<30 {
            let attachment = try await loader.load(fileURL: source, maximumPreviewPixels: 64)
            originals.append(attachment.originalURL)
            await loader.discard(attachment)
        }
        for url in originals {
            XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        }
    }
}
