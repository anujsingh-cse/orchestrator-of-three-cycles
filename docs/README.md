# Documentation Deployment

This folder contains the documentation site for **Orchestrator of Three Cycles**.

## Structure

```
docs/
├── index.html      # Main documentation page (styled)
├── index.md        # Markdown source
├── style.css       # Custom styles
└── README.md       # This file
```

## Local Preview

Open `index.html` directly in a browser, or serve locally:

```bash
# Python
cd docs && python -m http.server 8000

# Node.js
npx serve docs

# VS Code
# Right-click index.html → "Open with Live Server"
```

## Deploy to GitHub Pages

### Automatic (Recommended)

The workflow at `.github/workflows/docs.yml` deploys automatically on push to `main`.

1. Go to **Settings → Pages**
2. Source: **GitHub Actions**
3. Push to `main` — deployment runs automatically

### Manual

```bash
# Build not needed (static files)
# Just push the docs folder
git add docs/
git commit -m "docs: update documentation"
git push origin main
```

## Customization

### Colors & Theme

Edit `style.css` — CSS variables at the top:

```css
:root {
  --bg: #0a0f1a;           /* Background */
  --accent: #00d4aa;       /* Primary accent (teal) */
  --font-sans: 'Inter';    /* UI font */
  --font-mono: 'JetBrains Mono'; /* Code font */
}
```

### Content

Edit `index.md` for content, then regenerate `index.html` (or edit HTML directly).

### Navigation

Sidebar links in `index.html` — update `href` attributes to match section IDs.

## Features

- ✅ Dark/light mode (auto-detects system preference)
- ✅ Responsive (mobile sidebar drawer)
- ✅ Syntax highlighting (basic)
- ✅ Smooth scroll + active section highlighting
- ✅ Accessible (skip link, ARIA, semantic HTML)
- ✅ Print-friendly styles
- ✅ Zero dependencies (vanilla HTML/CSS/JS)