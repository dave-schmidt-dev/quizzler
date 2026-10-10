import QuizzlerKit
import UIKit

/// No study/network work is introduced; one process adapter owns local diagnostics.
extension QuizzlerAppDelegate {
    func recordDiagnosticsStart() {
        guard !diagnosticsStarted else { return }
        diagnosticsStarted = true
        // One process observer covers SwiftUI scenes; UIKit skips the delegate
        // background callback for scene-based apps. Selector observers are removed
        // automatically on deallocation at the supported platform floors.
        diagnosticsNotificationCenter.addObserver(self, selector: #selector(diagnosticsDidEnterBackground(_:)),
            name: UIApplication.didEnterBackgroundNotification, object: nil)
        Task { await diagnostics.lifecycle(.start) }
    }

    @objc func diagnosticsDidEnterBackground(_: Notification) {
        requestDiagnosticsBackgroundFlush()
    }

    /// Coalesces overlapping process notifications and returns immediately. An OS suspension can
    /// still interrupt the admitted tail; a Task is not a background-time guarantee.
    func requestDiagnosticsBackgroundFlush() {
        guard diagnosticsBackgroundTask == nil else {
            diagnosticsBackgroundPending = true
            return
        }
        diagnosticsBackgroundTask = Task { [weak self] in
            guard let self else { return }
            repeat {
                diagnosticsBackgroundPending = false
                await diagnostics.lifecycle(.background)
                if let diagnosticsBeforeBackgroundFlush { await diagnosticsBeforeBackgroundFlush() }
                _ = await diagnostics.flush()
            } while diagnosticsBackgroundPending
            diagnosticsBackgroundTask = nil
        }
    }
}
