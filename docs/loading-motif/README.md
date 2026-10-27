# Quizzler loading motif

Package date: 2026-10-11. Design source created 2026-10-10; owner accepted 2026-10-11. These offline references reproduce the accepted local prototype; native integration is not complete.

Open preview.html directly in a browser. It is the accepted, tested preview board with no network or build dependency. This app's panel is marked with data-app="quizzler". The board keeps the other approved panels visible so the shared controls and animation behavior remain exactly as verified. motif.svg is this app's settled full mark with explicit vector colors.

## Geometry, timing, and colors

100×100 viewBox; two rounded card outlines, question mark, and lower-right check. Card transforms rotate around their centers.

Waiting: rear and front card outlines exchange opposing ±30° Y rotations on an 1,800 ms cycle; the check stays visible. Progress reveals both outlines from 0–100% and shows the check at completion. Reduced motion settles the full stack.

Dark: cyan #00E5EF, white question, green #98D99E check. Light: cyan #00747c, question #193338, check #38651f. Static SVG uses dark-surface colors.

## Native integration notes

Integrate while a requested Quizzler study set loads or restores. Remove it as soon as that set is actually ready. Drive the loader from actual readiness, remove it immediately on success, and show initialization failures with an actionable error. Do not fake completion or impose a minimum duration. Add native tests for waiting, progress, reduced motion, lifecycle/readiness, and errors; inspect dark/light themes and a small viewport.
