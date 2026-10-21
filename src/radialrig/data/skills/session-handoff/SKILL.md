---
name: session-handoff
description: Use when the user asks to hand off, wrap up a session, or pass the work to a fresh agent before clearing context. Sweeps what this session started, writes a handoff file the next agent resumes from alone, and ends with a kickoff prompt.
radialrig:
  id: session-handoff
  version: 3.3.0
---

## First, the rules that bind

- The human drives the handoff. On completion of a substantive unit of work you may
  offer this once, as an option, and then let it go. Never run it unasked, never read
  your own transcript, never measure context.
- The script does the mechanical steps: `stamp`, `file`, `kickoff`, `check`. If it
  cannot run, say so and stop; do not improvise the retention, the ignore line, or
  the kickoff prompt.
- The kickoff prompt in chat is the script's bytes, unchanged, as the final fenced
  block; the handoff body is never echoed into chat.
- Done means `check` printed its pass verdict.

## When it fires

The user asks: hand off, wrap up, pass to a new agent.

This is the end of session half of a pair: it does not write at checkpoints, so a
session that must survive an unplanned reset needs a running notes file this skill
does not provide.

## Step 0, the premature handoff guard

When the session produced nothing substantive (no decision locked, no file written, no
problem settled), say that continuing is probably cheaper than restarting and ask one
confirming question. One question, not a negotiation. Judge it from what you remember
doing; context consumed is not the test. Zero tool calls.

## Step 1, the sweep

> Enumerate from context, verify by probe, report only session caused state.
> Never enumerate the machine.

The sweep runs before anything is written, so the saved file describes the settled
environment. What this session started is already in your own record; probe only to
verify those items, batching the probes into as few calls as you can.

| # | Check | Enumerate from | Verify by |
|---|---|---|---|
| 1 | Background shells started this session | the shell IDs you hold | liveness of those IDs only |
| 2 | Servers and ports bound this session | the ports this session bound | those ports only |
| 3 | Browsers launched this session | the browser handles you hold | teardown confirmation |
| 4 | Repos written to this session | the paths you wrote to | `git status --porcelain` per repo |
| 5 | Temp state created this session | worktrees, scratch files, half applied edits | direct check of those paths |

Processes you did not start are the user's environment, not session state; do not
enumerate them. Report one only where it collides with something this session started,
and say plainly that it is not yours. Kill only what this session launched, never by
name match. A check that could not be run is reported as unchecked, never omitted.

Consent runs in three tiers, by action class:

| Tier | Actions |
|---|---|
| Run, then report in one aggregate line | killing a process or server this session started; moving surplus handoffs to the archive; writing the handoff file; the ignore line for an untracked `handoffs/` |
| Ask first | a commit, a stash, removing a worktree, any write outside `handoffs/` other than that ignore line, anything else irreversible or visible off this machine |
| Never | deleting a handoff; killing anything this session did not start |

Where approval is absent, the item is recorded in Running state, never forced.

## Step 2, sources

Draw on the session you remember. Do not re-read or re-search to reconstruct it. The
sources are plan files referenced this session, task state, the sweep's results, files
created or modified, memory files touched, and questions left open on either side. Do
not audit the filesystem: no `git log`, no broad searches. If it was not touched this
session it is background, not state. Carry paths and identifiers; never inline file
content or diffs.

## Output template, exactly this structure, every time

```
# Session Handoff - <one line title>
<one sentence: what the user asked for>

## Standing constraints
## Decisions locked + what shipped
## Still broken
## Superseded this session
## Key files
## Running state
## Verification
## Deferred + open questions
## Pick up here
---
<provenance footer>
```

1. **Standing constraints.** Project and user policy still in force, one per line,
   each as its source (a record key or a file path) plus a short quote: the source is
   the authority, the quote is the lead. A line drawn from agent memory names the
   memory file and says it is a working note, not policy. A summary drops these first.
2. **Decisions locked + what shipped.** Each entry carries `verified: <command> ->
   <result>` or `unverified`; nothing is done because code was written. Evidence is
   the command, its exit code, and the salient line, and stops there.
3. **Still broken.** Failing tests, known bugs, half applied edits, anything mid
   flight, each with the exact failing command.
4. **Superseded this session.** Facts this session disproved and drafts it abandoned,
   with what replaced them, so a cold reader cannot re-adopt a killed assumption.
5. **Key files.** Absolute path plus why to read it first; the plan file first when
   one drove the session; any nested rules file that governed the work, because only
   the project root instruction file survives a reset.
6. **Running state.** Background processes with shell ID, what each is, and its kill
   command; dev servers with url and port; open worktrees and branches.
7. **Verification.** Two halves. *Verified this session*: command, exit code, salient
   line. *Re-verify with*: command and expected outcome. Without the first half the
   next session cannot tell proven from intended.
8. **Deferred + open questions.** `Deferred: <item> - <why>` and `Open: <question
   needing the user> - <context>`. Surfacing an embedded decision beats resolving it
   silently.
9. **Pick up here.** One action, roughly what a competent engineer finishes in under
   an hour, with its done condition stated.

Footer, in the file: `Source: this session, <ISO date and time>. Review status: N of M
entries verified; nothing here was read by a second reader. Confidence:
<high|mixed|low> - <one clause>. Skill: session-handoff 3.3.0.`

When the file grows past what a cold reader needs, cut rather than append: resolved
detours first, then background. A section with nothing to report says "none" and is
never omitted: a missing section is ambiguous between nothing and forgotten. Record
decisions and actions, not the reasoning that produced them. The terminating check is
a reader's: a fresh agent holding only this file can name the next action and the
command to re-verify it.

## Saving

The file goes to `<project root>/handoffs/` by absolute path, one new file per
handoff, never overwriting an earlier one. Nothing in that directory is ever deleted;
surplus moves to `handoffs/archive/`.

Run the commands below from the project root, with the Python 3 on this machine;
`<skill>` stands for the directory holding this SKILL.md, wherever the harness keeps
it. `python <skill>/scripts/handoff.py stamp` gives the directory and the filename.
Write the file. Then run `python <skill>/scripts/handoff.py file <absolute path>` and
report the lines it prints: retention, the ignore rule, the word count, the check.

`handoffs/` stays out of git: git history carries code state, this file carries
session state, and a repository travels. The script adds the ignore line only where
`handoffs/` is untracked; where the host already tracks the directory it writes
nothing and reports the control as unenforced.

## Step 3, the chat output

| # | Output | What the reader needs |
|---|---|---|
| 1 | Sweep checklist | one line per check, outcomes only; one line when all is clean |
| 2 | Saved path | the path, and what was archived |
| 3 | The brief | for the user, not the next agent: what they walk into, and whether now is a good time |
| 4 | Kickoff prompt, fenced | the script's bytes, the final output |

### The kickoff prompt

The script owns the prompt's bytes; you own none of them. Run
`python <skill>/scripts/handoff.py kickoff <absolute path>`.
It renders the one canonical prompt with the path filled in, copies it to the
clipboard, and prints it: the first line reports the copy; everything after the
blank line is the prompt.

Output that prompt as the final fenced block **exactly as the script printed it**: a
prompt retyped, rewrapped, or rebroken from memory varies from session to session.
Report the script's status line honestly; if it says "not copied", say so plainly.

Chat only, never inside the handoff file, and the final output: nothing follows the
fenced block, no closing remarks, no offers of further help. Name no command for
starting the fresh session. The script enforces ASCII and refuses dash punctuation;
non-ASCII mangles on Windows OEM code pages.

## Done when

`python <skill>/scripts/handoff.py check <absolute path>`
prints its pass verdict. If the check fails, fix and re-run before reporting; if the
sweep changed anything after the draft, regenerate.

No emojis, no hype, no retrospective, no recommended next steps beyond "Pick up here".
Terse and concrete: paths, commands, IDs, decisions.
