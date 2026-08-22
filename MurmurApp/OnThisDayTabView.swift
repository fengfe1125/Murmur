import SwiftUI

/// The 当年今日 tab.  For now it is the browser it has always been, opened
/// straight from the tab rather than from a disc over the conversation; the
/// calendar that will sit above it lands in the next step.
struct OnThisDayTabView: View {
    @ObservedObject var model: MurmurSessionModel
    @StateObject private var onThisDay = OnThisDayModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        OnThisDayFlowView(model: onThisDay, showsClose: false) { image in
            model.makePhotoRoom(image: image)
        }
        .background(MurmurTheme.paper.ignoresSafeArea())
        .task { await onThisDay.refreshAuthorization() }
        .onChange(of: scenePhase, initial: false) { _, phase in
            guard phase == .active else { return }
            Task { await onThisDay.refreshAuthorization() }
        }
    }
}
