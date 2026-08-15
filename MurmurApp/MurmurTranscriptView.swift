import SwiftUI

// MARK: - Bubble shape

/// A rounded bubble with one squared-off corner on the speaker's side, the way
/// a tail reads without drawing an actual tail on every message.
private struct BubbleShape: Shape {
    let isOutgoing: Bool
    var radius: CGFloat = 18
    var tail: CGFloat = 5

    func path(in rect: CGRect) -> Path {
        Path(
            roundedRect: rect,
            cornerRadii: RectangleCornerRadii(
                topLeading: radius,
                bottomLeading: isOutgoing ? radius : tail,
                bottomTrailing: isOutgoing ? tail : radius,
                topTrailing: radius
            )
        )
    }
}

// MARK: - Delivery ticks

private struct DeliveryTicks: View {
    let state: MurmurDeliveryState

    var body: some View {
        switch state {
        case .sending:
            Image(systemName: "clock")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.7))
                .accessibilityLabel("发送中")
        case .failed:
            Image(systemName: "exclamationmark.circle")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(MurmurTheme.coral)
                .accessibilityLabel("发送失败")
        case .sent, .answered:
            // Two overlapping checks, WhatsApp-style: the second slides in and
            // the pair turns colour once Murmur starts composing.
            ZStack(alignment: .leading) {
                Image(systemName: "checkmark")
                    .offset(x: state == .answered ? 0 : 3)
                Image(systemName: "checkmark")
                    .offset(x: 5)
                    .opacity(state == .answered ? 1 : 0)
                    .scaleEffect(state == .answered ? 1 : 0.6, anchor: .leading)
            }
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(state == .answered ? MurmurTheme.olive : MurmurTheme.secondaryInk.opacity(0.75))
            .frame(width: 16, alignment: .leading)
            .animation(.spring(response: 0.32, dampingFraction: 0.72), value: state)
            .accessibilityLabel(state == .answered ? "已送达，Murmur 正在回应" : "已送达")
        }
    }
}

// MARK: - Typing indicator

private struct TypingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase = 0

    private let timer = Timer.publish(every: 0.28, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(MurmurTheme.secondaryInk.opacity(0.55))
                    .frame(width: 7, height: 7)
                    .scaleEffect(!reduceMotion && phase == index ? 1.35 : 0.85)
                    .animation(.easeInOut(duration: 0.26), value: phase)
            }
        }
        .padding(.horizontal, 15)
        .padding(.vertical, 13)
        .background(MurmurTheme.raisedPaper, in: BubbleShape(isOutgoing: false))
        .overlay {
            BubbleShape(isOutgoing: false).stroke(MurmurTheme.rule, lineWidth: 1)
        }
        .onReceive(timer) { _ in
            guard !reduceMotion else { return }
            phase = (phase + 1) % 3
        }
        .accessibilityLabel("Murmur 正在输入")
    }
}

// MARK: - One message

private struct MessageRow: View {
    let message: MurmurMessage
    let imageURL: URL?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    private var isOutgoing: Bool { message.author == .you }

    var body: some View {
        HStack {
            if isOutgoing { Spacer(minLength: 56) }
            VStack(alignment: isOutgoing ? .trailing : .leading, spacing: 7) {
                if let imageURL, let image = UIImage(contentsOfFile: imageURL.path) {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                        .frame(maxWidth: 240, maxHeight: 280)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                        .accessibilityLabel("你发送的照片")
                }
                if !message.text.isEmpty {
                    Text(message.text)
                        .font(MurmurTheme.body(.body))
                        .foregroundStyle(isOutgoing ? Color.white : MurmurTheme.ink)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(
                            isOutgoing ? MurmurTheme.olive : MurmurTheme.raisedPaper,
                            in: BubbleShape(isOutgoing: isOutgoing)
                        )
                        .overlay {
                            if !isOutgoing {
                                BubbleShape(isOutgoing: false).stroke(MurmurTheme.rule, lineWidth: 1)
                            }
                        }
                }
                HStack(spacing: 5) {
                    Text(message.sentAt, format: .dateTime.hour().minute())
                        .font(MurmurTheme.body(.caption2))
                        .foregroundStyle(MurmurTheme.secondaryInk.opacity(0.85))
                    if isOutgoing { DeliveryTicks(state: message.delivery) }
                }
                .padding(.horizontal, 4)
            }
            if !isOutgoing { Spacer(minLength: 56) }
        }
        // Entrance: rise and fade from the speaker's side.
        .opacity(appeared || reduceMotion ? 1 : 0)
        .offset(y: appeared || reduceMotion ? 0 : 10)
        .onAppear {
            guard !reduceMotion, !appeared else { appeared = true; return }
            withAnimation(.spring(response: 0.38, dampingFraction: 0.82)) { appeared = true }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(isOutgoing ? "你说：\(message.text)" : "Murmur 说：\(message.text)")
    }
}

// MARK: - Day separator

private struct DaySeparator: View {
    let date: Date

    var body: some View {
        Text(date, format: .dateTime.year().month(.abbreviated).day())
            .font(MurmurTheme.body(.caption2, weight: .medium))
            .foregroundStyle(MurmurTheme.secondaryInk)
            .padding(.horizontal, 11)
            .padding(.vertical, 5)
            .background(MurmurTheme.raisedPaper, in: Capsule())
            .overlay { Capsule().stroke(MurmurTheme.rule, lineWidth: 1) }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
    }
}

// MARK: - Transcript

struct MurmurTranscriptView: View {
    @ObservedObject var model: MurmurSessionModel

    private var showsTyping: Bool {
        model.phase == .responding || model.phase == .uploading
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if model.messages.isEmpty && !showsTyping {
                        EmptyTranscript()
                            .padding(.top, 60)
                    }
                    ForEach(Array(model.messages.enumerated()), id: \.element.id) { index, message in
                        if needsSeparator(at: index) {
                            DaySeparator(date: message.sentAt)
                        }
                        MessageRow(
                            message: message,
                            imageURL: message.imageFile.map { model.transcriptStore.imageURL(for: $0) }
                        )
                        .id(message.id)
                        .accessibilityIdentifier("murmur-message-\(index)")
                    }
                    if showsTyping {
                        HStack {
                            TypingIndicator()
                            Spacer(minLength: 56)
                        }
                        .id(Self.typingAnchor)
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }
                    Color.clear.frame(height: 1).id(Self.bottomAnchor)
                }
                .frame(maxWidth: MurmurTheme.contentWidth)
                .padding(.horizontal, MurmurTheme.pageInset)
                .padding(.top, 16)
                .padding(.bottom, 12)
                .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
            .animation(.spring(response: 0.4, dampingFraction: 0.85), value: model.messages.count)
            .animation(.easeInOut(duration: 0.22), value: showsTyping)
            .onChange(of: model.messages.count) { _, _ in scrollToBottom(proxy) }
            .onChange(of: showsTyping) { _, _ in scrollToBottom(proxy) }
            .onAppear { proxy.scrollTo(Self.bottomAnchor, anchor: .bottom) }
        }
    }

    private static let bottomAnchor = "murmur-transcript-bottom"
    private static let typingAnchor = "murmur-transcript-typing"

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(.spring(response: 0.42, dampingFraction: 0.86)) {
            proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
        }
    }

    private func needsSeparator(at index: Int) -> Bool {
        guard index > 0 else { return true }
        return !Calendar.current.isDate(
            model.messages[index].sentAt,
            inSameDayAs: model.messages[index - 1].sentAt
        )
    }
}

private struct EmptyTranscript: View {
    var body: some View {
        VStack(spacing: 12) {
            Text("发来眼前的一刻。")
                .font(MurmurTheme.display(.title))
                .foregroundStyle(MurmurTheme.ink)
                .multilineTextAlignment(.center)
            Text("可以是一张照片，也可以只说一句。")
                .font(MurmurTheme.body(.subheadline))
                .foregroundStyle(MurmurTheme.secondaryInk)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}
