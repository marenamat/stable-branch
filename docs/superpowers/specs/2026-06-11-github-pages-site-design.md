# GitHub Pages Site Design — stable-branch

**Date:** 2026-06-11  
**Status:** Approved

## Goal

A single-page marketing/explainer site at `https://marenamat.github.io/stable-branch/` aimed at open-source maintainers running stable/LTS release lines. It should explain what stable-branch does by naming their pain first, then showing the tool. Tone is self-deprecating and fun. Comic Sans everywhere.

## File

`docs/index.html` — self-contained HTML + CSS, no build step, no dependencies. GitHub Pages serves `docs/` automatically.

The existing screenshot stays at `docs/stable-branch-releasing-in-bird.png` and is referenced with a relative path.

## Aesthetic

- **Font:** Comic Sans MS (with `cursive` fallback) — site-wide, no exceptions
- **Colors:** Dark background (#1a1a2e or similar), light text, colorful branch-palette accents (matching the tool's own group-color palette)
- **Meme cards:** Styled HTML `div` blocks — no external image hotlinks. Two-panel Drake format and single-panel captioned-image-style using emoji + CSS
- **Code blocks:** monospace exception (Comic Sans for code is illegible), dark background, syntax highlight via color only

## Sections

### 1. Nav
- Wordmark: `stable-branch`
- Right side: GitHub link (`github.com/marenamat/stable-branch`)
- Minimal, sticky

### 2. Hero
- Headline: "Managing stable release branches is a special kind of pain."
- Subtext: "Every bugfix. Every security patch. Five branches. Good luck remembering which commits landed where."
- CTA button: "Show me the thing ↓"

### 3. Meme #1 — Drake format (CSS two-panel card)
- ❌ panel: `git cherry-pick a3f91b` × N branches, then manually checking which commits landed where
- ✅ panel: drag

### 4. Screenshot
- Full-width image: `stable-branch-releasing-in-bird.png`
- Caption: "Actual screenshot from the BIRD router project. Yes, those are real commits."
- Brief paragraph: "This is stable-branch. It's not much, but it's honest work."

### 5. Feature cards
Four cards in a responsive grid:

| Card | Headline | Body |
|---|---|---|
| Matching | Same commit, five branches, one color | Commits that are the same logical change are automatically detected and shown in matching colors across all branches. |
| Moving | Drag to cherry-pick | Drag a commit to another branch column to cherry-pick it. Drag within a column to reorder. Queue up multiple drops; they run in order. |
| Hiding | Collapse the noise | Click `−` to hide a commit. It collapses to a thin strip. Hidden state persists across restarts. Auto-hide merge commits or filter by commit body headers. |
| Editing | Fix the message everywhere at once | Edit a commit's message or author. If the same logical commit lives on N branches, one Save updates all of them. |

### 6. Meme #2 — "This is fine" style
Single-panel captioned card: 🐶☕🔥  
Caption: "me, four terminal windows open, trying to remember if I cherry-picked that patch onto stable/v2"

### 7. Install
```
pip install stable-branch
stable-branch /path/to/repo main stable/v1 stable/v2
```
Copy button (JS). Note: requires Python 3.12+.

### 8. Meme #3 — Closing bit
Short card: "It's vibecoded. PRs welcome (also vibecoded)."  
GitHub link button.

### 9. Footer
- GitHub repo link
- "GNU GPL licensed — check the repo"

## Implementation Notes

- No JavaScript frameworks. Vanilla JS only, and only for the copy button.
- Responsive: single column on mobile, grid on desktop (CSS grid/flexbox).
- The meme cards use CSS pseudo-elements and emoji, no external images.
- `<meta>` OG tags for the GitHub social preview.
