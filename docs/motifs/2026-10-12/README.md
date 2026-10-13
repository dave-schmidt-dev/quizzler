# Quizzler motif incorporation guide (approved 2026-10-12)

Status: nothing in this package is integrated into the app yet. Approved 2026-10-12.

This package is the four owner-approved Quizzler motif designs: settled SVG assets, frame
strips, and the motion, color and pixel-match contract in [approved.json](approved.json) plus
the exporter's verify.json. The approving design comparison gallery lives outside this repo.

Supersedes the local, untracked `docs/loading-motif/` draft (2026-10-11): where the two
packages overlap, this package wins; that draft is not authoritative.

## Approved elements

| id | title | Owner option | Kind | Sizes | Multicolor | Primary asset |
| --- | --- | --- | --- | --- | --- | --- |
| loading-card-shuffle | Loading: Card shuffle, front card slides out and passes behind | rev3: Card shuffle: front card slides out, passes behind | animated | 20, 64 | yes | assets/loading-card-shuffle-20-dark.svg (settled/reduced frame; the 64 px form has no SVG) |
| section-progress-eight-card | Section progress: eight-card sample (5 of 8 reviewed) | eight-card sample (liked 2026-10-11) | static | component, 496x66 | yes | frames/section-progress-eight-card-dark.png (no SVG exported) |
| continue-stack-forward-arrow | Continue: Stack plus forward arrow | A: Stack plus forward arrow | static | 20, 64 | no | assets/continue-stack-forward-arrow-64-dark.svg (template also ships) |
| report-clean-flag-card-label | Report question: Clean flag and card label | C: Clean flag and card label | static | 20, 64 | no | assets/report-clean-flag-card-label-64-dark.svg (template also ships) |

None of these four is a stock pick, so no SF Symbol rows appear here.

## Where each element goes

The UI stack is SwiftUI on iOS 17.0 and Mac Catalyst (`app/project.yml:4`, `app/project.yml:26`).
Design tokens live in `app/QuizzleriOS/DesignSystem/QuizzlerTheme.swift`. The app forces the dark
scheme in every scene root (`app/QuizzleriOS/Launchpad/LaunchpadView.swift:260`,
`app/QuizzleriOS/Launchpad/TodayView.swift:368`,
`app/QuizzleriOS/Quiz/QuestionLeitnerViews.swift:212`,
`app/QuizzleriOS/Issues/ReportQuestionView.swift:194`).

### loading-card-shuffle

Replaces the indeterminate activity indicators. Primary call sites:

- `app/QuizzleriOS/Launchpad/LaunchpadView+StudyContent.swift:146`, the `PackLoadingView` label
  (`arrow.triangle.2.circlepath`, accessibility id `pack-loading` at line 155).
- `app/QuizzleriOS/Launchpad/LaunchpadView+StudyContent.swift:231`, the `ProgressView()` in
  `StudyPreparationView`.

Other indeterminate spinners: `app/QuizzleriOS/Launchpad/SessionSummaryView.swift:198`,
`app/QuizzleriOS/Issues/ReportQuestionView.swift:203`,
`app/QuizzleriOS/Launchpad/SettingsView.swift:129` and `:188`,
`app/QuizzleriOS/Quiz/QuestionLeitnerViews.swift:198`, and the sync glyphs at
`app/QuizzleriOS/Launchpad/CoursesView.swift:35` and `:40`. Keep the determinate bars at
`app/QuizzleriOS/Launchpad/LaunchpadView+Header.swift:38` and
`app/QuizzleriOS/Launchpad/CoursesView.swift:185`; they carry real fractions.

### section-progress-eight-card

New surface: no current call site exists for a discrete eight-card progress strip. Most likely
host is the session progress row in `sessionHeaderRow`
(`app/QuizzleriOS/Launchpad/LaunchpadView+Header.swift:37-49`): it is the app's only per-run
reviewed count (linear bar plus the `SessionPosition.counterLabel` "1/10" defined at
`app/QuizzleriOS/Quiz/QuestionShellView.swift:10-25`), which is what "5 of 8 reviewed" reads as.
Pack coverage at `app/QuizzleriOS/Launchpad/StudyProgressView.swift:47-105` ("Questions seen
N of M", also `CoursesView.swift:184-189`) is the second candidate.

### continue-stack-forward-arrow

There is no "Continue" button with an icon today. The trailing-arrow glyph this asset replaces
appears at:

- `app/QuizzleriOS/Launchpad/TodayView.swift:83` (resume saved session, id `today-resume-session`)
- `app/QuizzleriOS/Launchpad/TodayView.swift:200` (Learn new questions)
- `app/QuizzleriOS/Launchpad/TodayView.swift:225` (Retry missed)
- `app/QuizzleriOS/Launchpad/TodayView.swift:312` (CS0-004 learning lab)
- `app/QuizzleriOS/Launchpad/StudyProgressView.swift:181` (Start due review)
- `app/QuizzleriOS/Launchpad/StudyProgressView.swift:238` (Retry missed)

The only control whose accessibility label says "Continue" is the text-only Next session button
at `app/QuizzleriOS/Launchpad/SessionSummaryView.swift:263-281` (label at line 272); it has no
icon and is the other plausible home for the 64 px form.

### report-clean-flag-card-label

Replaces the `Image(systemName: "flag")` at `app/QuizzleriOS/Quiz/QuestionShellView.swift:209`
inside the report button (label "Report" at line 214, value "Question ID ..." at line 215,
identifier `question-report` at line 216). Use the 20 px asset in the 44 pt bottom-bar slot.
The report sheet (`app/QuizzleriOS/Issues/ReportQuestionView.swift`) is the natural host if a
large report action is ever added; its send button at `ReportQuestionView.swift:222-241` is
text-only today.

## How to implement

### loading-card-shuffle (animated)

- Build one SwiftUI card-stack view and drive it from a deterministic time source on iOS 17:
  `TimelineView(.animation)` with poses computed from elapsed time, or `KeyframeAnimator`.
  `motion.drivenBy` is `js`, so no platform animation semantics are implied.
- Cycle and phases: `cycleMs` 3200, two 1600 ms turns, the second turn repeats with the card
  roles swapped. Within a turn: rest 0 to 50 ms, slide-out 50 to 500 ms (smoothstep), swap 500
  to 640 ms with the depth swap at 570 ms, slide-back 640 to 1360 ms, settled 1360 to 1600 ms.
- Poses from `motion.poses`: 64 px stage front (x 29.5, y 35, scale 1, rotation 0, dim 0), back
  (x 36.5, y 28, scale 0.92, rotation 2, dim 0.32), out (x 15, y 31, scale 1, rotation -2). The
  20 px mirror has its own table: front (9.1, 11), back (13.6, 8), out (6.2, 9.6), same scale,
  rotation and dim values. Keep minimum swap displacement: 19 px at 64, 6 px at 20.
- Indeterminate activity only. This glyph must never imply progress or a percentage.
- Reduced motion: when `@Environment(\.accessibilityReduceMotion)` is true, render the settled
  frame, `assets/loading-card-shuffle-20-dark.svg` (or the light contrast version), at rest pose.
  The first frame of turn 0 is card Q 18 front, Q 19 behind and dimmed.
- The 64 px form is HTML/CSS on the source page, not SVG: absolutely positioned cards, a font
  "?" glyph, a CSS box-shadow, a `::after` dim overlay and a border-triangle notch. Rebuild it in
  SwiftUI from those parts and the `poses.large64` numbers. The package's `frames/` strips and
  the pose table are its reference; only the 20 px mirror is vector.
- Visual acceptance: `frames/loading-card-shuffle-dark.png` and
  `frames/loading-card-shuffle-light.png`.

### section-progress-eight-card (static, multicolor)

- No SVG ships (the two-digit text labels would need `<text>`), so build it as a SwiftUI view
  from the `component` measurements: eight cards 55.875 x 66 pt, 7 pt gap, 7 pt corner radius,
  8 x 8 pt notch, 11 px monospaced semibold labels 01 through 08, 5 of 8 completed. Its colors
  are measured computed styles with no cssVar resolution, and its `a11yLabel` source is derived
  because the page list has no aria-label, so the app supplies that label.
- Token map for the dark palette, from `app/QuizzleriOS/DesignSystem/QuizzlerTheme.swift`:
  completed stroke `#3dbfb8` to `primaryCyan` (line 18), completed text `#f5f5f5` to
  `textPrimary` (line 24), notch `#87d787` to `success` (line 30) or `answerNotch` (line 33),
  pending fill `#292932` to `raisedCard` (line 12), pending stroke `#3b3b46` to `border`
  (line 15), pending text `#9a9aa2` to `textMuted` (line 27).
- Flagged without a matching token: completed fill `#2f7372` (the closest token, `primaryCyan`,
  is much brighter). Do not invent a token for it.
- Flagged without a matching token: every light value (`#d8efed`, `#202624`, `#558b58`,
  `#fafaf8`, `#c9cdc9`, `#53615b`). `QuizzlerTheme` holds dark constants only, and the app is
  dark-only, so the light frame is a contrast check, not a shipping palette. All four elements
  carry the same `lightThemeNote`.
- Closest font token: `QuizzlerTheme.metadataFont` (line 64) is `.caption` monospaced, not the
  measured 11 px; the measured radius is 7 pt against `cardRadius` 8 (line 48). Use measured
  values where tokens are not exact.
- Visual acceptance: `frames/section-progress-eight-card-dark.png` and the light strip.

### continue-stack-forward-arrow (static, single ink)

- Import `assets/continue-stack-forward-arrow-20-template.svg` and
  `assets/continue-stack-forward-arrow-64-template.svg` into
  `app/QuizzleriOS/Assets.xcassets` with Preserve Vector Data and template rendering (the
  templates are black ink on transparent, masks keep their white/black). Use the 20 px template
  for the trailing 20 pt glyphs and the 64 px template only for a 64 pt placement.
- Tint with `QuizzlerTheme.textMuted` (line 27), the nearest token to the ink `#9a9aa2` in both
  themes. The 20 px form's `a11yLabel` is null: mark inline decorative uses
  `.accessibilityHidden(true)` and keep the host row's label. The 64 px form's label is
  "Continue with a stack and forward arrow".
- Never scale the 64 px asset down to 20 pt; the 20 px artwork uses its own geometry and heavier
  strokes. Visual acceptance: the `frames/continue-stack-forward-arrow-*.png` strips.

### report-clean-flag-card-label (static, single ink)

- Same import path as above with `assets/report-clean-flag-card-label-20-template.svg` and
  `assets/report-clean-flag-card-label-64-template.svg`; tint with `QuizzlerTheme.textMuted`.
- Keep the existing button behavior: label "Report", value "Question ID ...", identifier
  `question-report` (`app/QuizzleriOS/Quiz/QuestionShellView.swift:214-216`). The 20 px form has
  no recorded `a11yLabel`, so keep the button's label rather than the asset's. The 64 px label
  is "Report question with a compact flag icon", for use only if the sheet gains a large action.
- Visual acceptance: the `frames/report-clean-flag-card-label-*.png` strips.

### Shared rules

- Use each element's recorded `a11yLabel` only where the glyph stands alone. Where a host
  already labels the surface (for example `PackLoadingView` labels "Loading question packs" at
  `LaunchpadView+StudyContent.swift:149`), combine into one accessibility element instead of
  double-announcing. Note that the loading motif's only recorded label names the 20 px form; no
  64 px label was recorded.
- Light-theme assets exist for all four elements. They are neutral contrast checks on the design
  page, so the app should ship the dark files and treat the light files as review material.
- verify.json records the pixel match: an exported SVG swapped in place of the live element
  matches within 2 channel levels for settled assets.

## Acceptance checks

Framework: XCTest. Headless unit and render tests belong in `app/QuizzleriOSTests` and in the
`QuizzlerSnapshotTests` target (`app/project.yml:172-177`, currently the renderer inventory in
`app/QuizzleriOSTests/QuestionRendererSnapshotTests.swift`). Run the headless gate with
`app/test-gate.sh --phase native`. Screen-seizing XCUITest automation in
`app/QuizzleriOSUITests` is a release-milestone step only (INV-14): `app/test-gate.sh --phase ui`,
`app/test-gate.sh --quick accessibility`, `bash scripts/mac_milestone_ui_tests.sh`.

Add, per element:
1. Render or snapshot tests at both shipped sizes (20 and 64, or 496 x 66 for the section strip)
   using `ImageRenderer` in the `QuizzlerSnapshotTests` target: dark appearance primarily, plus
   one light SVG/component render. Package `frames/*.png` stay the reviewer's reference.
2. A reduced-motion test: render the loading motif with `accessibilityReduceMotion` true and
   assert it matches the settled SVG pose rather than an animation frame.
3. An accessibility-label test: assert the recorded strings for the 64 px forms and that 20 px
   inline placements are hidden from accessibility while their host row stays labeled. Label
   assertions through XCUITest follow `app/QuizzleriOSUITests/AccessibilityUITests.swift`
   (`question-report`, `question-skip`) and are milestone-only.
4. Animated frame checks: expose the loading pose as a pure function of time and unit-test the
   contract milestones 0, 250, 480, 600, 850, 1100, 1450, 1600, and 3200 ms, including the
   570 ms depth swap. Snapshot the rendered frames at a few `sampleTimesMs` values, for example
   0, 570, 850, 1600 and 3200.
5. A component test for the section strip: 8 cards, exactly 5 completed, 7 pt gap, labels 01
   through 08, and no token invented for the flagged `#2f7372` fill.

Do not add screen-seizing automation per change. The milestone commands above are the place for
end-to-end checks.

## Asset inventory

- loading-card-shuffle: `assets/loading-card-shuffle-20-dark.svg`,
  `assets/loading-card-shuffle-20-light.svg`, `frames/loading-card-shuffle-dark.png`,
  `frames/loading-card-shuffle-light.png`. No template (multicolor); no 64 px SVG.
- section-progress-eight-card: `frames/section-progress-eight-card-dark.png`,
  `frames/section-progress-eight-card-light.png`. No SVG, no template; measurements in
  approved.json `component`.
- continue-stack-forward-arrow: `assets/continue-stack-forward-arrow-20-dark.svg`,
  `assets/continue-stack-forward-arrow-20-light.svg`,
  `assets/continue-stack-forward-arrow-64-dark.svg`,
  `assets/continue-stack-forward-arrow-64-light.svg`,
  `assets/continue-stack-forward-arrow-20-template.svg`,
  `assets/continue-stack-forward-arrow-64-template.svg`,
  `frames/continue-stack-forward-arrow-dark.png`,
  `frames/continue-stack-forward-arrow-light.png`.
- report-clean-flag-card-label: `assets/report-clean-flag-card-label-20-dark.svg`,
  `assets/report-clean-flag-card-label-20-light.svg`,
  `assets/report-clean-flag-card-label-64-dark.svg`,
  `assets/report-clean-flag-card-label-64-light.svg`,
  `assets/report-clean-flag-card-label-20-template.svg`,
  `assets/report-clean-flag-card-label-64-template.svg`,
  `frames/report-clean-flag-card-label-dark.png`,
  `frames/report-clean-flag-card-label-light.png`.
