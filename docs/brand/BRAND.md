# APEX brand guide

## The mark: Summit

The tip of the A lifts clear of its base: the one point that rises above the rest. Every edge sits on an
exact 60° angle. The mark avoids trading clichés (arrows, bulls, candlesticks) so it stays ownable.

| File | Use |
|---|---|
| `apex-mark.svg` | Symbol, full colour, light backgrounds |
| `apex-mark-reversed.svg` | Symbol on dark backgrounds |
| `apex-lockup.svg` / `apex-lockup-reversed.svg` | Symbol + wordmark, light / dark backgrounds |
| `apex-mark-black.svg`, `-white.svg`, `-mono-0a7d86.svg` | One-colour versions (print, stamps, single-ink) |
| `app-icon.svg`, `apex-mark-app-icon.svg`, `apex-mark-square.svg` | Square tiles for app icons and avatars |
| `apex-mark-favicon.svg` | Browser-tab icon |

The dashboard serves its own copies from `src/apex/api/static/brand/` (favicon.svg adapts to dark browser tabs,
favicon.ico for older browsers, apple-touch-icon.png for phones).

## Colour

| Name | Hex | Role |
|---|---|---|
| Summit teal | `#0A7D86` | The summit of the mark; dashboard accent (active tab, price line) |
| Summit teal, dark UI | `#43C3CB` | Same role on dark backgrounds |
| Ink | `#142430` | Base of the mark, wordmark, body text |
| Chart paper | `#EDF1F4` | Dashboard background (light) |
| Night | `#0F181E` | Dashboard background (dark) |

Teal is reserved for the brand and for the one thing on a page that matters most. Gains and losses use
their own green and red, never teal.

## Rules

- **Clear space:** keep a margin around the mark equal to the height of the summit triangle.
- **Minimum size:** symbol 16 px tall on screen; lockup 80 px wide.
- **Don't** rotate, stretch, outline, add shadows or gradients, recolour the summit to green/red, or close the
  gap between the summit and the base (the gap is the idea).
- The wordmark is drawn as shapes, not typed, so no font is needed. Its A is the same open chevron as the mark.

## Dashboard look

`src/apex/api/static/apex.css` holds the dashboard theme as CSS variables on `:root`, with a dark-mode set.
The SIMULATED / NO LIVE TRADING banner is always the first element, sticky, striped and never dismissable.

Trademark clearance has not been checked; do a professional search before using APEX commercially.
