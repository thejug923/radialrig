"""Reproduce every recorded eval answer against the live packaged read server.

The pairs in `retrieval-evals.xml` were authored by solving each question
through the read server's tools. This runner replays that resolution: one
solver per question, keyed by the question's opening words, each
restricted to the same read-only tools an evaluated model would have. A
question file that carries only some of the questions runs as it is. It
compares by exact string and exits non-zero on any miss.

    uv run --with "mcp>=2,<3" python evals/run_retrieval_evals.py

The directory is ungoverned evidence, not a governed record, and is not
part of the pytest suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Coroutine

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from radialrig._shell import split_command

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
DEFAULT_COMMAND = "uv run radialrig-read"

# The records spell small counts as words; one question asks for the integer.
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
}


class Surface:
    """The read server's five tools, each returning parsed JSON."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self.calls = 0

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a tool by name. Used directly only when the name is discovered."""
        self.calls += 1
        result = await self._client.call_tool(name, arguments or {})
        text = "\n".join(getattr(block, "text", "") for block in result.content)
        return json.loads(text)

    async def get_ruling(self, query: str, scope: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"query": query}
        if scope is not None:
            args["scope"] = scope
        return await self.call("get_ruling", args)

    async def list_rulings(self, type_: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if type_ is not None:
            args["type"] = type_
        return await self.call("list_rulings", args)

    async def search_history(self, query: str) -> dict[str, Any]:
        return await self.call("search_history", {"query": query})

    async def check_docs(self) -> dict[str, Any]:
        return await self.call("check_docs")

    async def get_guidance(self, topic: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if topic is not None:
            args["topic"] = topic
        return await self.call("get_guidance", args)


def _flat(text: str) -> str:
    """Collapse the record's hard line wrapping so a phrase search survives it."""
    return re.sub(r"\s+", " ", text)


# Five solvers each sweep every spec body; the sweep is one fetch, shared.
_SPEC_LOCK = asyncio.Lock()
_SPEC_RECORDS: list[dict[str, Any]] | None = None


async def _spec_records(s: Surface) -> list[dict[str, Any]]:
    """Every specification record, fetched once for the whole run."""
    global _SPEC_RECORDS
    async with _SPEC_LOCK:
        if _SPEC_RECORDS is None:
            listing = await s.list_rulings(type_="spec")
            _SPEC_RECORDS = [
                await s.get_ruling(entry["id"]) for entry in listing["records"]
            ]
    return _SPEC_RECORDS


async def _spec_carrying(s: Surface, marker: str) -> dict[str, Any]:
    """Find the one specification record whose body carries `marker`."""
    found = [
        record for record in await _spec_records(s)
        if marker in _flat(record["body"])
    ]
    assert len(found) == 1, [record["id"] for record in found]
    return found[0]


def _paragraph(body: str, lead_in: str) -> str:
    """The blank-line-delimited paragraph of `body` that opens with `lead_in`.

    Records mark a self-contained paragraph with a bold lead-in such as
    `**The bound.**`; the marker survives rewording of the prose after it.
    """
    for block in re.split(r"\n\s*\n", body):
        if block.lstrip().startswith(lead_in):
            return _flat(block)
    raise AssertionError(f"no paragraph opens with {lead_in!r}")


def _run_counts(text: str) -> set[int]:
    """Every run count `text` states, whether spelled `fourteen runs` or `14 runs`."""
    counts: set[int] = set()
    for word in re.findall(r"\b([A-Za-z]+|\d+) runs\b", _flat(text)):
        if word.isdigit():
            counts.add(int(word))
        elif word.lower() in NUMBER_WORDS:
            counts.add(NUMBER_WORDS[word.lower()])
    return counts


def _sections(body: str) -> dict[str, str]:
    """Split a record body into its `### N.M` subsections.

    A subsection ends at the next heading of any level; before 2026-08-25
    nothing followed the last subsection, then SPEC-1 gained a trailing
    `## History` section that must not count as 8.7 text.
    """
    parts = re.split(r"^### (\d+\.\d+)[^\n]*$", body, flags=re.M)
    out: dict[str, str] = {}
    for number, text in zip(parts[1::2], parts[2::2]):
        out[number] = re.split(r"^#{1,6} ", text, flags=re.M)[0]
    return out


# --- solvers -----------------------------------------------------------
# One per qa_pair, numbered in the order of the full question file. Each
# returns the answer as a string; SOLVERS below says which question each
# one answers.


async def solve_1(s: Surface) -> str:
    """Concept query -> typed candidates -> the record holding the argument."""
    hit = await s.get_ruling("assumed decided verified")
    assert hit["kind"] == "ambiguous", hit["kind"]
    reasoning: list[str] = []
    for candidate in hit["candidates"]:
        record = await s.get_ruling(candidate["id"])
        if "this record holds the reasoning" in _flat(record["body"]):
            reasoning.append(record["path"])
    assert len(reasoning) == 1, reasoning
    return reasoning[0]


async def solve_2(s: Surface) -> str:
    """Archived wording changes -> the identifier of the record that forced them.

    The answer was the forcing record's `effective` date until 2026-08-18,
    when a D-33 amendment moved that date and went stale. The identifier
    is the one handle the change procedure never moves (D-06).
    """
    scrub = await s.search_history("authorship scrub")
    assert len(scrub["matches"]) == 4, len(scrub["matches"])
    named = await s.search_history("authorship, which")
    forcing = {
        key
        for match in named["matches"]
        for key in re.findall(r"\[(D-\d+)\]", match["excerpt"])
    }
    assert len(forcing) == 1, forcing
    record = await s.get_ruling(forcing.pop())
    return record["id"]


async def solve_3(s: Surface) -> str:
    """The earlier plan's first phase -> the run count a record and its archive agree on.

    Until 2026-09-04 two in-force records stated the count and the
    solver matched the phrase both used. The D-18 amendment of that day
    cites the plan instead of repeating it, so the second in-force
    citation is gone; the archive still carries the count in the prior
    wording of the record that keeps it. The archived entry is tied to
    its live record by the derived id and the record's `## History`
    link, not by phrasing.
    """
    hit = await s.get_ruling("PLAN-0003")
    assert hit["kind"] == "ambiguous", hit["kind"]
    citing = {candidate["id"] for candidate in hit["candidates"]}
    history = await s.search_history("fresh agents")
    archived: list[tuple[str, str, int]] = []
    for match in history["matches"]:
        derived = re.match(r"([A-Z]+-\d+)-history-", match["id"])
        assert derived, match["id"]
        counts = _run_counts(match["excerpt"])
        if counts and derived.group(1) in citing:
            assert len(counts) == 1, (match["id"], counts)
            archived.append((match["path"], derived.group(1), counts.pop()))
    assert len(archived) == 1, archived
    path, key, count = archived[0]
    live = await s.get_ruling(key)
    _, _, linked = live["body"].partition("## History")
    assert Path(path).name in linked, (key, path)
    assert _run_counts(live["body"]) == {count}, (key, count)
    return str(count)


async def solve_4(s: Surface) -> str:
    """The archived tool contract -> the verb its amendment added.

    Until 2026-08-25 one spec-type archive entry existed, so type alone
    selected it. The SPEC-1 glob correction added a second; the tool
    contract is now the one whose entry carries the tool-adding
    amendment sentence the question asks about.
    """
    entries = await s.search_history("grade:")
    specs = [match for match in entries["matches"] if "type: spec" in match["excerpt"]]
    amended = await s.search_history("added the")
    excerpts = {match["path"]: _flat(match["excerpt"]) for match in amended["matches"]}
    contracts = [
        (match, verb)
        for match in specs
        for verb in re.findall(
            r"added the \w+ tool,\s*`([a-z_]+)`", excerpts.get(match["path"], "")
        )
    ]
    assert len(contracts) == 1, [match["id"] for match, _ in contracts]
    archived, verb = contracts[0]
    replaced = re.search(r"replaced-by:\s*(\S+)", archived["excerpt"])
    assert replaced, archived["excerpt"]
    live = await s.get_ruling(replaced.group(1))
    assert archived["id"] in _flat(live["body"]), archived["id"]
    assert verb in _flat(live["body"]), verb
    served = await s.call(verb)
    assert "kind" in served, served
    return verb


async def solve_5(s: Surface) -> str:
    """Sweep every in-force record for the one promoted from an ungoverned path."""
    listing = await s.list_rulings()
    promoted: list[str] = []
    for entry in listing["records"]:
        record = await s.get_ruling(entry["id"])
        if re.search(r"stood at `[^`]+` as an ungoverned", _flat(record["body"])):
            promoted.append(record["id"])
    assert len(promoted) == 1, promoted
    return promoted[0]


async def solve_6(s: Surface) -> str:
    """The refusal bound in the retrieval contract -> the topic it borrows.

    Until 2026-09-04 the contract named the topic in one phrase beside
    the figure and the solver matched that phrase. The amendment of that
    day reworded the paragraph; the figure now stands under a
    `**The bound.**` lead-in, and the topic is whichever catalog name
    that paragraph carries.

    Until the research refresh of 2026-09-25 the topic's guidance also
    stated the figure, as its line limit for a `SKILL.md` body, and the
    solver checked that. The refresh budgets that body in tokens, so the
    check is now that the guidance still carries the `SKILL.md` body rule
    the contract says it borrows from.
    """
    contract = await _spec_carrying(s, "**The bound.**")
    bound = _paragraph(contract["body"], "**The bound.**")
    assert re.search(r"over \d+ lines", bound), bound
    borrowed = re.search(r"rule for a `([^`]+)` body", bound)
    assert borrowed, bound
    catalog = await s.get_guidance()
    named = {entry["topic"] for entry in catalog["topics"]} & set(
        re.findall(r"[a-z]+(?:-[a-z]+)*", bound)
    )
    assert len(named) == 1, named
    guidance = await s.get_guidance(named.pop())
    assert f"{borrowed.group(1)} body" in _flat(guidance["body"]), borrowed.group(1)
    return guidance["topic"]


async def solve_7(s: Surface) -> str:
    """The exclusion glob the rulebook records as the route map's form.

    Until 2026-08-25 D-04 stated one form as present-tense working and
    the rooted form as failing, and the glob was read off that contrast.
    The amendment dated the measurements, so the standing statement of
    the route map's form is SPEC-1 section 2's: the one backticked
    !-glob the rulebook carries.
    """
    rulebook = await _spec_carrying(s, "## 8. Change procedure")
    forms = set(re.findall(r"`(![^`]+)`", _flat(rulebook["body"])))
    assert len(forms) == 1, forms
    return forms.pop()


async def solve_8(s: Surface) -> str:
    """The amend-time archive filename -> the subsection that prescribes it.

    Until 2026-08-18 the question claimed every reachable archive file
    shared the shape, a premise the first 8.2/8.3 retirement would break;
    it now rests on the rule text alone, so no solver premise sweeps the
    live archive.
    """
    rulebook = await _spec_carrying(s, "## 8. Change procedure")
    prescribing = [
        number
        for number, text in _sections(rulebook["body"]).items()
        if re.search(r"[A-Z]+-\d+-history-\d{4}-\d{2}\.md", text)
    ]
    assert len(prescribing) == 1, prescribing
    return prescribing[0]


async def solve_9(s: Surface) -> str:
    """The analogous worked case in the change procedure -> is the id kept?

    The question poses an approval-regime change in vocabulary the
    rulebook does not use. Until 2026-09-04 section 8.3 carried the
    worked case inline and named its route in the same sentence; that
    day the case moved to the one decision 8.3 cites. That record poses
    the case as quoted questions, and the route for the approval
    question is the subsection named right after it.
    """
    rulebook = await _spec_carrying(s, "## 8. Change procedure")
    sections = _sections(rulebook["body"])
    cited = set(re.findall(r"\[(D-\d+)\]\(", sections["8.3"]))
    assert len(cited) == 1, cited
    decision = await s.get_ruling(cited.pop())
    worked = _paragraph(decision["body"], "**Worked case.**")
    posed = re.findall(r'"[^"]*\?"', worked)
    answered = re.split(r'"[^"]*\?"', worked)[1:]
    routes = [
        re.findall(r"SPEC-1 (\d+\.\d+)", after)
        for question, after in zip(posed, answered)
        if "approv" in question.lower()
    ]
    assert len(routes) == 1 and len(routes[0]) == 1, routes
    route = sections[routes[0][0]]
    return "True" if "Keep the `id`" in route else "False"


async def solve_10(s: Surface) -> str:
    """The contract's fallback rule -> the decision it cites -> that title."""
    contract = await _spec_carrying(s, "word-assisted fallback")
    cited = re.search(r"fallback\s*\(\[(D-\d+)\]", contract["body"])
    assert cited, "fallback citation not found"
    listing = await s.list_rulings()
    titles = {entry["id"]: entry["title"] for entry in listing["records"]}
    return titles[cited.group(1)]


Solver = Callable[[Surface], Coroutine[Any, Any, str]]

# The opening words of each question -> its solver.
SOLVERS: dict[str, Solver] = {
    "A governed record carries both a label": solve_1,
    "Four archived entries filed for the same month": solve_2,
    "Before any authoring verb existed": solve_3,
    "One archived entry preserves a tool contract": solve_4,
    "Most in-force records were governed from the day": solve_5,
    "The retrieval contract withholds the body": solve_6,
    "An adopter is configuring a search tool": solve_7,
    "A decision record is amended": solve_8,
    "A governed record settles which reviewer": solve_9,
    "When a query matches no key and no title": solve_10,
}


def solver_for(question: str) -> Solver | None:
    """The one solver whose opening words begin `question`, or None."""
    found = [solver for opening, solver in SOLVERS.items() if question.startswith(opening)]
    return found[0] if len(found) == 1 else None


def load_pairs(path: Path) -> list[tuple[str, str]]:
    root = ET.parse(path).getroot()
    pairs = []
    for pair in root.findall("qa_pair"):
        question = (pair.findtext("question") or "").strip()
        answer = (pair.findtext("answer") or "").strip()
        pairs.append((question, answer))
    return pairs


async def run(command: list[str], cwd: Path, eval_file: Path) -> int:
    pairs = load_pairs(eval_file)
    solvers = [solver_for(question) for question, _answer in pairs]
    unsolved = [index for index, solver in enumerate(solvers, start=1) if solver is None]
    if not pairs:
        print(f"error: {eval_file} holds no qa_pair", file=sys.stderr)
        return 2
    if unsolved:
        print(f"error: no solver, or more than one, for pair(s) {unsolved}", file=sys.stderr)
        return 2

    params = StdioServerParameters(command=command[0], args=command[1:], cwd=str(cwd))
    async with Client(stdio_client(params)) as client:

        async def attempt(solver: Solver) -> tuple[Surface, str]:
            surface = Surface(client)
            try:
                return surface, await solver(surface)
            except Exception as error:  # noqa: BLE001 - reported, not raised
                return surface, f"<solver error: {error!r}>"

        # The solvers are independent, so they run together; the report
        # prints afterward, in file order.
        outcomes = await asyncio.gather(*(attempt(solver) for solver in solvers))

    reproduced = 0
    for index, ((question, expected), (surface, actual)) in enumerate(
        zip(pairs, outcomes), start=1
    ):
        ok = actual == expected
        reproduced += ok
        label = question if len(question) <= 64 else question[:61] + "..."
        print(
            f"[{'ok' if ok else 'MISS'}] {index:2d}  {surface.calls:3d} calls  "
            f"expected={expected!r} actual={actual!r}\n"
            f"          {label}"
        )

    print(f"\n{reproduced}/{len(pairs)} reproduced")
    return 0 if reproduced == len(pairs) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default=DEFAULT_COMMAND, help="stdio server command")
    parser.add_argument("--cwd", default=str(REPO_ROOT), help="working directory for the server")
    parser.add_argument("--evals", default=str(EVALS_DIR / "retrieval-evals.xml"), help="eval XML")
    args = parser.parse_args()
    return asyncio.run(run(_split_command(args.command), Path(args.cwd), Path(args.evals)))


def _split_command(text: str) -> list[str]:
    # POSIX splitting mangles a Windows path; see radialrig._shell.
    return split_command(text, posix=sys.platform != "win32")


if __name__ == "__main__":
    raise SystemExit(main())
