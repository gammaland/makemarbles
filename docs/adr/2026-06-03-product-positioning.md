# ADR 2026-06-03: Product Positioning, Open Source Strategy, Business Model

**Status:** Accepted
**Date:** 2026-06-03

## Context

After 2 months of build planning, two product-level questions surfaced:

1. **Framing**: Calling MakeMarbles an "AI memory layer" puts it in direct competition with mem0 / Letta / ChatGPT Memory / Rewind, and undersells the human user. Is there a sharper positioning?
2. **Business model viability**: If multi-device sync is paid, what's the single-device user value? Does the product stand alone, or does sync rescue it?

Same session also locked open-source license strategy across components, and a roadmap pivot away from a Tauri GUI in favor of a Textual TUI.

## Decision 1: Positioning — Personal Knowledge Atom Layer

MakeMarbles is **not** an AI memory product, **not** a notes app, **not** an AI-augmented Obsidian. It is a **personal knowledge atom layer**, where:

- A *marble* is an atomic unit of knowledge (idea / decision / observation / fragment).
- **Both humans and AIs are first-class read/write citizens** of the same data plane.
- The product is the layer, not any single capture surface or query interface.

Analogy: Stripe is a payments layer (any app writes, any channel charges). Marbles is a personal knowledge layer (any capture surface writes, any AI / human reads).

### Competitive landscape (2x2)

|                    | AI-first I/O weak                  | AI-first I/O strong                                     |
|--------------------|------------------------------------|---------------------------------------------------------|
| **Human-first strong** | Obsidian, Logseq, Notion          | **MakeMarbles** (target — empty quadrant today)         |
| **Human-first weak**   | Apple Notes                       | ChatGPT Memory, Claude Memory, Rewind, mem0, Letta, Zep |

Right-top quadrant is structurally empty: AI-memory products are vendor-locked single-channel; notes apps treat AI as a bolt-on. MakeMarbles' dual-channel symmetric I/O is the differentiator.

### Dual-channel I/O — what it concretely means

**Human capture surfaces** (P0 / P1 / P2 / P3):
- P0: `marbles "..."` CLI, iOS Shortcut, Siri voice
- P1: TUI `i` key insert mode
- P2: Email forward to `add@marbles.you`, browser extension highlight-to-marble
- P3: Apple Watch complication, local Whisper voice ingest, Tauri/Web GUI

**Human read surfaces**:
- `marbles search`, `marbles tui`, `marbles digest --week` (static HTML), `marbles serve` local web

**AI capture / read surfaces**:
- MCP server (stdio) — any MCP-compatible AI tool (Claude Desktop, Cursor, Cline, Continue) reads and writes the same marbles.

### Anti-features (what we deliberately do **not** build)

| Won't build | Reason |
|---|---|
| Rich-text editor | A marble > one paragraph is misuse; users should write long-form in Obsidian and capture atoms in marbles. |
| Bi-directional links / graph view | Obsidian owns this; "zero-structure" is our wedge. |
| Collaboration / sharing | Enters Notion red ocean; personal-first only. |
| Markdown render | Reinforces atom-not-document discipline. |
| Passive screen recording (Rewind-style) | Privacy radioactive, data explosion, not the wedge. |

## Decision 2: Open Source Strategy (Component Matrix)

Locked per-component licensing — strategy-driven, not blanket-MIT:

| Component | License | Repo | Strategic intent |
|---|---|---|---|
| Client (CLI + TUI) | **AGPL-3.0** | public, day 1 | Trust signal for local-first product; AGPL prevents commercial SaaS forks. |
| Sync protocol spec + crypto params | **CC-BY-SA** | public, in client repo `/docs/protocol/` | Independent cryptographic auditability — the legal basis for the E2E claim. |
| MCP Server | **MIT** | separate public repo | Maximize ecosystem reach; license-shy AI-tool vendors (Cursor, Cline) can adopt without friction. |
| Sync server implementation (CF Durable Objects + R2) | **Closed source** | private | Core monetization. Hosted SaaS is the revenue product. |
| Future Tauri GUI | TBD | TBD | Not committed — see Decision 4. |

**Why protocol is open but server implementation is closed:**

> If the protocol is closed, "E2E encryption" is a marketing word — users must trust us, not verify us. Open protocol lets cryptographers audit the design independently of our implementation. **Our hosted service should not need to be trusted; it should be verifiable that it cannot read your data.**

**Precedent (this is not an invented model):** Bitwarden, Plausible, Standard Notes, Mattermost all run the fair-source open-core model — open client, open protocol, closed hosted server, AGPL spec.

## Decision 3: Business Model — Single-Device Value First, Sync as Upsell

**Reject** the framing that sync paywall hollows out the product. **Single-device must be a complete product on its own.**

### Single-device value proposition (free forever, no feature gating)

1. **Persistent memory for every AI tool** — MCP makes Claude Desktop, Cursor, Cline, Continue all share the same local knowledge base. Solves the "all AI tools are goldfish" pain.
2. **Zero-structure capture** — no folders, no tags; vectors auto-organize. Eliminates the Obsidian gardening tax.
3. **AI-native skills loop** — `/grow`, `/career-prep`, future skills consume marbles for personal workflows.
4. **Developer-grade ingest** — CLI scriptable, iOS Shortcut, future browser/email; symmetric to AI access.
5. **Local-first privacy** — vectors never leave device, no vendor lock-in, no cloud memory leaking across users.

### Pricing tiers (MVP)

| Tier | Devices | Price | Purpose |
|---|---|---|---|
| Free | 1 | $0 | Full product + automatic encrypted cloud backup of that single device |
| Pro | up to 5 | $5/mo (or $48/yr) | Multi-device sync + sync history + workspace separation |
| Team / Enterprise | N | TBD | Deferred post-PMF |

**Why Free includes single-device backup (not just local)**: PLG pattern. User experiences sync infrastructure as a backup utility before being asked to pay. When the second device shows up, the conversion prompt lands on a user who already has weeks of accumulated data dependence.

**Why $5/mo not $8/mo**: $5 is the no-brainer subscription band. Obsidian Sync at $8 generates friction; Bitwarden at $10/year is the sweet spot. Pick the low side for a new entrant.

**Capacity-blowup protection**: Free tier is single-device write/read with light backup traffic (write-heavy, read-cold). Scales linearly with active users, not exponentially.

### Future monetization levers (not for MVP)

| Lever | Description |
|---|---|
| Sync subscription | Active — Pro tier |
| Team / workspace | Shared marbles for small teams |
| Higher AI processing | Optional cloud LLM workflows (cross-week digest, automatic insights) on top of local index |
| Enterprise self-host license | Closed-source server license for orgs that want on-prem |

## Decision 4: GUI Roadmap — Defer Tauri, Ship Textual TUI

**Tauri GUI is deferred.** The user-stated needs (vector indexing progress, data size, quick search, lightweight browse) are information-density problems, not graphical-interaction problems. **Textual (Python TUI framework) covers 100% of stated needs at ~5h vs ~30h for Tauri.**

Also: a low-polish Tauri GUI is a negative signal in interviews — a strong CLI + MCP + TUI stack reads as "developer judgment + dev-experience taste"; a half-baked GUI dilutes that.

**Roadmap update**:

- **v1**: CLI + MCP + Sync (existing 7 phases)
- **v1.5**: `marbles status` (rich progress + DB size), `marbles tui` (Textual), `marbles digest` (static HTML weekly)
- **v2 / post-offer**: Reconsider Tauri only if non-developer users emerge as a real segment.

## Consequences

### Positive
- Positioning escapes "another AI memory product" crowd and stakes an empty quadrant.
- License matrix lets us monetize hosted SaaS without compromising trust signal.
- Roadmap focus tightens: ship CLI + MCP + Sync, then TUI, then maybe GUI — not parallel tracks.
- Anti-features list gives a clean answer to scope creep requests.

### Negative / risks
- Closed-source server invites community pressure ("when will you open-source the server?"). Mitigated by open protocol + reference implementation possibility.
- AGPL client may scare a small fraction of corp users. Mitigated by client being end-user software, not embedded library.
- "Personal knowledge atom layer" positioning is wider than "AI memory" — harder to explain in 5 words but stronger in 30 seconds.
- $5/mo Pro tier may underprice future enterprise features; consider grandfathering early subscribers.

## Talking points (for behavioral interview §4.5 MakeMarbles deep dive)

1. **Per-component licensing as strategic decision** — "I chose the Bitwarden model not the Notion model, because trust must be verifiable, not assumed."
2. **Anti-features as positioning** — "We don't build X because Y."
3. **Framing precision determines competitive landscape** — original "AI memory" framing put us in a crowd; "knowledge atom layer" stakes an empty quadrant.
4. **Single-device value as product floor** — sync is upsell, not rescue.
5. **Self-correction as process signal** — initial framing was wrong, recognized via "what's the wedge if sync is paid" stress test, repositioned.

## Master Narrative (for behavioral §4.5 deep dive, locked 2026-06-03)

Opening story (~80 sec, ~260 words), used as the response to "tell me about a project you've worked on outside of work":

> MakeMarbles is a personal knowledge layer that any AI tool can read and write through MCP.
>
> It started as a personal notes app — low-friction daily capture, with iOS Shortcut input, a UI, and a daily email digest where the AI synthesized a week of context into insights across small wins, health, deep thinking, and action. I gave it to a friend to try. His first question was, "wait — can you see what I write?" That hit harder than any feature request I'd ever gotten. Trust wasn't something I could bolt on. It was the actual product.
>
> Around the same time, I noticed something else. Whenever I worked with Claude — especially on a self-reflection skill I'd built — the answers stayed general because the AI had no real context on me. Going the other way, the AI sometimes said something I genuinely wanted to keep, and I had nowhere clean to save it. There was a data gap on both sides — into the AI, and out of it.
>
> The two signals pointed at the same product. Not a trustworthy notes app — a personal knowledge layer, where trust is the foundation and any AI tool can plug in.
>
> So I rebuilt. Local-first, end-to-end encrypted, client and protocol both open source — so trust is verifiable, not asked for. MCP lets any AI tool tap in without handing my data to a vendor. Free single-device, five bucks a month for sync.
>
> I use it every day — the bar I hold myself to is that every iteration has to be usable enough that I'd lose data if I stopped. That keeps the rebuild honest.

**Signal density notes:**
- Two converging independent signals → same root cause = staff-level judgment marker
- "Trust isn't a feature, it's the product" = decision reframe under user feedback
- "Data gap on both sides, into the AI and out of it" = dual-channel I/O positioning in spoken form
- "Verifiable, not asked for" = principled stance on trust as auditability
- Final quality bar (dogfooding rule) = principal-level self-imposed standard

**Anticipated follow-ups (prep separately):**
1. Encryption design — how E2E works across multiple devices (asymmetric ingest companions for free tier)
2. Why MCP specifically — transport choice driven by local-first positioning
3. Open source moat — what stops a bigger player from forking?
4. Other features built but cut — voice ingest via Whisper, daily email digest prompt engineering

## References

- `docs/design-decisions.md` — earlier (2026-05-05) design, partially superseded by this ADR
- `docs/roadmap.md` — phase plan (to be updated to reflect TUI insertion and Tauri deferral)
- `career_prep/interview_vocab.md` — vocab anchors from this narrative
- Memory: `project_makemarbles_status.md`, `project_eink_display_idea.md`, `reference_lionote.md`
