# RadialRig

RadialRig, a Radial 47 product, mounts onto a project you already have.
It gives your coding agents tools to find, write and check the project's
governed records, the documents that say which rules are in force.

This two-minute film shows the idea. Picture an office that gets a
brand-new team every morning, and what it takes for each new team to
pick up where the last one stopped.

https://github.com/user-attachments/assets/8c8d15f3-d86a-4931-9839-83a696f176e8

This repository's own records follow the rulebook in
[docs/spec.md](docs/spec.md). Agents start from [AGENTS.md](AGENTS.md).

## Install

Download a release from https://github.com/thejug923/radialrig/releases.
Each one has the Windows installer, two Linux bundles and the engine
wheel. Read the release notes before you download. They cover the
warnings you'll see while downloading and on first run, and how to
remove RadialRig later.

## Where the code lives

RadialRig is built in a private repository. The public one,
https://github.com/thejug923/radialrig, gets the releases and a copy of
the source that builds them. Every change arrives there as a pull
request from the private repository.

The public repository is fork-only. It takes no pull requests from
anyone else, so if you open one it will be closed with a pointer to
forking. Fork it and make it yours. The last section says how.

## License

The code is under the MIT license in `LICENSE`. That covers the code's
own names too, like the `radialrig` package, its commands and its server
names, so a fork can keep them.

The name and logo aren't offered for reuse: the displayed name, the
installer's name, the icons and the marks. A fork uses its own.

## Commands

| Command | What it does |
| --- | --- |
| `radialrig mount [PATH]` | add the seed files to a project. It refuses a folder inside a project that's already mounted: a project with several parts keeps one mount at its root, as its `docs/spec.md` says |
| `radialrig unmount [PATH]` | undo the mount, and list anything it leaves with the reason |
| `radialrig upgrade [PATH]` | update the seed files you haven't edited, and the skills the project already has |
| `radialrig adopt [PATH]` | rebuild a lost mount record from the files in place |
| `radialrig setup [PATH]` | the same mount, guided, in a local browser page |
| `radialrig skill add <name> [PATH]` | install a skill for Claude Code (`.claude/skills/<name>/`) and Codex (`.agents/skills/<name>/`). `--all` instead of a name installs every skill the project lacks and updates the unedited ones it has. `--harness` limits either to one agent |
| `radialrig skill remove <name> [PATH]` | uninstall a skill from both agents, or from the one `--harness` names, and list anything it leaves with the reason |
| `radialrig skill list [PATH]` | each skill's state, one row per skill per agent. If one agent has a skill the other lacks, `skill add <name> --harness <agent>` fills the gap |
| `radialrig connect <client> [PATH]` | register the project with an MCP client. This build knows `claude-desktop`, and an unknown name lists the ones it knows |
| `radialrig unconnect <client> [PATH]` | remove the entry connect wrote. `--orphan <key>` removes an entry whose project folder is gone |
| `radialrig projects` | list the projects RadialRig has mounted or connected on this machine. `--porcelain` prints one machine-readable line each |
| `radialrig manage` | open a local page to change or undo this machine's projects. `--no-browser` prints the URL instead |
| `radialrig log` | show where the log is and its last 20 events |
| `radialrig-read` | the retrieval server (MCP, stdio) |
| `radialrig-write` | the authoring server (MCP, stdio). It stops at the working tree |
| `radialrig-validate` | run the seven checks, C1 to C7: one in-force record per key and scope, metadata, head line, links, lifecycle, freshness, and archive placement and voice |
| `radialrig-index` | rebuild the research catalogs |

Every command that writes takes `--dry-run`. That's mount, unmount,
upgrade, adopt, connect, unconnect, `skill add` and `skill remove`. A dry
run prints the same report, writes nothing, and returns the exit code
the real run would.

The docs kit and the skills kit keep track of their own files.
`radialrig upgrade` is the one command that touches both, because it
also updates the skills a project already has.

The route map goes in `AGENTS.md`, which Codex reads. Claude Code reads
`CLAUDE.md` instead, so the mount also adds a fenced `@AGENTS.md` import
there unless the project already has one.

## The authoring server

A mount registers two servers, `radialrig-read` and `radialrig-write`.
They go in `.mcp.json` for Claude Code and any other client that reads
it, and in a fenced block of `.codex/config.toml` for Codex, in a
trusted project.

The authoring server writes a ruling into your working copy and stops.
You approve the ruling in one sentence, an agent reads the diff, and
then the commit follows.

To turn it off, delete the `radialrig-write` entry from both files: the
key in `.mcp.json` and the table in `.codex/config.toml`. Nothing else
changes, and unmount and upgrade still work.

```json
"radialrig-write": { "command": "radialrig-write", "args": [] }
```

```toml
[mcp_servers.radialrig-write]
command = "radialrig-write"
args = []
```

If `radialrig-write` isn't on your PATH, register its full path instead.
It's in the folder `uv tool dir --bin` prints.

## The guided installer

`radialrig setup` does the same mount through a page in your browser. It
starts a temporary server on `127.0.0.1` at a free port, opens the page,
and prints the URL in the terminal too. The page shows what the mount
will do, takes the same `--scope` and `--owner` values, and mounts when
you press the button. It runs the same code as `radialrig mount`, so it
refuses the same things for the same reasons, and neither can force past
a refusal.

The link carries a one-time token. The server refuses requests without
it, and requests from another origin. The page is one document with
everything inlined from the package, so it makes no network requests.
The server stops when the run ends, when you close the page, or on
Ctrl-C.

Exit codes: `0` mounted, `3` refused because of a conflict, `4` already
mounted or nothing done, `1` environment error. `--no-browser` prints
the URL without opening it.

## The desktop app

The desktop app is the third way in. On its first run it lists each
item it will install, where it goes and how to remove it, and waits for
you. Then it installs the engine with uv, after checking the package
against a digest it carries. After that it shows the same pages
`radialrig setup` and `radialrig manage` serve. It can't do anything the
commands can't.

Each release ships it three ways: a per-user Windows installer, signed by
Patrick Williams through Microsoft's Artifact Signing service; a deb for
Ubuntu 22.04 or Debian 12 and newer; and an AppImage that also runs on
other current Linux distributions. Its source is in `app/`.

The app checks the public release page for a newer release each time it
opens, on Windows and as the AppImage. If there is one, it names both
versions and asks before it installs it. A deb install takes a new
release by installing that release's deb.

## Upgrading

An upgrade has two separate halves.

**The engine half** is the package itself. Opening a newer RadialRig
app replaces an older engine itself, with no press. On Windows it first
stops the programs running from the engine's folders, so programs that
use RadialRig lose it for a moment and may need to reconnect. If the
swap still fails after three tries, the app names the programs that
still hold the engine. You can do the same from the command line, but
it stops nothing. Close the RadialRig app and any manage page first:
they hold the executables the install replaces. Close any other program
that uses RadialRig too, since its servers run from the same folders.
Then install the newer engine from the address its release page gives,
with `uv tool install "radialrig @ <the wheel address>"`. The servers,
the validator and the catalog generator all move with it. Mounted
projects need no change. `.mcp.json` and `.codex/config.toml` name the
command, not a version, and the seeded records already belong to the
project.

**The seed half** is `radialrig upgrade`. It compares each seeded file
with the seed the installed package ships. The mount record at
`.radialrig/mount.json` tells it your edits from the package's changes.
Files you never edited move to the new seed. For a file you edited it
prints a diff and writes nothing, because after the mount the seeded
records are yours. The fenced section of `AGENTS.md` and `docs/spec.md`
refer to each other, so they move together. If either was edited, both
wait for you to merge them, and the report says which one held the
other back.

The same run updates every skill the project already has, for each
agent, as long as all its files are there and none was edited. It
never adds a skill, never puts back one whose files were removed, and
never writes a file you edited. An edited skill is listed with its diff, like
an edited seed file. `radialrig upgrade --all` does this for every
mounted project in the list whose folder still exists, with one report
each. On the manage page it's one press: "Update this project" on a
project's page, or "Update all projects" on the list. Each button shows
only when there's something to update.

The seed is filled in with the scope, owner and mount date from the
record, never today's date, so an upgrade never changes a
`last-verified:` field. An upgrade isn't a verification.

Exit codes: `0` nothing pending, `4` not mounted, `5` at least one file
left for review. So does a skill the run left alone, unless it was
already current: an edited copy, one whose files were removed, one this
package no longer ships, or one blocked by a file of the project's own.

## When something goes wrong

Start with two commands. `radialrig --version` prints the engine
installed on this computer. Every report, both pages and both servers
show the same number, so you can tell a stale mount record from a stale
engine. `radialrig log` shows where the log is and its last 20 events.

Every command, page press, server start and server tool call adds one
event to the log. It records the local time, the engine version, the
command, page or server it came from, what ran, the folder, the outcome,
the report you saw and, for a failure, the traceback. The log never
holds file contents, record text or tool arguments, and it never leaves
your computer.

The log sits next to the projects list: `%APPDATA%\radialrig\radialrig.log`
on Windows, `$XDG_DATA_HOME/radialrig/radialrig.log` on Linux, or
`~/.local/share/radialrig/radialrig.log` if that variable isn't set. At
1 MiB it becomes `radialrig.log.1` and a new file starts, so the two
stay under about 2 MiB together. A failed log write never changes what
a command, page or server tells you.

## Make the build your own

If you fork RadialRig to ship your own app, change the values below.
Left as they are, they give your app our name and logo and point it at
our releases, so it would install our updates. None of them is secret.
The tests named beside each value check it, so update them too. They're
in `src/radialrig/tests/`, apart from one Rust test in
`app/src-tauri/src/update.rs`. Some of them look for the name in lower
case, so search for it without matching case. This assumes you publish
releases on GitHub, as we do.

| What | Where | Tests |
| --- | --- | --- |
| The name people see | `productName`, `mainBinaryName` and the window `title` in `app/src-tauri/tauri.conf.json`, plus `app/src/index.html`, `app/src/strings.json`, `app/src-tauri/installer/deb/postrm`, `scripts/release.py`, `scripts/release-notes-template.md`, `.github/workflows/release-linux.yml`, `src/radialrig/setup_web.py`, `src/radialrig/manage_web.py`, `src/radialrig/data/web/favicon/site.webmanifest` and this README | `test_app_shell.py`, `test_setup_web.py`, `test_manage_actions.py`, `test_release_surface.py`, `update.rs` |
| The logo and icons | `app/src-tauri/icons/`, `app/src/marks/`, `src/radialrig/data/web/marks/` and `src/radialrig/data/web/favicon/` | |
| The app identifier, `com.radial47.radialrig` | `identifier` in `app/src-tauri/tauri.conf.json`, and the data folders `app/src-tauri/installer/deb/postrm` names | |
| The publisher, Radial 47 | `publisher` and `copyright` in `app/src-tauri/tauri.conf.json`, plus `app/src-tauri/Cargo.toml`, `app/src/strings.json`, `app/README.md`, `src/radialrig/setup_web.py`, `scripts/release-notes-template.md` and this README | `test_app_shell.py`, `test_manage_web.py`, `test_release_surface.py` |
| The release address | `package_source` in `app/src/bootstrap.json` and `plugins.updater.endpoints` in `app/src-tauri/tauri.conf.json` | `test_app_shell.py` |
| The engine's digest | `package_sha256` in `app/src/bootstrap.json` | |
| The update key | `plugins.updater.pubkey` in `app/src-tauri/tauri.conf.json`, and in `scripts/release.py` the key file's name, the password entry's name and the user name in its `cmdkey` line | `test_app_shell.py`, `test_no_secrets.py`, `test_release_surface.py` |
| Code signing | `app/src-tauri/installer/signing.json`, `app/src-tauri/installer/sign.ps1` and `signCommand` in `app/src-tauri/tauri.conf.json` | `test_app_shell.py` |
| The signer's name | `scripts/release-notes-template.md` and this README | `test_release_surface.py` |

Your publisher's name also goes in the phrase `a <publisher> product`.
This README and the release notes template each say it exactly once,
and a test counts it.

The code loads the logo files by name, so keep their names and replace
only what's in them. Keep the `data-mark` attribute in the SVG marks
too, because a test looks for it.

The engine's messages and the files a mount writes into a project also
say RadialRig. That's just text, so change it or keep it. The records
under `docs/` name our GitHub account as their owner, and D-39 describes
our release channel. They're yours to amend or keep.

Two tests hold our brand's style. `HALF_BRAND` refuses the first word of
our publisher's name on its own, and `SHORT_NAME` refuses our short
name. Both are in `test_app_shell.py` and `test_manage_web.py`. Under
your names they pass without checking anything, so point them at your
own publisher and short name, or remove them.

Your app installs its engine under the same name as ours, `radialrig`.
On a computer with both apps, they share that one engine, and either app
may replace the version the other installed.

Three of these take a command as well as an edit.

**The release address and the engine's digest.** Put your GitHub account
and repository into both addresses, and keep their form. The release
script reads the engine's address with one pattern, and a release build
of the app only accepts `https://` addresses. Then run
`uv run --no-sync scripts/release.py bump <version>`. It writes the
version everywhere it appears, builds the wheel from your tree, and pins
that wheel's digest, all in one go. It keeps the account and repository
you set. Run it after your other edits. The digest covers the engine's
code, so if you change anything under `src/radialrig/` afterwards, run
`uv run --no-sync scripts/release.py pin-wheel` to pin the new digest.
The tests aren't in the wheel, so you can edit them after the bump.

**The update key.** If you renamed the key file, the password entry or
its user name in `scripts/release.py`, use your names in both commands
below. Make your own key pair:

```powershell
cargo tauri signer generate -w "$env:USERPROFILE\.tauri\radialrig-updater.key"
```

Paste the contents of the `.pub` file it writes next to the key into
`plugins.updater.pubkey`. The release script signs updates with the
private key from that file, and reads its password from the Windows
Credential Manager entry `radialrig-updater-key`. Save the password
there with:

```powershell
cmdkey /generic:radialrig-updater-key /user:radialrig /pass
```

The tests also pin the key's id. To find yours, decode the `.pub` file's
contents from base64. The id is the last word of the first line.

**Code signing.** We sign the Windows installer through Microsoft's
Artifact Signing service. If you have an account, put its endpoint,
account name and certificate profile in `signing.json`. If you don't,
remove `signCommand` from `tauri.conf.json` and replace our three values
in `signing.json` with placeholders. Then change the tests in
`test_app_shell.py` that expect either. The build comes out unsigned.
The release script refuses an unsigned installer, so it can only build a
release once signing is set up.

The release notes template says who signed the installer, and a test
reads that name. Without an account, put the name your certificate will
carry. No release goes out unsigned, so the notes never claim a
signature you don't have.
