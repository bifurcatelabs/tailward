# v3.0.0 — Tauri scope + design

Living scoping doc for the v3.0.0 milestone. Captures the architectural
decisions, open questions, and milestone sequencing so the
implementation phase is execution-shaped, not a 200-question
wandering session.

**Pairs with:** `~/.claude/projects/C--warden/memory/project_rebrand.md`
(public name landing), `project_knowledge_roi.md` (skill-surface
rationale), `project_v2_cutover_plan.md` (light-path cutover model).

## Goal

Ship tailward as a **downloadable, signed, native application** for
macOS / Windows / Linux. Users on the GitHub Releases page download a
single binary, double-click, app launches with the SPA in a native
window. No Python install required.

PyPI / pipx install path is **preserved**, not replaced. Two
audiences: developers who already have a Python runtime and prefer
`pipx install tailward`; everyone else who wants a downloadable app.

## Non-goals

- Replace the existing pipx-install path. Stays as a first-class
  audience; some users will always prefer it (CI environments,
  developer workflows, headless deploy).
- Rewrite the daemon in Rust. The Python daemon stays as-is.
- Move to a web-hosted SaaS. The local-first architecture is
  load-bearing for the trust-layer thesis (`project_no_injection_position.md`,
  `project_warden.md`).
- Active mode revival. The historical artifact stays historical; v3
  is a packaging milestone, not an architectural reversal.

## Architecture decision: subprocess + bundled Python

Three options were considered:

**A. Daemon-as-subprocess, expects user-installed Python.** Smallest
change, but defeats the "downloadable, just works" promise — falls
back to the same friction pipx-install already has.

**B. Daemon-as-library (Python embedded via PyO3 / PyOxidizer).**
Single binary, no subprocess. But Tauri + embedded Python is uncommon
territory, finicky to build cross-platform, loses the "daemon runs
standalone via CLI" property, requires teaching Rust about Python's
async event loop.

**C. Daemon-as-subprocess + bundled Python sidecar.** Tauri's
`externalBin` config bundles a Python runtime as an app resource;
on launch, Tauri spawns the daemon using that bundled interpreter.
User doesn't need Python on their machine. Daemon stays an
independent process.

**Decision: C.** Maintains the simplicity of A while achieving B's
single-app distribution. The cost is binary size (~50-80 MB for the
bundled Python + dependencies) — acceptable for a desktop app, well
within ecosystem norms.

### What this means concretely

- Tauri's main process spawns `python -m modmcp.cli daemon run` (or
  similar) as a subprocess on app launch, using the bundled Python.
- Daemon binds to `127.0.0.1:7878` (or a free port chosen at runtime
  if 7878 is occupied — see Open Questions).
- Tauri's webview loads `http://127.0.0.1:<port>/` once the daemon is
  ready. SPA renders unchanged.
- On app quit: Tauri sends SIGTERM to the daemon subprocess, waits
  briefly, force-kills if needed.
- Power users running `warden daemon start` outside the Tauri app
  continue to work — same code path, same state directory.

### Bundling specifics (open: needs research)

How Python+Tauri apps typically bundle a Python runtime varies. Three
patterns observed in the wild:

1. **PyInstaller** — produce a single-file .exe / .app of the daemon,
   ship as a Tauri `externalBin`. Simplest. Ships only what the
   daemon imports. Probably the right call.
2. **Bundled Python distribution** (e.g., python-build-standalone) —
   ship a Python interpreter, install dependencies into a vendored
   site-packages, run as a sidecar. More flexible but larger bundle.
3. **Briefcase / BeeWare** — Python-app packagers with their own
   ecosystem. Wrong shape for Tauri integration but worth knowing
   about.

**Provisional choice: PyInstaller.** Validate by building a test
sidecar early in the v3 implementation phase; pivot if it doesn't
cooperate with Tauri's `externalBin` cleanly.

## IPC pattern: HTTP localhost (no change)

Tauri offers a native "command" system (Rust functions invokable
from JavaScript via `invoke()`). For most operations, the existing
HTTP endpoints already work — the SPA inside Tauri's webview does
exactly what it does in a regular browser.

**Decision: HTTP localhost stays as the primary IPC.** Use Tauri
commands *only* for native concerns:

- **Window controls** — close, minimize, maximize (Tauri-native)
- **OS notifications** — system tray, native toasts
- **File dialogs** — opening intent.md in the user's editor of choice
- **Auto-update prompts** — Tauri's built-in updater UI

Decoupling the SPA from Tauri-specific APIs keeps the same bundle
running cleanly in a regular browser too — the developer workflow
(`npm run dev` against the daemon at `:7878`) doesn't care that
production wraps it in Tauri.

## Build matrix

| Runner | Output | Signing |
|---|---|---|
| `macos-latest` (Apple Silicon) | `.app` bundle, `.dmg` installer | Apple Developer ID + notarization |
| `windows-latest` | `.msi` installer, optionally `.exe` portable | Microsoft Trusted Signing via Azure |
| `ubuntu-latest` | `.deb`, `.AppImage` | unsigned (ecosystem norm) |

Tauri's `tauri build` produces all platform-appropriate formats out
of the box. CI runs the build on each runner; release artifacts
upload to the GitHub Release for the tag.

**Architecture choice for macOS:** Apple Silicon only, single-arch.
Universal binaries (lipo'd Intel + ARM) require deprecated Intel
runners or self-hosted; the modern default for new projects is
ARM-only. Document in the README that Intel Macs use the pipx path.

**Linux distribution norm:** Both .deb and .AppImage. .deb covers
Debian/Ubuntu/derivatives via package managers; .AppImage works
everywhere as a portable bundle. No flatpak / snap initially —
those are post-v3 expansions if there's pull.

## Signing flow

### macOS — Apple Developer Program ($99/year)

1. Generate a Developer ID Application certificate via Apple Developer
   account; export as .p12 with a password.
2. Store .p12 + password in GitHub Secrets (`APPLE_CERTIFICATE`,
   `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_TEAM_ID`,
   `APPLE_APP_PASSWORD`).
3. CI step: import cert into temporary keychain, run `tauri build`
   with signing config, then `xcrun notarytool submit` for
   notarization, then `xcrun stapler staple` to attach the
   notarization ticket to the .app.
4. Tauri has first-class support for this via `tauri.conf.json`.

### Windows — Microsoft Trusted Signing (~$9.99/month via Azure)

1. Provision a Trusted Signing account in Azure (already partially
   set up per `project_rebrand.md`).
2. Configure GitHub Actions OIDC federation with Azure AD so the
   workflow can sign without storing a long-lived secret (mirrors
   the PyPI Trusted Publishing pattern shipped in v2.6.1).
3. Use `azure/trusted-signing-action@v0.x` after `tauri build`
   produces the unsigned .msi / .exe.
4. SmartScreen reputation builds over time — first downloads will
   show "Microsoft Defender SmartScreen prevented an unrecognized
   app" until enough installs accumulate. Document this expectation
   in the v3 release notes.

### Linux — unsigned

.deb files can be signed with `dpkg-sig`, but distribution-channel
signing (apt repos) is the more common security surface and out of
scope for v3. .AppImage doesn't have a standardized signing flow;
ship unsigned. SHA256 checksums on the GitHub Release page give
users a verification path if they want one.

## Distribution surfaces

| Surface | Audience | Mechanism |
|---|---|---|
| **GitHub Releases** | downloadable-app users | Tag-driven CI uploads .dmg / .msi / .deb / .AppImage to the release |
| **PyPI** (`pipx install tailward`) | Python-aware developers | Existing `publish-to-pypi.yml` workflow continues unchanged |
| **Homebrew tap** (post-v3) | macOS package-manager users | Optional later — write a formula that wraps the GitHub Release .dmg |
| **Winget** (post-v3) | Windows package-manager users | Optional later — submit manifest to winget-pkgs |

Both the GitHub Releases path and the PyPI path fire off the same
tag push. Single source-of-truth tag → multiple distribution
artifacts.

## Internal rename — `modmcp` → `tailward`

Per `project_rebrand.md`, v3 is the natural place to align the
internal package name to the public name. Three sub-decisions:

### a. Python package directory rename
`src/modmcp/` → `src/tailward/`. Mechanical change touching every
`from modmcp.X import ...` line. Hatchling build target updates.
Tests update. CI test paths update.

### b. State directory rename
`~/.modmcp/` → `~/.tailward/`. **Needs a migration helper** —
existing v2.x users shouldn't lose ledger.db, intent.md files,
snapshots. The migration:

1. On daemon startup, if `~/.tailward/` doesn't exist but
   `~/.modmcp/` does → copy the entire tree (or symlink, on
   platforms that support it).
2. Leave a breadcrumb (`~/.modmcp/MIGRATED_TO_TAILWARD.txt`) so a
   user looking for state knows where it went.
3. Document in CHANGELOG + release notes.

### c. Env var rename
`MODMCP_HOME` → `TAILWARD_HOME`. Honor both for v3.x as a
deprecation period; drop `MODMCP_HOME` in v4.

### d. CLI binary — open question

Two options per `project_rebrand.md`:

- **Rename to `tailward`** — keep `warden` as a backwards-compat
  alias for v3.x. Aligns to "one name."
- **Keep `warden` permanently** — three-name layout becomes
  intentional: `tailward` (public/PyPI), `warden` (CLI), `tailward`
  (internal package after rename). Like httpie/http or python-pptx/pptx.

**Provisional lean: keep `warden` as the CLI.** Muscle memory is
real, the name is fine for a daemon-shaped command, and the clear
audience-per-name layout (public-facing → `tailward`, hands-on-the-
keyboard → `warden`, source code → `tailward`) is honest about how
people interact with the project at each level. Decision deferable
to actual implementation phase.

## Auto-update — open question

Tauri has a built-in updater that checks a manifest URL on launch
and prompts the user to install when a new version is available.
Mechanism is well-documented; cost is moderate (need to host the
update manifest, sign update bundles, decide on cadence).

**Open: do we want it?** Tradeoffs:

- **Pro:** users always on the latest version; security fixes
  propagate; no "you're on v3.0.1, current is v3.4.0" problem.
- **Pro:** the trust-layer thesis benefits from low-friction patching
  if a JSONL schema breaks something downstream.
- **Con:** more CI surface (sign update bundles, host manifest).
- **Con:** users who want pinned versions get auto-update fatigue.
- **Con:** GitHub Releases as manifest source is workable but means
  the manifest URL is `api.github.com/...` — ugly.

**Decision: defer.** Ship v3.0.0 without auto-update; users update
by re-downloading from GitHub Releases or via package manager. Add
auto-update as a v3.1 if it becomes a real friction.

## What the Tauri shell looks like

Initial UX target: **chromeless browser-like window**. The SPA owns
the entire content area; minimal native chrome (just the OS-standard
title bar / window controls).

Native features added in v3.0.0:
- System-tray icon (close-to-tray on minimize) — optional, per-OS
  conventions vary
- "Open project folder" menu item that triggers a native folder dialog

Native features deferred to v3.1+:
- Native dock/taskbar badge for unread synthesis events
- Global shortcut to focus the window
- macOS menu bar entries beyond the defaults

Keep v3.0.0 minimal native; expand based on real use.

## Daemon health on the Tauri side

If the daemon subprocess dies (crash, OOM, manual kill), what does
Tauri do?

**Provisional design:**
1. Tauri monitors the subprocess via standard process-handle status.
2. On unexpected exit, log the exit code, attempt one auto-restart.
3. If restart fails, surface a "Daemon stopped unexpectedly" message
   in the webview (could be a minimal Tauri-rendered error page if
   the SPA can't load).
4. Provide a "Restart daemon" button.

Not blocking for v3.0.0; the simple version is "if the daemon dies,
the user re-launches the app." Add monitoring + auto-restart as
follow-up if it becomes a real friction.

## Open questions (need decisions before implementation phase)

1. **Python bundling mechanism.** PyInstaller (lean) vs
   python-build-standalone (flexible). Validate via a sidecar build
   experiment early; pivot if needed.
2. **Port selection.** Hardcoded `7878` clashes if user already has
   the daemon running outside Tauri. Use a free-port-at-startup
   pattern (read back from daemon's stdout? config file?) or insist
   on a single-instance-only model.
3. **Single-instance enforcement.** Should running tailward.app
   prevent a second instance from launching? Tauri has built-in
   support; needs an opinion.
4. **macOS architecture.** Apple Silicon only (cleanest), or
   universal binary (broader compat, more build complexity)?
5. **CLI binary name.** `warden` permanent vs `tailward` rename. See
   "Internal rename — d." above.
6. **Auto-update inclusion.** Defer to v3.1 (current lean) or bundle
   into v3.0.0?

## Implementation milestones (commit-shaped)

When v3 enters implementation phase, this is the rough sequence — each
landing as a coherent commit or short series:

1. **Tauri scaffold + bundled-Python sidecar experiment.** Hello-world
   Tauri app that spawns `python -c "print('hi')"` from a bundled
   Python sidecar; loads a static page in the webview. Validates
   the Python-bundling approach end-to-end.
2. **Daemon-spawn integration.** Tauri spawns the actual daemon,
   waits for `127.0.0.1:7878` to respond, loads the SPA. Lifecycle:
   clean startup, clean shutdown.
3. **macOS signing flow.** First signed + notarized .app build via
   GitHub Actions on a feature branch. End-to-end validation of the
   signing pipeline before committing to it on a tag.
4. **Windows signing flow.** Same, with Microsoft Trusted Signing.
5. **Linux build flow.** .deb + .AppImage outputs (unsigned).
6. **Internal rename — Python package.** `src/modmcp/` →
   `src/tailward/`, all imports updated, tests green.
7. **Internal rename — state dir.** `~/.modmcp/` → `~/.tailward/`
   with migration helper.
8. **Internal rename — env var.** `MODMCP_HOME` → `TAILWARD_HOME`,
   honoring both.
9. **Release pipeline extension.** Extend `publish-to-pypi.yml` (or
   add a sibling workflow) to also build + sign + upload to GitHub
   Releases on tag push.
10. **v3.0.0 cut.** Tag, watch all artifacts land on PyPI + GitHub
    Releases simultaneously.

## How to apply this doc

- Treat this as the source-of-truth for v3 architectural decisions.
  Update inline as decisions land.
- Open questions get resolved either via design discussion (mark
  with date + decision) or deferred explicitly (mark with rationale).
- Implementation-phase work starts from milestone (1) above and
  flows top-down. Each milestone is small enough to be a coherent
  commit; large enough to make visible progress.
- If a future session needs to pick up v3 work cold, this doc + the
  related memory files (`project_rebrand.md`, `project_knowledge_roi.md`,
  `project_v2_cutover_plan.md`) are the primer.
