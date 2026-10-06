import UIKit

/// Keeps the edge-swipe back gesture working on screens that hide the system
/// navigation bar (Your courses uses its own Back to Today header).
///
/// UIKit disables `interactivePopGestureRecognizer` when the bar is hidden
/// because its default delegate is the bar. Taking over the delegate restores
/// it, and limiting it to stacks deeper than the root avoids a stuck pop.
extension UINavigationController: @retroactive UIGestureRecognizerDelegate {
    override open func viewDidLoad() {
        super.viewDidLoad()
        interactivePopGestureRecognizer?.delegate = self
    }

    public func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        gestureRecognizer === interactivePopGestureRecognizer && viewControllers.count > 1
    }
}
