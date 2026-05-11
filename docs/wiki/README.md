# `docs/wiki/`

Source of truth for the GitHub wiki at
**https://github.com/hankthebldr/cortex-pov-engine/wiki**.

The `.github/workflows/wiki-sync.yml` workflow clones the wiki repo
(`<repo>.wiki.git`) on every merge to `main`, copies every markdown
file from this directory over, and force-pushes. Direct edits in the
GitHub wiki UI are overwritten on the next merge.

## Page conventions

- One `.md` file per page; the filename (minus `.md`) is the wiki
  page title GitHub renders.
- Wiki page links use `[[Page Name]]` (GitHub wiki double-bracket
  syntax). Page names are the file's basename with hyphens
  converted to spaces — `Detection-Planes.md` is linked as
  `[[Detection Planes]]`.
- `_Sidebar.md` and `_Footer.md` are special — GitHub renders them on
  every page. Do not link `[[_Sidebar]]` etc.

## Adding a page

1. Create `docs/wiki/My-Page.md`.
2. Cross-link from `_Sidebar.md`, `Home.md`, and any related pages.
3. Open a PR; the workflow will publish on merge.

## Local preview

GitHub wiki uses GFM. Any GFM renderer (`grip`, `glow`, IDE preview)
will render acceptably; the actual sidebar / footer behaviour only
appears once published.
