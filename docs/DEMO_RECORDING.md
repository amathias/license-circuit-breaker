# Demo recording runbook

The recording runbook of record. `../DEMO_AND_SUBMISSION.md` keeps the narrative background
and the claims-to-avoid list; this file is the sequence you actually execute, in order, with
the checks that must pass before you start and the conditions that stop you mid-take.

The video does not exist yet. Nothing in the submission claims it does. When it exists,
paste its public link into the Devpost video field and into `../SUBMISSION.md`.

---

## Hard constraints

| Constraint | Value |
|---|---|
| Maximum length | **under 3:00**. Judges need not watch past three minutes |
| Target length | **2:40**, hard stop at **2:55** |
| Hosting | public YouTube, Vimeo, or Youku — not unlisted-only, not a private link |
| Language | English |
| Audio | your own voice, or none. **No third-party music.** Silence is a legal recording |
| Content | must show the project actually functioning, and must match what the submission says |
| Screen | one browser window, URL bar visible in every frame that shows the app |

## What must be visible in the finished video

Derived from the judging criteria, not invented. Each of these must be *on screen*, not just
narrated.

- [ ] The public app URL and the public repository URL.
- [ ] Prohibited content being served **before** the trigger.
- [ ] Impact derived from DataHub lineage, including the branch that is **not** affected.
- [ ] At least one artifact whose lineage path is **incomplete**, and the escalation it causes.
- [ ] Rule IDs on the decisions.
- [ ] The approval gate refusing **before** any approval is recorded.
- [ ] The approval binding to a plan hash.
- [ ] Real containment steps running, including the one that reports **no change**.
- [ ] Post-action probes, including the two **precision** probes that must still pass.
- [ ] The DataHub writeback, verified by re-read.
- [ ] The verdict stated as **escalated**, not glossed as success.
- [ ] The simulated-DataHub disclosure, said aloud once and visible on screen.
- [ ] The no-legal-advice line.

## The disclosure line

Say it once, early — during segment 2 — and do not narrate around it:

> The walkthrough from here runs locally against a deterministic in-memory DataHub
> substitute, and everything it produces is labelled simulated. The local artifact changes
> are real and are probed directly.

And once, at the close:

> This supports compliance operations. It is not legal advice.

The console renders both a banner and a `DataHub: simulated` pill throughout the local
segments. A judge who spots that unmentioned will discount everything else in the video.

---

## Before you record

### Machine and screen hygiene

- Close every other browser tab and window. Sibling hackathon projects, coordinator
  documents, ticket trackers, and mail clients do not appear in this recording.
- Use a clean browser profile: no bookmarks bar, no extensions, no autofill dropdowns, no
  profile avatar.
- Disable OS and chat notifications.
- No terminal window may show `.env`, a token, an SSM session, an AWS account ID, a host
  name, or any path containing your home directory. If you show a terminal at all, show only
  the four commands in the local segment.
- Screen resolution fixed for the whole take; do not resize mid-recording.

### Bring the local instance up

```bash
python -m demo.cli estate reset && python -m demo.cli estate build
npm --prefix web run build
python -m app.main                       # http://127.0.0.1:8102
```

Open the console and press **Reset demo** once. That rebuilds the estate *and* clears the
approval and run journals, so the take starts from a state where the gate has nothing
recorded and will genuinely refuse.

Keep the browser network tab open. The status codes are part of the argument.

### Pre-record gate

Every row must pass before the first take. If one fails, fix it and re-check — do not record
around it and do not narrate a claim the screen cannot support.

| # | Check | Pass condition | If it fails |
|---|---|---|---|
| 1 | Public app reachable | `https://license.datahub-hackathon.aaronmathias.com/` returns the console in a browser | Record segment 0 as the repository only, and cut the public-URL narration entirely |
| 2 | Public readiness | `GET /api/readiness` on the public host returns **200** with all checks passed | Do not show or mention public readiness. Skip to check 4 and record segment 0 as URL bar plus repository only |
| 3 | Public host untouched | You have run **no** mutating request against the public host — no execute, no writeback, no reset, no seed | Stop. That is a coordinator-owned shared instance; see "Never, during a take" |
| 4 | Public repository | `https://github.com/amathias/license-circuit-breaker` loads while signed out, and the Apache-2.0 badge is visible on the repository page | Fix the repository visibility before recording; it is a submission requirement |
| 5 | Local masthead | Shows `verdict: not_started`, the amber `DataHub: simulated` pill, and a readiness pill | Press **Reset demo**; if the verdict is still not `not_started`, the journals did not clear |
| 6 | Stage 1 | All three probes read **exposed**, and `P-` review IDs are visible in the vector-search result | Re-run `estate reset && estate build`; the partner rows are not being served |
| 7 | Stage 3 | The subtitle names a non-zero entity count and a non-zero lineage-edge count, and the graph renders | The context read failed; do not proceed |
| 8 | Stage 4 | **8** decisions, each with at least one `LCB-R…` rule ID | The plan is wrong; do not record |
| 9 | Stage 5 | Reads "No decision has been recorded." | Press **Reset demo** |
| 10 | Nothing private on screen | No token, no `.env`, no home-directory path, no other project's name | Fix before recording |

---

## The recording sequence

Total budget **2:40**, hard stop **2:55**. Timings are cumulative from 0:00. Narration lines
are what to say; keep them close, because they are calibrated to what the screen shows.

### 0:00–0:12 — Where it lives (public)

**On screen:** the browser at `https://license.datahub-hackathon.aaronmathias.com`, URL bar
legible, for about six seconds. Then a new tab at
`https://github.com/amathias/license-circuit-breaker`, showing the Apache-2.0 license.

**Do not** click Execute, Approve, Write back, or Reset on the public host. Read-only frames
only.

> License Circuit Breaker is deployed here and the source is Apache-2.0 licensed here.

*Optional, only if pre-record check 2 passed and you show the response body:* navigate to
`/api/readiness` on the public host and let the JSON sit on screen for two seconds.

> Readiness on the deployed instance reports its checks against a live DataHub. What follows
> is a local run, so you can reproduce all of it.

If check 2 failed, say neither sentence.

### 0:12–0:26 — Exposure (local, stage 1)

**On screen:** switch to `http://127.0.0.1:8102`, URL bar visible. Stage 1, all three probes
`exposed`. Point at the `P-` partner review IDs in the vector-search result.

> A partner has revoked the right to use this review feed. That data has already reached a
> model, a vector index, an endpoint, and an export — and all three are answering right now.

### 0:26–0:46 — Trigger and trace (stages 2 and 3)

**On screen:** stage 2 — the structured rights event, `training` and `retrieval` in the
purposes-lost pills, `analytics` retained, the content hash visible. Deliver the simulated
disclosure here, over the banner.

Then stage 3. Click `license.reports.review_volume` and show it is **not** affected. Click
`license.reviews.legacy_snapshot` and show its lineage path marked `[incomplete]`.

> DataHub supplies the provenance. Every decision cites the exact lineage path behind it —
> and when the path has a gap, it says so instead of guessing.

### 0:46–1:14 — Policy and the refusal (stages 4 and 6)

**On screen:** stage 4, eight decisions, each with a rule ID. Point at `LCB-R050` freezing
the API, `LCB-R040` purging the index, `LCB-R001` escalating the broken-lineage snapshot,
and `LCB-R010` clearing the analytics report.

Then scroll to stage 6 and press **Execute containment** *before approving*. The server
answers **409**; the console renders the reason verbatim. Show the 409 in the network tab.

> The plan is complete and nothing has been touched, because no human has approved it. That
> gate is on the server, not in the browser — the button is deliberately left enabled so you
> can watch it refuse.

### 1:14–1:24 — Approval (stage 5)

**On screen:** stage 5. Enter an approver, press **Approve this exact plan**, and show the
recorded approval naming the plan hash above it.

> The approval binds to one exact plan hash. Regenerate the plan and it stops applying.

### 1:24–1:52 — Containment (stage 6)

**On screen:** press **Execute containment**. Eight steps: freeze, quarantine, purge,
rebuild ×3, retrain, replace. Point at the step that reports **no change**.

> These execute against real local artifacts. Step four reports no change because that
> rebuild was already satisfied — it says so rather than claiming work it did not do. Every
> action is idempotent, so a resumed run cannot double-apply.

### 1:52–2:16 — Verification (stage 7)

**On screen:** press **Re-run every probe**. Eight probes pass — six containment, two
precision. Highlight the two precision probes: the analytics report still queryable, the
approved model still serving. Then scroll to the residual-exposure table.

> Verification reads the artifacts, never the receipts. And it fails on over-reach too: if
> containment had broken the branch it was told to leave alone, these two probes would fail.

### 2:16–2:28 — Writeback (stage 8)

**On screen:** press **Write the outcome back to DataHub**. Show `8/8 statuses verified` and
the per-artifact table with `globalTags, datasetProperties`.

> Each artifact gets the status it earned, plus the plan hash and an evidence reference,
> confirmed by re-read.

### 2:28–2:40 — Close

**On screen:** the masthead verdict pill reading `escalated`, and the residual-exposure row.

> The verdict is escalated, not contained. One artifact is reachable through a lineage path
> DataHub cannot complete, so it is escalated for manual confirmation — the tool does not
> report an all-clear it has not earned. This supports compliance operations. It is not legal
> advice.

### If you are running long

Cut in this order. Never cut the 409 refusal, the escalated verdict, or the disclosure.

1. The optional readiness frame in segment 0.
2. The `license.reports.review_volume` click in stage 3 — keep the incomplete-path click.
3. Two of the four rule-ID callouts in stage 4.
4. The residual-exposure scroll at 2:16, since the close shows it again.

---

## Stop conditions

Each row is a claim the narration could make. If the required evidence is not on screen at
the moment the claim is made, **do not make the claim** — re-shoot the segment or cut the
line. These exist so the video cannot assert live behavior a viewer cannot see.

| Claim | Required on screen at that moment | If absent |
|---|---|---|
| "deployed here" / "you can test it here" | The public URL in the browser URL bar, page rendered | Cut the sentence; show the repository only |
| "readiness reports its checks against a live DataHub" | The public host's `/api/readiness` response body, status 200 | Cut the sentence entirely. Never assert a live DataHub read from a screen that is not showing one |
| "DataHub supplies the provenance" | Stage 3 rendered with a non-zero lineage-edge count | Re-shoot; the context read failed |
| "cites the exact lineage path" | The selected node's lineage-path row | Re-shoot with a node selected |
| "the gate refuses" | The 409 in the network tab **or** the rendered refusal notice | Re-shoot; a described refusal is not a demonstrated one |
| "these execute against real local artifacts" | Stage 6 rows reading `completed`, with detail text | Re-shoot |
| "verified by re-read" | Stage 8 showing verified equal to attempted | Re-shoot |
| "the verdict is escalated" | The masthead verdict pill reading `escalated` | Re-shoot; do not state a verdict the screen does not show |

Three claims are prohibited outright in this recording, in any wording:

1. **Anything asserting that the walkthrough ran against a live DataHub instance.** It did
   not. The `DataHub: simulated` pill is on screen for the whole local segment, and a
   narration that contradicts it is the single most damaging untrue claim the video could
   make.
2. **Anything attributing the deployment coordinator's live closeout to this recording, or
   to the repository.** That run happened on the deployment host, its receipts stay there,
   and it is not what the video shows. If it belongs anywhere, it belongs in the written
   submission, attributed — not in the voiceover.
3. **Any legal conclusion.** No "this makes you compliant", no "this satisfies the
   obligation", no reading of a license.

## Never, during a take

- Do not run **any** mutating request against the public host: no Execute, no Approve, no
  Write back, no Reset, no seed, no reset/restore. It is a coordinator-owned shared instance
  carrying four sibling projects, and the durable revocation writeback has never been
  exercised live. The public segment is read-only frames and nothing else.
- Do not open an SSM session, an AWS console, a DataHub admin page, or any private evidence
  on camera.
- Do not show `.env`, a token value, or a receipt file.
- Do not show any other hackathon project.

---

## After recording

- [ ] Length is under 3:00 — check the exported file, not the timeline estimate.
- [ ] Watch it once end to end with sound on, against the "what must be visible" list above.
- [ ] No token, `.env`, home-directory path, SSM session, sibling project, or private
      document appears in any frame.
- [ ] No third-party music or copyrighted material.
- [ ] Upload to YouTube or Vimeo as **public**, not unlisted, not private.
- [ ] Video description states, in one line, that the walkthrough runs against a simulated
      DataHub substitute and that local artifact changes are real.
- [ ] Confirm the link plays signed out, in a private browser window.
- [ ] Put the link in the Devpost video field and in `../SUBMISSION.md`.
- [ ] Re-check the deadline and the official rules on Devpost before submitting.

### Video description text

Ready to paste. It carries the disclosure and the disclaimer, so the video is honest even
with the sound off.

> License Circuit Breaker — executable data-rights revocation with DataHub. One upstream
> right is revoked; every affected downstream dataset, feature table, model, vector index,
> endpoint, and export is traced from DataHub lineage, contained under a server-side approval
> gate, and verified by probing the artifacts themselves.
>
> The walkthrough in this video runs locally against a deterministic in-memory DataHub
> substitute; everything it produces is labelled simulated. The local artifact changes are
> real and are probed directly.
>
> This supports compliance operations. It is not legal advice, does not interpret contract
> text, and makes no determination about whether any obligation has been met.
>
> App: https://license.datahub-hackathon.aaronmathias.com
> Source (Apache-2.0): https://github.com/amathias/license-circuit-breaker
