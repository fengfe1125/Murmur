import UIKit
import UniformTypeIdentifiers

/// The extension never talks to the VPS and never trusts the provider's share
/// text as metadata.  It writes one bounded raw draft into the App Group; the
/// authenticated containing app resolves and previews it on its next open.
final class ShareViewController: UIViewController {
    private let statusLabel = UILabel()
    private var didStart = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        statusLabel.numberOfLines = 0
        statusLabel.textAlignment = .center
        statusLabel.font = .preferredFont(forTextStyle: .body)
        statusLabel.text = "正在准备这首歌…"
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(statusLabel)
        NSLayoutConstraint.activate([
            statusLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            statusLabel.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
            statusLabel.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        guard !didStart else { return }
        didStart = true
        captureFirstSupportedItem()
    }

    private func captureFirstSupportedItem() {
        let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? [])
            .flatMap { $0.attachments ?? [] }
        guard let provider = providers.first(where: {
            $0.hasItemConformingToTypeIdentifier(UTType.url.identifier)
                || $0.hasItemConformingToTypeIdentifier(UTType.plainText.identifier)
        }) else {
            finish(with: ShareDraftError.noSupportedItem)
            return
        }

        if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
            provider.loadItem(forTypeIdentifier: UTType.url.identifier, options: nil) { [weak self] item, error in
                if error != nil {
                    DispatchQueue.main.async { self?.finish(with: ShareDraftError.loadingFailed) }
                    return
                }
                let text = (item as? URL)?.absoluteString ?? (item as? NSURL)?.absoluteString
                DispatchQueue.main.async { self?.save(text) }
            }
        } else {
            provider.loadItem(forTypeIdentifier: UTType.plainText.identifier, options: nil) { [weak self] item, error in
                if error != nil {
                    DispatchQueue.main.async { self?.finish(with: ShareDraftError.loadingFailed) }
                    return
                }
                let text = item as? String
                DispatchQueue.main.async { self?.save(text) }
            }
        }
    }

    private func save(_ value: String?) {
        guard let value else { finish(with: ShareDraftError.noSupportedItem); return }
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        // 服务端 MAX_SHARED_TEXT_BYTES 是 8KiB；扩展这里放得更宽的话，
        // 草稿会被存下来、主 App 再拿去换一个 413。
        guard !text.isEmpty, text.utf8.count <= 8 * 1024 else {
            finish(with: ShareDraftError.invalidText)
            return
        }
        do {
            try SharedMusicDraftStore().save(SharedMusicDraftV1(rawText: text))
            DispatchQueue.main.async { [weak self] in
                self?.statusLabel.text = "已保存。打开 Murmur 后确认再发送。"
                self?.extensionContext?.completeRequest(returningItems: nil)
            }
        } catch {
            finish(with: error)
        }
    }

    private func finish(with error: Error) {
        DispatchQueue.main.async { [weak self] in
            self?.statusLabel.text = "这次没有识别到可发送的网易云链接。"
            self?.extensionContext?.cancelRequest(withError: error)
        }
    }
}

private enum ShareDraftError: LocalizedError {
    case noSupportedItem
    case invalidText
    case loadingFailed

    var errorDescription: String? {
        switch self {
        case .noSupportedItem: "没有可用的分享链接。"
        case .invalidText: "分享内容为空或过长。"
        case .loadingFailed: "没有读到这次分享的内容。"
        }
    }
}
