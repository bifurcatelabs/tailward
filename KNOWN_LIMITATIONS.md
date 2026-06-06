# Known limitations

Edge cases and intentional trade-offs that are understood but not (yet)
addressed. Each entry notes how often it bites in practice and the current
behaviour, so you can tell a known limitation apart from a bug.

## Remote aggregation: session IDs must be unique across followed boxes

**What.** When tailward follows remote boxes (see remote aggregation), each
project's identity is *box-qualified* — two boxes working in the same path
(e.g. both `/opt/camcontrol`) stay distinct. **Session** identity is not: a
session is keyed by its `session_id` alone. So if two followed boxes contain
the same `session_id`, only the first one tailward sees is tracked; the
second box's copy is skipped.

**How often it bites.** Rarely. Claude Code assigns each session a random
UUID, so two *independently* running boxes effectively never share a
`session_id`. The realistic triggers are:

- **Copied or templated `~/.claude` state** — a golden VM image that already
  contains Claude Code sessions, then deployed to several boxes, so they all
  carry the same `session_id`s.
- **Following one box under two different names** (a misconfiguration).

In normal production work — distinct boxes, each running their own
sessions — this does not occur.

**Current behaviour (guarded, non-destructive).** The daemon detects the
collision, logs a prominent warning, and **does not clobber** the owning
box's session state or read offset; the colliding session is skipped. The
failure is visible in the daemon log rather than silent.

**Why it isn't fully fixed.** Box-qualifying session identity end-to-end is
invasive: `session_id` is used in page URLs, in transcript file-path
reconstruction, and as the primary key of several tables. Given the rarity of
the trigger, the loud guard is the deliberate trade-off; a full box-qualified
session identity may land in a later release if templated-fleet use makes it
worth the change.
