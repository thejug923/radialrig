# The desktop shell

This directory holds the RadialRig desktop shell, a Radial 47
product. The shell is a Tauri v2 window. The toolkit is pinned by
PLAN-0008 constraint 11. The shell never bundles the engine
(constraint 2). It installs the engine with uv, checking the package
against a digest it carries first, starts it, and then shows the pages
the engine itself serves.

## What is here

- `src/index.html` and `src/bootstrap.js` — the bootstrap screen.
  This is the one screen the shell draws itself, because it runs
  before there is an engine to draw anything. It shows the consent
  gate, a progress line, and any stop the run reaches.
- `src/strings.json` — every sentence the shell says. Three readers
  share this one file: the Rust reads it with `serde_json`, the
  bootstrap page fetches it, and the pytest hold reads it with
  `json.loads`.
- `src/bootstrap.json` — the pins. The uv version, the two archive
  addresses, the archive checksum, the package source, the Python
  pin, and the oldest engine this shell drives.
- `src/styles.css`, `src/marks/`, `src/fonts/` — the paint and the
  shipped assets. The colors are copied from
  `src/radialrig/data/web/page.css` and a test holds them level.
- `src-tauri/src/` — the Rust. `main.rs` builds the window,
  `strings.rs` and `config.rs` read the two compiled-in files,
  `bootstrap.rs` runs the install steps, `attach.rs` starts the
  engine and follows the address it prints, and `picker.rs` is the
  folder picker that writes a picked path into the engine page's
  own folder field.
- `src-tauri/` also holds the Tauri config, the capability file,
  and the icon. The icon is the shipped 512 px dark rounded square
  repacked into `icon.ico`, downscaled only (constraint 7).

## The pytest hold

`src/radialrig/tests/test_app_shell.py` checks this tree from the
outside. It pins the key set of `strings.json`, sweeps every
sentence for the words the brand forbids, checks the pins, holds
the window name level across the four files Tauri needs it in, and
checks that no color is invented for this window. Run it with the
rest of the suite:

```
uv run --no-sync --with pytest pytest -q
```

## Toolchain

- Rust and cargo (built with rustc 1.97.0).
- tauri-cli v2 (`cargo install tauri-cli --version "^2"`; built
  with v2.11.4).
- Visual Studio Build Tools with the Windows SDK (the MSVC
  linker).
- WebView2 runtime. Windows 11 already ships it.
- NSIS. The Tauri bundler downloads it on first build, so there is
  no separate install.

## Build

Run from this directory:

```
cargo tauri build
```

The per-user NSIS installer, signed through `installer/sign.ps1`, lands
at `src-tauri/target/release/bundle/nsis/RadialRig_<version>_x64-setup.exe`.
It installs under `%LOCALAPPDATA%` and asks for no admin rights
(constraint 4). That signing step needs the Windows SDK, the Microsoft
client library and an `az login`; `reports/signing-runbook-2026-09-03.md`
names all three and the build stops with one sentence when any is
missing.
