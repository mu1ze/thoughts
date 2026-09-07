# thoughts

A minimalist blog where we dump things we thought about. Opinions are loud and fact-checking is a vibe.

**Live site:** https://mu1ze.github.io/thoughts/ (once Pages is enabled)

## How it works

- `index.html` — the whole site. One file. No build step, no framework, no JavaScript dependencies.
- `posts/` — raw markdown of every post (source of truth for the writing).
- `assets/` — images and other media.

## Publishing a new thought

### The lazy way (ask Hermes)
Just tell Hermes the idea. It writes the post, updates `index.html`, commits, and pushes.

### The manual way
1. Write your post as markdown in `posts/YYYY-MM-DD-slug.md`
2. Open `index.html` and add:
   - a new `<a class="post-row">` block in the **Latest** section (title + date + one-line teaser)
   - a new `<article id="slug">` section with the essay body, matching the existing structure
3. Commit and push:

```bash
git add -A && git commit -m "post: your title" && git push
```

GitHub Pages redeploys automatically in ~1 minute.

## Design notes

- Paper background, serif body, single accent color. No gradients, no cards, no emoji.
- Surface: editorial. One idea per section, whitespace is a feature.
- Mobile-friendly via responsive type scale; respects `prefers-reduced-motion`.
