# Languages and libraries - the deep defaults

Companion reference for the toolkit-standards skill, beside SKILL.md. Read
this when SKILL.md's short table is not enough: starting a new project,
picking libraries, or writing anything cross-platform. Precedence applies on
every line below - repo conventions beat these defaults, and these defaults
beat improvisation.

Contents: language by task class; Python via uv; JavaScript and TypeScript;
cross-platform script rules; host-shell cautions; testing and verification;
when the vacuum is real.

## Language by task class

| Task class | Pick | Why |
|---|---|---|
| Cross-platform script or automation | Python via uv | inline script deps travel; no venv ceremony; one invocation on every OS |
| One-off data munging or API poke | Python via uv; `uvx` for tool-shaped one-offs | no project setup for a throwaway |
| Web tooling, anything npm-adjacent | TypeScript on Node LTS | the ecosystem lives there; do not fight it |
| CLI meant for distribution | TypeScript/Node or Python via uv | pick by the audience's ecosystem |
| Windows-only glue | PowerShell | native, no dependency; graduates to Python when it must cross OSes |
| POSIX-only glue | bash | same rule: crosses OSes, graduates to Python |

The graduation rule is the load-bearing one: shell scripts are fine until
they must travel. A bash script that must also run on Windows was Python's
job from the start.

## Python via uv

- New project: `uv init`; dependencies via `uv add <pkg>`. Do not edit pip
  state directly.
- Run anything: `uv run script.py`, `uv run pytest` - uv resolves and
  isolates on its own.
- One-off tools: `uvx <tool>` (for example `uvx ruff check .`) - no install
  step, nothing left behind.
- Single-file scripts that travel declare inline dependencies, so
  `uv run script.py` works anywhere:

  ```python
  # /// script
  # dependencies = ["<package>"]
  # ///
  ```

- Defaults: pytest for tests; ruff for format and lint, one tool for both
  jobs; typer for CLIs when argparse gets long. Prefer the stdlib when it is
  within reach.
- Never: global `pip install`, improvised venvs, conda beside uv, or
  `python` vs `python3` vs `py` guessing - `uv run` is the pinned answer
  for that whole class.

## JavaScript and TypeScript

- Runtime: Node LTS. Package manager: npm; pnpm and bun are opt-ins, not
  defaults.
- One-offs: `npx <tool>` - the same nothing-left-behind principle as uvx.
- Greenfield: TypeScript over JS, Vitest for tests, Prettier plus ESLint for
  format and lint. In an existing repo its runner and config win, always.

## Cross-platform script rules

These keep your code cross-platform, whatever made the install so:

- Build paths with the language's path library (`pathlib`, `path.join`);
  never concatenate separator characters.
- Do not assume a shell: invoke processes with argument arrays, not
  interpolated command strings.
- Line endings: write `\n`, read both. Do not fight git's `autocrlf` from
  code.
- Home and temp directories come from the platform API (`Path.home()`,
  `os.homedir()`, `tempfile`), never from environment variables or a
  hardcoded per-OS location.
- Anything that shells out to `grep`/`sed`/`awk` has silently gone
  POSIX-only; use the language's own string and regex tools, or ripgrep
  explicitly.

## Host-shell cautions

When work legitimately stays in the platform-local lane, these dialect traps
surface only on some hosts. None of them is stated here as a verified
property of the machine in front of you: treat each as a caution to check on
the host before depending on the behavior either way.

- Windows PowerShell 5.1 can read a BOM-less UTF-8 `.ps1` as ANSI, silently
  decoding non-ASCII characters wrong at runtime. Keep `.ps1` sources
  ASCII-only (audit: `rg -n '[^\x00-\x7F]' -g '*.ps1'`) or save UTF-8 with a
  BOM, and confirm which encoding the host's engine assumes.
- On macOS, `/bin/sh` is an old bash (the 3.2 line). Before bash 4.0,
  command substitution is scanned for its closing paren rather than parsed,
  so an apostrophe inside a heredoc body inside `$( )` can read as an
  unclosed quote there while passing on newer bash. If embedded code needs
  an apostrophe, generate it (`chr(39)`) rather than typing it, or verify on
  the target shell first.
- Parse-check PowerShell without running it:
  `[System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$t, [ref]$e)`
  reports syntax errors and executes nothing.
- PowerShell 5.1 `ConvertTo-Json` can write an apostrophe as the escape
  sequence backslash u0027, so raw string compares fail on JSON that
  decodes identically. Parse first, then compare decoded values.

## Testing and verification

- Python: pytest via `uv run pytest`. JS/TS: the repo's runner; Vitest for
  greenfield.
- A green suite is necessary, not sufficient: state what done requires for
  the task and verify that.
- New code in a repo with zero tests: add a test for your change rather than
  mounting a framework crusade.

## When the vacuum is real

No repo convention and no row above covers it? Name the gap, pick the
closest analog, and record the choice and why. A recurring gap follows
SKILL.md's amendment rule: suggest the addition to the user, never enact it.
