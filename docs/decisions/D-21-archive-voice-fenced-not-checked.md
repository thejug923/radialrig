---
id: D-21
title: Archive voice is fenced, not checked
type: decision
status: in-force
grade: verified
owner: thejug923
scope: RadialRig
effective: 2026-08-17
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: D-05
---

**Status: in-force.**

## Question

Must a mechanical check enforce the past tense that
[D-05](D-05-past-tense-archive.md) requires of archived records?

## Ruling

No. **The past tense stays a written rule, and no check enforces it.**
C7 continues to test one thing it can test — that the fence sentence
opens the record — and tests nothing about the body's voice.

The risk D-05 names is that imperative text in a dead record still
commands an agent. That risk is answered where the text is **read**, not
where it is written, and both read paths already answer it:

- Through the retrieval server, `search_history` is the only path that
  serves archive content, and it attaches the fence to every response
  *(verified 2026-08-15: `ARCHIVE_FENCE` is applied at the single return
  site; `get_ruling` and `list_rulings` exclude the archive, and the
  zombie-record test holds them to it)*.
- Reading the file directly, the head-of-file rule
  ([D-03](D-03-head-of-file-status.md)) puts the fence sentence in the
  first paragraph, and C7 does enforce that.

## Why

Tense is a property of meaning, and a check reads characters. The
plausible test — flag `must`, `never` or `shall` in an archived body —
fires on the correct case, because a past-tense record legitimately
quotes the rule it used to state. A check that punishes correct writing
is worse than no check: authors learn to write around it, as they learned
to unwrap the fence sentence before C7 was fixed.

PLAN-0003 Phase 1 recorded this as gap G10 and routed it "tool must cover
it, or accept as unreachable". Fourteen runs, in-session and by fresh
agents, produced no archive entry left in the imperative *(verified
2026-08-15)*. That is not proof the class is empty. It is evidence that
the written rule plus the fence is holding, and that the class is rare
enough not to justify a check that misfires on correct text.

## Consequences

An archived record written in the imperative passes every check. A
reviewer catches it or nobody does. The fence limits what that costs: an
agent that reads the record through either path is told, before the body,
that it is reading a past position.

If the fence ever stops covering a read path, this ruling is wrong and
reopens. That is the condition to watch — not the tense itself.
