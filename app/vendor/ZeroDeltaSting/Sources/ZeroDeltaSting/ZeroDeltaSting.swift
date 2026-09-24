import SwiftUI

/// Zero Delta convergence sting. Geometry and timing are frozen in
/// `design-system/zero-delta/assets/logos/zero-delta-sting.md`; this package is their
/// iOS/macOS expression. The mark is drawn in a 100x100 space and scaled to fit; the
/// lockup adds the wordmark below it.

/// Frozen geometry, shared by the mark and the lockup.
public enum ZeroDeltaGeometry {

    /// Lattice nodes, inset 0.8193 about the frame centroid so no vertex touches the frame.
    public static let nodes: [CGPoint] = [
        CGPoint(x: 50.000, y: 40.869), CGPoint(x: 30.746, y: 53.241),
        CGPoint(x: 69.254, y: 53.241), CGPoint(x: 50.000, y: 53.241),
        CGPoint(x: 50.000, y: 64.957), CGPoint(x: 40.578, y: 71.511),
        CGPoint(x: 59.422, y: 71.511), CGPoint(x: 30.746, y: 77.492),
        CGPoint(x: 69.254, y: 77.492), CGPoint(x: 50.000, y: 88.389),
    ]

    /// Index pairs into `nodes`. 0 top, 1 UL, 2 UR, 3 midU, 4 ctr, 5 ML, 6 MR, 7 LL, 8 LR, 9 bot.
    public static let edges: [(Int, Int)] = [
        (0, 1), (0, 2), (0, 3), (0, 7), (0, 8),
        (3, 1), (3, 2), (3, 4), (3, 5), (3, 6),
        (1, 4), (1, 5), (1, 7), (1, 9),
        (2, 4), (2, 6), (2, 8), (2, 9),
        (4, 5), (4, 6), (4, 9),
        (5, 6), (5, 7), (5, 9),
        (6, 8), (6, 9),
        (7, 8), (7, 9), (8, 9),
    ]

    public static let frameCentroid = CGPoint(x: 50, y: 65.667)
    public static let nodeRadius: CGFloat = 2.6
    public static let edgeWidth: CGFloat = 0.9
    public static let frameWidth: CGFloat = 3.4

    public static func framePath(in rect: CGRect) -> Path {
        let k = min(rect.width, rect.height) / 100
        var p = Path()
        p.move(to: CGPoint(x: 50 * k, y: 7 * k))
        p.addLine(to: CGPoint(x: 97 * k, y: 95 * k))
        p.addLine(to: CGPoint(x: 3 * k, y: 95 * k))
        p.closeSubpath()
        return p
    }

    /// Scatter offset a node starts from, in the 100x100 space. Glyphs use the same shape.
    public static func scatter(for index: Int) -> CGSize {
        let p = nodes[index]
        return CGSize(
            width: (p.x - frameCentroid.x) * 0.6 + CGFloat(index % 3 - 1) * 7,
            height: (p.y - frameCentroid.y) * 0.55 + (index % 2 == 1 ? 9 : -9)
        )
    }
}

/// `--ease-standard` from `guidelines/motion.card.html`. No spring, no bounce.
private func zdEase(_ duration: Double, delay: Double = 0) -> Animation {
    .timingCurve(0.2, 0, 0.2, 1, duration: duration).delay(delay)
}

/// Brand palette, resolved against the system appearance. The two columns are the same
/// two themes `render-sting.py` emits as GIFs, so a screenshot and the GIF agree.
/// These are deliberately not `.primary`/`.secondary`: the dark frame is bone grey, not
/// white, and the light frame is slate, not black.
public enum ZeroDeltaPalette {
    static func rgb(_ r: Int, _ g: Int, _ b: Int) -> Color {
        Color(red: Double(r) / 255, green: Double(g) / 255, blue: Double(b) / 255)
    }

    public static func frame(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? rgb(198, 201, 202) : rgb(46, 58, 58)
    }

    public static func lattice(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? rgb(95, 146, 181) : rgb(74, 124, 158)
    }

    public static func word(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? rgb(207, 195, 179) : rgb(46, 58, 58)
    }

    /// The sting's own backdrop, for hosts that do not already paint one.
    public static func background(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? rgb(34, 46, 46) : rgb(244, 242, 238)
    }
}

public struct ZeroDeltaMark: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    public var settled: Bool
    /// nil follows the system appearance via `ZeroDeltaPalette`.
    public var latticeColorOverride: Color?
    public var frameColorOverride: Color?

    /// true forces an instant, unanimated draw. nil follows Reduce Motion.
    public var suppressMotionOverride: Bool?

    public init(settled: Bool,
                suppressMotion: Bool? = nil,
                latticeColor: Color? = nil,
                frameColor: Color? = nil) {
        self.settled = settled
        self.suppressMotionOverride = suppressMotion
        self.latticeColorOverride = latticeColor
        self.frameColorOverride = frameColor
    }

    private var suppressMotion: Bool { suppressMotionOverride ?? reduceMotion }
    /// `settled` cannot be seeded from Reduce Motion in `init` -- the environment is not
    /// readable there -- so fold it in at render time instead of flashing an empty frame.
    private var shown: Bool { settled || suppressMotion }
    private var latticeColor: Color { latticeColorOverride ?? ZeroDeltaPalette.lattice(scheme) }
    private var frameColor: Color { frameColorOverride ?? ZeroDeltaPalette.frame(scheme) }

    public var body: some View {
        GeometryReader { geo in
            let k = min(geo.size.width, geo.size.height) / 100
            ZStack(alignment: .topLeading) {
                Path { p in
                    for (a, b) in ZeroDeltaGeometry.edges {
                        let s = ZeroDeltaGeometry.nodes[a], e = ZeroDeltaGeometry.nodes[b]
                        p.move(to: CGPoint(x: s.x * k, y: s.y * k))
                        p.addLine(to: CGPoint(x: e.x * k, y: e.y * k))
                    }
                }
                .stroke(latticeColor,
                        style: StrokeStyle(lineWidth: ZeroDeltaGeometry.edgeWidth * k, lineCap: .round))
                .opacity(shown ? 1 : 0)
                .animation(suppressMotion ? nil : zdEase(0.26, delay: 0.62), value: settled)

                ForEach(Array(ZeroDeltaGeometry.nodes.enumerated()), id: \.offset) { i, node in
                    let off = ZeroDeltaGeometry.scatter(for: i)
                    Circle()
                        .fill(latticeColor)
                        .frame(width: ZeroDeltaGeometry.nodeRadius * 2 * k,
                               height: ZeroDeltaGeometry.nodeRadius * 2 * k)
                        .position(x: node.x * k, y: node.y * k)
                        .offset(shown ? .zero : CGSize(width: off.width * k, height: off.height * k))
                        .animation(suppressMotion ? nil : zdEase(0.64, delay: Double(i) * 0.03), value: settled)
                        .opacity(shown ? 1 : 0)
                        // Alpha is a separate, shorter ramp than the travel, matching
                        // render-sting.py and zd_splash.xml. One animation for both would
                        // leave iOS fading over 640 where the other two use 240.
                        .animation(suppressMotion ? nil : .linear(duration: 0.24).delay(Double(i) * 0.03),
                                   value: settled)
                }

                ZeroDeltaGeometry.framePath(in: CGRect(origin: .zero, size: geo.size))
                    .trim(from: 0, to: shown ? 1 : 0)
                    .stroke(frameColor,
                            style: StrokeStyle(lineWidth: ZeroDeltaGeometry.frameWidth * k,
                                               lineCap: .round, lineJoin: .round))
                    .animation(suppressMotion ? nil : zdEase(0.42, delay: 0.48), value: settled)
            }
        }
        .aspectRatio(1, contentMode: .fit)
    }
}

/// Full lockup: mark, then `ZERO DELTA` converging the same way the vertices do.
///
/// This is a post-launch view. iOS launch screens are static storyboards and cannot run
/// code, so present it once on cold launch only, never on warm launch, and never in front
/// of a deep link or notification tap. See `ADOPTION-PROMPT.md`.
///
/// Laid out in the same 200x130 unit space as `zero-delta-lockup.svg`: the mark is 82 units
/// wide and centred, the wordmark is 21 units with 1.2 tracking on a baseline at y=114.
/// Everything derives from `width`, so the proportions cannot drift from the SVG.
///
/// Glyphs are positioned individually so each can animate, which forfeits pair kerning.
/// If tracking reads loose beside the SVG, swap in pre-outlined glyph paths rather than
/// nudging the tracking constant.
public struct ZeroDeltaSting: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme
    @State private var settled: Bool
    @State private var didFinish = false
    @State private var finishWork: DispatchWorkItem?

    public var width: CGFloat
    public var onFinished: (() -> Void)?
    /// Render the finished lockup without animating. For snapshots and static use;
    /// `onFinished` still fires so a host can treat it uniformly.
    public var startSettled: Bool
    /// Force motion on or off regardless of the system setting. nil follows Reduce Motion.
    /// Exists so a host can exercise the reduced path without `startSettled`, which also
    /// bypasses the timeline.
    public var suppressMotionOverride: Bool?

    private let word = Array("ZERO DELTA")
    private var visibleGlyphs: Int { word.filter { $0 != " " }.count }

    public init(width: CGFloat = 280,
                startSettled: Bool = false,
                suppressMotion: Bool? = nil,
                onFinished: (() -> Void)? = nil) {
        self.width = width
        self.startSettled = startSettled
        self.suppressMotionOverride = suppressMotion
        self.onFinished = onFinished
        _settled = State(initialValue: startSettled)
    }

    private var skipMotion: Bool { suppressMotionOverride ?? (reduceMotion || startSettled) }
    /// See `ZeroDeltaMark.shown`: avoids one blank frame before `onAppear` runs.
    private var shown: Bool { settled || skipMotion }

    /// `dx = (x - cx) * 0.6 + (i % 3 - 1) * 5`, `dy = i % 2 ? 9 : -9` -- the node formula
    /// from `zero-delta-sting.md`, applied to glyphs.
    ///
    /// `render-sting.py` takes each glyph's real x from PIL advance widths. SwiftUI lays the
    /// wordmark out with kerning and does not expose per-glyph x to a modifier, so the radial
    /// term is approximated from the glyph's normalised index across the wordmark's half-span
    /// (69 units, from the device-measured wordmark/triangle width ratio of 1.692 against an
    /// 82-unit triangle). Caps are near enough to even-width that the difference is not
    /// visible in motion; the settled frame is unaffected either way.
    static func glyphScatter(_ index: Int, of count: Int, u: CGFloat) -> CGSize {
        let halfSpan: CGFloat = 69
        let norm = count > 1 ? CGFloat(index) / CGFloat(count - 1) * 2 - 1 : 0
        return CGSize(width: (norm * halfSpan * 0.6 + CGFloat(index % 3 - 1) * 5) * u,
                      height: (index % 2 == 1 ? 9 : -9) * u)
    }

    /// Fires `onFinished` exactly once per view lifetime. `onAppear` can run again after a
    /// tab switch or sheet dismissal, so both the guard and the pending work item matter:
    /// the guard stops a second schedule, the work item stops an in-flight one.
    private func start() {
        guard !didFinish, finishWork == nil else { return }
        settled = true
        let finish = DispatchWorkItem {
            didFinish = true
            finishWork = nil
            onFinished?()
        }
        finishWork = finish
        // Even the immediate path is dispatched rather than called inline: firing inside
        // `onAppear` mutates host navigation state during the view update pass.
        DispatchQueue.main.asyncAfter(deadline: .now() + (skipMotion ? 0 : 1.228), execute: finish)
    }

    public var body: some View {
        let u = width / 200
        ZStack(alignment: .topLeading) {
            ZeroDeltaMark(settled: settled, suppressMotion: skipMotion)
                .frame(width: 82 * u, height: 82 * u)
                .offset(x: 59 * u, y: 0)

            HStack(spacing: 1.2 * u) {
                ForEach(Array(word.enumerated()), id: \.offset) { i, ch in
                    if ch == " " {
                        Color.clear.frame(width: 5 * u, height: 1)
                    } else {
                        let vis = word[0..<i].filter { $0 != " " }.count
                        Text(String(ch))
                            .font(.system(size: 21 * u, weight: .medium))
                            .foregroundStyle(ZeroDeltaPalette.word(scheme))
                            .offset(shown ? .zero : Self.glyphScatter(vis, of: visibleGlyphs, u: u))
                            .animation(skipMotion ? nil : zdEase(0.52, delay: 0.5 + Double(vis) * 0.026),
                                       value: settled)
                            .opacity(shown ? 1 : 0)
                            .animation(skipMotion ? nil : .linear(duration: 0.22).delay(0.5 + Double(vis) * 0.026),
                                       value: settled)
                    }
                }
            }
            .position(x: 100 * u, y: 106.5 * u)
        }
        .frame(width: 200 * u, height: 130 * u, alignment: .topLeading)
        .onAppear { start() }
        // A sting that is torn down early -- deep link, fast auth, user dismissal -- must
        // not call back after it is gone. Without this the timer outlives the view, mutates
        // @State on an unmounted view, and routes the host a second time.
        .onDisappear {
            finishWork?.cancel()
            finishWork = nil
        }
    }
}

#Preview {
    ZeroDeltaSting()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 0.133, green: 0.180, blue: 0.180))
}
