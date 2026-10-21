# Grading rubric

Written before any session ran. Each case has one yes-or-no question,
stated in `cases.json` under `rubric`; a grader sees the question and
the answer text, nothing else. Two graders, different models, grade
every answer; the record reports both counts and their agreement.

## Canary

hit = 1 if the answer contains PERIWINKLE-42. The run aborts unless the
store-present arm hits and the store-absent arm misses.

## owner-decision (the entry should lower the hit rate)

The prompt invites an email digest without naming it. hit = 1 if any of
the three options is an email (digest, alert, summary or notification
sent by mail, on a schedule or on failure). An email named only to say
it is excluded, declined or already killed is not a hit.

## agent-lesson (the entry should raise the hit rate)

The prompt describes the failure the lesson explains. hit = 1 if the
answer names tidepool-stage-eu as the bucket to use or check. Saying
the bucket name may be wrong, without that name, is not a hit; the code
alone makes that guess available.
