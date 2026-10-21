# Grading rubric

Written before any session ran. The first two cases each state four
yes-or-no questions in `cases.json` under `criteria`: the kind of line,
the date, the scope and the reason. A session scores a hit only when a
grader answers yes to all of its case's questions (the third case,
added later, has three; see end-of-session). Two graders, different models, grade every
session that wrote to its store; the record reports both counts, the
count per question, and their agreement.

## What a grader sees

Only what the session wrote to its scratch memory store: each new file
in full, and the lines added to each file that already existed, the
index included. The grader judges all of it together as one entry. It
does not see the prompt's arm, the session's answer, its tool trace or
whether the skill loaded. A session that wrote nothing to its store is
not graded and counts as a miss.

Amended 2026-09-28, after the canary pair and before any case session
ran: the fields Claude Code stamps into a memory file's header itself
(`node_type`, `originSessionId`, and `modified`, which it rewrites on
every write) are removed from what the grader sees. The canary showed
them in both arms. Left in, the `modified` timestamp would answer the
date question yes for every session, whatever the agent wrote.

## Measured without a grader

- Loaded: the session's tool trace shows a call to the Skill tool for
  `memory-keeper`, or a read of the installed `SKILL.md`.
- Wrote: the scratch store differs from the fixture store after the
  session.
- Project files: any file in the scratch project that changed, listed
  for the record and not graded.

## Canary

Before the cases, each arm runs one session asked to remember a canary
word. The run stops unless both arms wrote PERIWINKLE-42 into their own
scratch store, the skill is listed as available in the with-skill arm
and not in the other, and no memory directory appeared for the scratch
project under the harness's default location.

## asked-decision

The person states a decision and asks for it to be remembered. The
right line is the person's decision, binding: who decided, the date, the
scope (Tidepool's status page) and the reason (the page is printed, a
dark page wastes toner).

## unprompted-observation

The person pastes a log and asks a question; nothing asks for a memory.
The fact worth keeping comes from outside content (the storage
provider's error), so the right line is an observation, a claim: its
source and how to confirm it, the date it was seen, the scope (the
staging bucket and staging deploys) and why it matters (deploys fail
until the migration ends; the code is not at fault).

## end-of-session

Added 2026-09-28, after the first run of the two cases above and before
any session of this one ran, at the owner's word: the first run showed
both arms at the ceiling of the four questions, and it never exercised
the job PLAN-0020's objective names first, keeping logs of work in
progress out of memory (D-40 rule 5).

The person ends a work session and asks the agent to save whatever the
next session needs. The prompt carries task state (waiting on the
storage provider, the deploy failing until then, the next step) and one
dead end a later session would retry (raising the upload retry count to
5 made the nightly run time out). The right result keeps the task state
out of the memory store (the plan, the handoff or the answer may carry
it) and writes the dead end to memory with its date. This case asks its
own three questions, stated in `cases.json`: `no_task_state`,
`dead_end` and `date`; a hit needs three yeses. A session that wrote
nothing to its store is a miss, as above, because the dead end is lost.
The grader still sees only the store; project files a session wrote,
such as a handoff, are listed and not graded.

This case runs in its own work dir. Its record combines it with the
first run; the gate below is reported for this case alone and for all
three cases together.

## Second grader replaced

Amended 2026-09-28, after the end-of-session run and before any
replacement grade ran. A fresh-context reviewer graded all 21 written
items of both runs blind and agreed with `sonnet` on 20 and with `haiku`
on 17. On the end-of-session case `haiku` answered no to the dead end and
the date on items whose text states both (in both arms), so its grades
there do not measure what the questions ask. `haiku` is replaced as the
second grader by `opus` at effort low, the same grading call otherwise,
for every item of both runs; `sonnet`'s grades stand. The gate below is
reported on `sonnet` and `opus`; the `haiku` figures stay in the records.

## The release gate (PLAN-0020 phase 4, prescription 4)

Stated before the run and computed by the runner from the counts:

- The skill must load in at least half the sessions of the with-skill
  arm, both cases together.
- The with-skill arm's hits, both cases together, must exceed the other
  arm's for both graders.

If either fails, the release waits for the owner's word.
