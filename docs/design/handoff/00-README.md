# CortexSim — Front-End Redesign Handoff Package

> **For:** Claude Design (and any designer) tasked with a fundamental front-end
> redesign of CortexSim.
> **Posture:** *Evolve the v2 baseline.* The guided-stepper IA, Cortex-green
> token system, and Targets/Launch model shipped in this repo are the
> **foundation** — elevate and extend them to high fidelity; don't start from
> zero. Each screen below carries an explicit **Keep / Fix / Elevate** note.

## What this product is

CortexSim is an **operator console for a Palo Alto Networks POV** (proof of
value). A Domain Consultant (DC) uses it to fire controlled, high-fidelity
attack signals into a customer's **Cortex** tenant (XSIAM/XDR) and prove the
customer's detections fire. It is *not* a SOC analyst dashboard and *not* a red
team C2 — think "detection-quality QA engine." The deliverable a DC walks away
with is an **exportable POV report** (coverage %, MTTD, ATT&CK Navigator layer,
narrative).

## Package contents

| File | What it is |
|---|---|
| `00-README.md` | This index + how to run the live reference |
| `01-design-spec.md` | Comprehensive spec: product, users, jobs-to-be-done, principles, IA, v2 baseline status, per-screen requirements, constraints, success criteria |
| `02-screen-inventory.md` | Every reachable surface catalogued + annotated current-state screenshots in `screens/` + states + Keep/Fix/Elevate |
| `03-api-data-map.md` | The real API endpoints and response shapes feeding each screen — so the redesign is grounded in available data, not invented fields |
| `screens/*.png` | Current-state captures (1440×900 @2×) of all surfaces |

**Companion references (same repo):**
- `../cortexsim-design-handoff.md` — the canonical **design-system / token** spec (color, type, spacing, motion, components). Tokens are summarized in `01` and live in full here.
- `../console-redesign-v2.md` — the **strategy decisions** behind v2 (why the stepper, why the Targets model).

## How to run the live reference

The current v2 build is functional and is the reference the screenshots came from.

```bash
# 1. Backend (FastAPI + the React build it serves), production-faithful:
cd cortex-pov-engine
docker compose up -d          # serves the app at http://localhost:8888

# 2. For live design iteration with hot-reload (proxies /api → :8888):
cd ui && npm install && npm run dev   # http://localhost:5173
```

The console theme is the default. `?theme=legacy` reaches the deprecated
light theme (ignore for the redesign).

## Tech constraints the redesign must honor

- **React 18** SPA, **plain CSS** (no Tailwind/CSS-in-JS) — the entire theme is
  CSS custom properties in `ui/src/styles/cortex-console.css`. A designer can
  change token values and the live app updates.
- Built with **Vite**; production build is copied into `core/static/` and served
  by FastAPI as a static SPA. No SSR.
- Fonts already loaded: Funnel Display, Archivo, JetBrains Mono, Fraunces.
- Must remain **keyboard-driven** (⌘K palette, ⌘L launch, ⌘E export, ⌘/ help)
  and projector-readable (theater mode).

## The one open brand decision

Primary accent is **Cortex green `#6CC24A`** (working value). If PANW has an
official Cortex green hex, it drops into the five `--c-action*` tokens and the
whole UI re-tints — nothing else changes.
