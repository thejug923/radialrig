---
id: D-39
title: The release channel — a public repository for the releases and the base
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-10-20
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: PLAN-0008, D-34
---

**Status: in-force.**

## Question

PLAN-0008 phase 1, the channel
ruling: where do the public release and the engine package live, and
under what names does the product ship?

## Ruling

1. **The release host is a public repository that holds the releases
   and the public base.** The releases are the installer, the engine
   wheel, and later the linking web page. The public base is the part
   of the source that the private repository's export lists make
   public, exported from the private repository, where the work is
   done. The public repository is `radialrig` under the owner's GitHub
   account. It is fork-only: it takes no pull requests from anyone
   else. Its `main` changes only through a pull request the owner
   merges, and every push to the repository waits for the owner's yes
   in chat for that push.
2. **The package channel is the release itself.** Every release
   attaches the built engine wheel beside the installer, and the
   app's bootstrap installs the engine from that release address —
   never by bare package name from a public index, because a bare
   `uv tool install radialrig` would resolve against PyPI, where
   this product does not live and where the name could belong to a
   stranger. Publishing to PyPI, including a defensive claim of the
   name, stays open as a later ruling.
3. **The released names**, as PLAN-0008 phase 1 proposed and the
   owner accepted: asset `RadialRig-Setup.exe` with a
   version-stamped twin beside it, display name "RadialRig",
   publisher "Radial 47".
4. **The app updates itself through the same channel.** Each release
   attaches an update manifest, `latest.json`, giving the version
   and, for the Windows installer and the Linux AppImage, that
   asset's address on the release and its update signature. The app
   reads the manifest at the public repository's latest-release
   address and installs nothing whose signature the owner's update
   key did not make. The key stays on the owner's computer, with an
   encrypted copy the owner keeps in his own cloud storage; it never
   enters either repository or CI. Every installed app holds the
   manifest's address, so the public repository keeps its name and
   its owner.

## Why

Ruled by the owner on 2026-08-19, in the session that landed
PLAN-0008, choosing the recommended option on both trades: the
public journey could ship without making the source public, and the
engine travels with the release that installs it, so one address
serves the whole download. Losing this ruling would return
PLAN-0008's phases to their private stand-ins and phase 6 to a
closure that records the public journey as not yet shipped, which
admits it under [D-10](D-10-governed-scope.md).

Rule 4 was ruled by the owner on 2026-10-02, with
PLAN-0021. Updating by
hand meant noticing a release and rerunning the installer. The updater
checks a signature it cannot skip, so the key decides who can ship to
installed apps, and losing it ends updates to all of them. Rule 1's
allowance to rename the repository at its creation closed with rule 4,
since a rename would now strand every installed app's updates.

Rule 1's public base was decided by the owner on 2026-10-07, with
PLAN-0023: people can fork
RadialRig and make it their own, from the repository that already
carries its releases, while the work goes on in the private
repository. A push to the public repository is public the moment it
lands, before any merge, so each push waits for his yes. The
repository takes forks and not pull requests because no way for an
outside change to come back to the private repository has been
designed.

## Consequences

- PLAN-0008 phase 1 meets its done-condition with this record; its
  execution log names it.
- PLAN-0008 phase 5's bootstrap and release prescriptions read
  their addresses from this ruling: the wheel's release address is
  the configured package source, and `gh release create` targets
  the public repository, which has carried every release since
  v0.1.0 on 2026-08-20.
- PLAN-0021 phase 3 builds rule 4: `release.py` signs the update files
  and writes the manifest, and the app reads it.
- PLAN-0023 builds rule 1's public base: the export lists and the
  export in the private repository, the README both repositories
  share, and the public repository's section of
  [SPEC-4](../spec-git-environment.md).
