---
id: D-00
title: The target the archive exclusion is tested against
type: decision
status: archived
grade: decided
owner: {{OWNER}}
scope: {{SCOPE}}
effective: {{DATE}}
last-verified: {{DATE}}
supersedes: none
replaced-by: none
depends-on: none
---

**Status: archived.** This is a record of a past position, not an
instruction. It holds no ruling of its own.

The mount seeds this file so the archive exclusion has something to
find. Default retrieval excludes `docs/archive/`, and a glob that fails
to exclude fails silently: nothing reports it, and the archive simply
reappears in results. A search run against an empty archive finds
nothing whether the glob works or not, so it proves nothing.

The marker phrase is `radialrig-archive-exclusion-marker`. In a fresh
project the phrase sits here and in the prose that names it, nowhere
else. The check is a search for the marker with the exclusion form this
project records in [docs/spec.md](../spec.md) section 2. While the form
excludes, that search returns no hit under `docs/archive/`. A hit under
`docs/archive/` means the recorded form failed and needs a fresh
measurement in this project's search tooling. A hit outside
`docs/archive/` is the prose that names the marker, and it is expected.

Once the archive carries records of this project's own, they serve as
targets too. A project that no longer wants this one can remove the
file; the exclusion rule is unaffected either way.
