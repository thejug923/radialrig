//! D13 `bootstrap.rs`: what the first run would change, and the state
//! the window is in while it changes it.
//!
//! Four things live here. The discovery pass D2 and D4 put in front of
//! the gate — is there a uv at the one documented location, does it
//! answer a version probe, is the engine already installed. The steps
//! themselves: D1's archive over https with its digest taken over the
//! bytes in memory and its two members read out of that same buffer, D2's
//! one Path entry, and D4's `uv tool install`, which downloads the
//! pinned wheel on the same terms and proves it against the pinned
//! digest before uv is given it, together with the engine swap PLAN-0021
//! adds, which replaces an engine older than this app needs with no press.
//! The one-shot state machine
//! D12 pins, with the two commands that drive it. And the journey that
//! runs the named steps in order and hands the window to `attach`.
//!
//! Two promises run through all of it. Nothing is named at the gate that
//! will not run, and nothing runs that was not named (R6). And no byte
//! this app did not write is ever written over (constraint 5) — which is
//! why the archive's members are only ever created, never replaced, why
//! `--force` is never passed to uv, and why a `radialrig.exe` with no uv
//! entry behind it stops the run where it stands, unless this app's own
//! install step left it there or it is still, byte for byte, the
//! launcher of the engine a running swap is replacing.

use serde::{Deserialize, Serialize};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use crate::attach;
use crate::config;
use crate::holders;
use crate::strings;
use tauri::{AppHandle, Emitter, Manager, State};

/// The one event that carries the whole of what the screen shows.
///
/// One event rather than several: the page draws itself from a single
/// picture, so a reload, a retry, and the return navigation D7 makes all
/// land on the same rendering path instead of three that can disagree.
pub const STATE_EVENT: &str = "shell-state";

/// The four states the screen can be in.
pub const PHASE_GATE: &str = "gate";
pub const PHASE_RUNNING: &str = "running";
pub const PHASE_FAILED: &str = "failed";
pub const PHASE_DONE: &str = "done";

/// The window this shell draws, by the label the config gives it.
pub const MAIN_WINDOW: &str = "main";

/// Where D2 looks for a uv, and the only place it looks. A generic PATH
/// search could turn up a stranger's copy from any directory a user can
/// write to. The same directory on both platforms, spelled with each
/// one's own separator.
#[cfg(windows)]
const DOCUMENTED_DIR: &str = ".local\\bin";
#[cfg(not(windows))]
const DOCUMENTED_DIR: &str = ".local/bin";

/// The uv program as this platform names it, under the documented
/// directory.
#[cfg(windows)]
const UV_EXE: &str = "uv.exe";
#[cfg(not(windows))]
const UV_EXE: &str = "uv";

/// The Windows archive's two members, by name. `uvw.exe` is the third
/// program that archive carries and is deliberately left in it: it is
/// the one uv binary with a recorded quarantine history (uv issue
/// #15011). These are the zip's own spellings, fixed, because the zip
/// reader reads the Windows archive and no other.
#[cfg_attr(not(windows), allow(dead_code))]
const ZIP_UV: &str = "uv.exe";
#[cfg_attr(not(windows), allow(dead_code))]
const ZIP_UVX: &str = "uvx.exe";
#[cfg_attr(not(windows), allow(dead_code))]
const ZIP_MEMBERS: [&str; 2] = [ZIP_UV, ZIP_UVX];

/// The Linux archive's shape: one directory, two programs nested under
/// it, and no third program at all — `uvw.exe` is Windows only. These
/// are the tar's own spellings, fixed, read off the pinned archive
/// itself on 2026-08-23.
#[cfg_attr(windows, allow(dead_code))]
const TAR_DIR: &str = "uv-x86_64-unknown-linux-gnu";
#[cfg_attr(windows, allow(dead_code))]
const TAR_UV: &str = "uv";
#[cfg_attr(windows, allow(dead_code))]
const TAR_UVX: &str = "uvx";
#[cfg_attr(windows, allow(dead_code))]
const TAR_MEMBERS: [&str; 2] = [TAR_UV, TAR_UVX];

/// The engine's own command, under whatever directory uv names.
#[cfg(windows)]
pub const ENGINE_EXE: &str = "radialrig.exe";
#[cfg(not(windows))]
pub const ENGINE_EXE: &str = "radialrig";

/// Which operating system this build is for, as the page reads it. The
/// strings file carries a sentence per platform where the two differ,
/// and this is the key the page picks the platform's own words by.
pub const PLATFORM: &str = if cfg!(windows) { "windows" } else { "linux" };

/// The engine as uv knows it: the name of the tool entry uv keeps, which
/// is what `uv tool uninstall` acts on and what `uv tool list` prints.
pub const ENGINE_TOOL: &str = "radialrig";

/// D2's record: the file that says what this app put on the user's Path,
/// written into the app's own data directory before the value is written.
pub const PATH_RECORD: &str = "path-entry.json";

/// The record that says this app was part way through installing the
/// engine. It goes down before `uv tool install` runs and is taken away
/// once the install has answered, so a command standing in the documented
/// directory with no uv entry behind it can be told apart: with this
/// record it is what this app's own step left, and without it, it belongs
/// to someone else and is never touched. The record names the command it
/// was writing and the moment it went down, and it speaks only for that
/// path and only for a command written no earlier than that moment.
pub const INTENT_RECORD: &str = "install-intent.json";

/// How long the one archive GET may take from start to finish.
const ARCHIVE_WAIT: Duration = Duration::from_secs(180);

/// The most of an answer this process will hold. The pinned archive is
/// well under it; the bound exists because the bytes stay in memory.
const ARCHIVE_CAP: u64 = 128 * 1024 * 1024;

/// How many redirects the one GET may follow. Both hosts answer with one
/// hop to their own storage; the bound is what closes a loop.
const ARCHIVE_HOPS: usize = 10;

/// The scheme D1 accepts, and the only one. A redirect that leaves it
/// puts the bytes this app runs on the wire for anyone to answer.
const HTTPS_SCHEME: &str = "https";

/// Where the pinned wheel is put down while uv reads it: a directory of
/// this app's own, under the app's own data directory, so the file is
/// never written anywhere the user keeps their own work. It is taken
/// away again as soon as the install has answered.
const PACKAGE_DIR: &str = "package";

/// The one file name shape the pinned address may end with.
const WHEEL_SUFFIX: &str = ".whl";

/// One failure, named by the key whose sentence says it, with the one
/// value that sentence has a slot for.
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct Slot {
    pub key: &'static str,
    pub detail: String,
}

impl Slot {
    pub fn bare(key: &'static str) -> Self {
        Slot { key, detail: String::new() }
    }

    pub fn of(key: &'static str, detail: impl Into<String>) -> Self {
        Slot { key, detail: detail.into() }
    }
}

/// Everything the bootstrap screen shows, at one moment.
#[derive(Clone, Debug, Serialize)]
pub struct View {
    pub phase: &'static str,
    /// Which operating system this build is for, so the page can pick
    /// the platform's own sentence where the strings file carries one
    /// per platform.
    pub platform: &'static str,
    /// The oldest engine this build drives, so the old-engine stop can
    /// name it. It belongs to the build, as the platform does, and not
    /// to any one stop, which keeps a stop at one value (BUG-0017).
    pub engine_needed: &'static str,
    /// The gate blocks, in the order they are named, for the steps that
    /// will actually run (R6).
    pub blocks: Vec<&'static str>,
    /// The absolute path of a uv this machine already carries, when
    /// discovery found one there.
    pub found_uv: Option<String>,
    /// The directory the Path block would name.
    pub bin_dir: Option<String>,
    /// The debug switch's notice, when it is in force.
    pub notice: Option<Slot>,
    /// What the PATH read found, on the platform where that step is a
    /// report rather than a write (PLAN-0009 phase 3 prescription 3).
    pub path_report: Option<Slot>,
    /// The step that is running.
    pub progress: Option<&'static str>,
    /// The stop, and whether it pairs with the retry control.
    pub failure: Option<Slot>,
    /// The one sentence an engine swap that gave up adds under its stop:
    /// the programs that still hold the engine, or that none was found
    /// running from its folders (PLAN-0021 phase 2, prescription 4).
    pub held: Option<Slot>,
    pub retry: bool,
}

impl View {
    fn empty() -> Self {
        View {
            phase: PHASE_GATE,
            platform: PLATFORM,
            engine_needed: config::pins().engine_min_version.as_str(),
            blocks: Vec::new(),
            found_uv: None,
            bin_dir: None,
            notice: None,
            path_report: None,
            progress: None,
            failure: None,
            held: None,
            retry: false,
        }
    }
}

// --- discovery ---------------------------------------------------------

/// What this machine already carries, read before the gate is drawn.
///
/// The whole pass is an asking pass. It reads the file system, it asks uv
/// what it keeps, and it asks a uv for its own version. The one thing it
/// never does is run a `radialrig.exe` it found: whether that command is
/// this app's is answered by uv's own tool list and by this app's own
/// record, never by starting the program to see what it says.
#[derive(Clone, Debug, Default)]
pub struct Discovery {
    /// A uv at the one documented location.
    pub uv: Option<PathBuf>,
    /// What that uv answered a version probe with, when it answered.
    pub uv_version: Option<String>,
    /// The probe's own stop, when the binary was there and did not answer.
    pub probe_failed: Option<Slot>,
    /// The directory uv installs commands into, when uv named one.
    pub bin_dir: Option<PathBuf>,
    /// The engine's command inside that directory, when uv keeps an entry
    /// naming it — which is the only way this app calls it its own.
    pub engine: Option<PathBuf>,
    /// Whether that engine is at least the version this shell drives. An
    /// engine that answered no version is not.
    pub engine_current: bool,
    /// Whether uv keeps a tool entry for the engine.
    pub listed: bool,
    /// Whether this app's own step was part way through installing the
    /// command standing in the bin directory: its record names that path
    /// and went down no later than the command was written.
    pub intent: bool,
    /// Whether the command standing there is what this app's own step
    /// left behind: no uv entry, but this app's record beside it, or, on
    /// the swap path, the very launcher of the engine the swap began on.
    pub leftover: bool,
    /// The refusal, when a command standing there belongs to someone else.
    /// It is reached without that command ever being run.
    pub refusal: Option<Slot>,
    /// Whether the bin directory is already a segment of the user Path
    /// value, read from the registry the write reads.
    pub on_path: bool,
}

/// The user's own directory, which is where uv lands and where the engine
/// is spawned from (the engine exits when its working directory is gone).
#[cfg(windows)]
pub fn profile_dir() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE").map(PathBuf::from).filter(|p| p.is_dir())
}

#[cfg(not(windows))]
pub fn profile_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from).filter(|p| p.is_dir())
}

/// The one documented location, as an absolute path.
pub fn documented_uv() -> Option<PathBuf> {
    profile_dir().map(|home| home.join(DOCUMENTED_DIR).join(UV_EXE))
}

/// Where the archive's members land, and where uv puts commands in the
/// default case. The gate names this directory before there is a uv to
/// ask; once there is one, the Path step asks it rather than assuming.
pub fn documented_dir() -> Option<PathBuf> {
    profile_dir().map(|home| home.join(DOCUMENTED_DIR))
}

/// Whether a directory is already a segment of the user Path value.
///
/// On Windows the registry is the oracle, and it is the same one the
/// write consults: the gate and the writer have to answer alike, or the
/// gate names a step the writer skips, or worse, skips a step the writer
/// then takes. This process's own PATH is not that oracle — it was
/// inherited at launch, carries the machine value as well as the user
/// one, and cannot see a change made since.
#[cfg(windows)]
pub fn user_path_has(dir: &Path) -> bool {
    match read_user_path() {
        Ok((_, value)) => segment_present(&value, &dir.to_string_lossy()),
        Err(_) => false,
    }
}

/// On Linux there is no registry and no writer: the step is a report,
/// and the one PATH this process can read honestly is its own inherited
/// one. That is a bounded answer — a login shell may carry more — and
/// the report's sentence claims no more than what was read.
#[cfg(not(windows))]
pub fn user_path_has(dir: &Path) -> bool {
    match std::env::var("PATH") {
        Ok(value) => path_env_has(&value, &dir.to_string_lossy()),
        Err(_) => false,
    }
}

/// Whether a directory is a segment of a POSIX PATH value: an exact,
/// case-sensitive match with at most a trailing separator forgiven. No
/// quoting and no variable expansion, because a POSIX PATH value carries
/// neither.
#[cfg_attr(windows, allow(dead_code))]
pub fn path_env_has(value: &str, dir: &str) -> bool {
    let wanted = dir.trim_end_matches('/');
    if wanted.is_empty() {
        return false;
    }
    value
        .split(':')
        .map(|part| part.trim_end_matches('/'))
        .any(|part| !part.is_empty() && part == wanted)
}

/// What the PATH read found, said as the one sentence the report step
/// puts on screen (PLAN-0009 phase 3 prescription 3).
#[cfg_attr(windows, allow(dead_code))]
pub fn path_report_of(dir: &Path, on_path: bool) -> Slot {
    let key = if on_path {
        strings::PATH_REPORT_ON
    } else {
        strings::PATH_REPORT_OFF
    };
    Slot::of(key, dir.to_string_lossy())
}

/// `uv --version`, which is the first thing a found binary is ever asked.
/// No install command is ever the first thing a found binary runs.
pub fn probe_uv(uv: &Path) -> Result<String, Slot> {
    let said = run(uv, &["--version"])
        .map_err(|why| Slot::of(strings::FAIL_UV_PROBE, why))?;
    match parse_named_version("uv", &said) {
        Some(version) => Ok(version),
        None => Err(Slot::of(strings::FAIL_UV_PROBE, first_line(&said))),
    }
}

/// The directory uv installs commands into, by asking uv.
pub fn tool_bin_dir(uv: &Path) -> Result<PathBuf, Slot> {
    named_dir(run(uv, &["tool", "dir", "--bin"]))
}

/// The directory uv keeps each tool's own environment in, by asking uv
/// beside the question above. The engine's environment is the
/// `radialrig` folder inside it, and that folder, with the launchers in
/// the directory above, is where the programs that hold the engine run
/// from (PLAN-0021 phase 2, prescription 3). The engine swap asks it once,
/// as it begins.
pub fn tool_dir(uv: &Path) -> Result<PathBuf, Slot> {
    named_dir(run(uv, &["tool", "dir"]))
}

/// One directory uv was asked for, read from its answer. Both asks stop
/// the same way: a uv that failed, said nothing, or named a directory
/// that is not absolute is a uv that did not name one.
fn named_dir(said: Result<String, String>) -> Result<PathBuf, Slot> {
    let said = said.map_err(|why| Slot::of(strings::FAIL_TOOL_DIR, why))?;
    let named = first_line(&said);
    if named.is_empty() {
        return Err(Slot::bare(strings::FAIL_TOOL_DIR));
    }
    let dir = PathBuf::from(named);
    if !dir.is_absolute() {
        return Err(Slot::of(strings::FAIL_TOOL_DIR, dir.to_string_lossy()));
    }
    Ok(dir)
}

/// One command, run to completion, its output read as text.
///
/// On Windows the child joins D9's Job object before it is waited on,
/// so a shell that dies mid-install leaves no installer running behind
/// it — which is why this spawns and waits rather than calling
/// `output`, which gives back no child to put in the job. Linux has no
/// app-lifetime job: a shell that dies mid-install leaves the installer
/// to finish or strand on its own, and the install-intent record is
/// what lets the next launch tell that leaving apart and recover from
/// it (X2).
pub fn run(exe: &Path, args: &[&str]) -> Result<String, String> {
    let mut command = Command::new(exe);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let child = attach::quiet(&mut command)
        .spawn()
        .map_err(|why| why.to_string())?;
    attach::join_job(&child);
    let out = child.wait_with_output().map_err(|why| why.to_string())?;
    if !out.status.success() {
        let tail = tail_of(&String::from_utf8_lossy(&out.stderr));
        return Err(match out.status.code() {
            Some(code) if tail.is_empty() => format!("{code}"),
            Some(code) => format!("{code}: {tail}"),
            None => tail,
        });
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

/// The first line of some output, with the line endings taken off.
pub fn first_line(said: &str) -> String {
    said.lines().next().unwrap_or("").trim().to_string()
}

/// The last of some output, bounded, for a sentence's one slot.
pub fn tail_of(said: &str) -> String {
    let lines: Vec<&str> = said.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    let from = lines.len().saturating_sub(3);
    lines[from..].join(" ").chars().take(400).collect()
}

/// `<name> <version>`, which is how both programs answer a version ask.
pub fn parse_named_version(name: &str, said: &str) -> Option<String> {
    let line = said.lines().find(|line| !line.trim().is_empty())?;
    let rest = line.trim().strip_prefix(name)?.strip_prefix(' ')?;
    let version = rest.split_whitespace().next()?;
    Some(version.to_string())
}

/// Who the `radialrig.exe` standing in the documented directory belongs
/// to, decided without ever running it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Owner {
    /// Nothing is standing there.
    Absent,
    /// uv keeps an entry naming it, so it arrived the one way this app
    /// installs anything.
    Ours,
    /// No uv entry, but this app's own record says its install step was
    /// running, or a running swap knows the file as its old engine's own
    /// launcher. The file is what this app's own step left behind.
    Leftover,
    /// No uv entry and no record of this app's own step. It came from
    /// somewhere else, and this app neither runs it nor writes over it.
    Foreign,
}

/// The classification, as one answerable question over three facts. The
/// third is whether this app's own step accounts for the file: its
/// install record, or a running swap's knowledge of its old engine.
pub fn owner(present: bool, listed: bool, intent: bool) -> Owner {
    if !present {
        return Owner::Absent;
    }
    if listed {
        return Owner::Ours;
    }
    if intent {
        return Owner::Leftover;
    }
    Owner::Foreign
}

/// The whole pass, in the order X1 pins: the location, then the version
/// probe of a uv, then what uv itself keeps, and only then the engine.
///
/// The order is the point. A `radialrig.exe` standing in the documented
/// directory is never started to find out what it is; uv's own tool list
/// answers that, and this app's own install record answers the one case
/// uv cannot. A command uv does not name and this app did not leave is
/// refused where it stands, before the archive is asked for, before the
/// Path is touched, before any step runs at all.
pub fn discover(pins: &config::Pins, data: Option<&Path>) -> Discovery {
    discover_with(pins, data, documented_uv().filter(|uv| uv.is_file()), None)
}

/// The same pass, given the uv to ask and, on the swap path, the old
/// engine's launchers as they stood when the swap began.
///
/// The swap's knowledge is the one thing that lets a retry read its own
/// remains as its own (PLAN-0021 phase 2, prescription 4). A failed
/// uninstall can take uv's entry and leave the old launcher in place, and
/// that launcher was written long before any install record this app
/// keeps, so the record cannot speak for it. The swap can: a launcher at
/// the path it read, still byte for byte what it read there, is the
/// engine it began replacing. Anything else standing there, a launcher
/// the app never installed included, is refused as before.
pub fn discover_with(
    pins: &config::Pins,
    data: Option<&Path>,
    uv: Option<PathBuf>,
    remains: Option<&Remains>,
) -> Discovery {
    let mut found = Discovery::default();

    found.uv = uv.clone();

    // Whether uv's answer about what it keeps could be read at all. With
    // no uv there is no entry either, which is a read answer and not an
    // unread one: nothing installed the command that may be standing
    // there, so nothing this app owns did.
    let mut unread = false;
    if let Some(uv) = &uv {
        match probe_uv(uv) {
            Ok(version) => found.uv_version = Some(version),
            Err(stop) => {
                found.probe_failed = Some(stop);
                return found;
            }
        }
        match tool_bin_dir(uv) {
            Ok(dir) => found.bin_dir = Some(dir),
            Err(stop) => {
                found.refusal = Some(stop);
                return found;
            }
        }
        match run(uv, &["tool", "list"]) {
            Ok(said) => found.listed = lists_tool(&said, ENGINE_TOOL),
            Err(why) => {
                unread = true;
                found.refusal = Some(Slot::of(strings::FAIL_ENGINE_INSTALL, why));
            }
        }
    }

    let dir = match found.bin_dir.clone().or_else(documented_dir) {
        Some(dir) => dir,
        None => return found,
    };
    found.on_path = user_path_has(&dir);
    let exe = dir.join(ENGINE_EXE);
    let present = exe.is_file();
    // The record is read against the one path it could speak for, so a
    // record left beside another directory, or from before the command
    // standing here was written, is no record at all.
    found.intent = intent_present(data, &exe);
    if found.bin_dir.is_none() {
        found.bin_dir = Some(dir);
    }

    if unread {
        // A uv that cannot say what it keeps is not evidence that the
        // command standing there belongs to a stranger. The stop already
        // held is the one that actually happened, and it takes the retry
        // control rather than the refusal no second press can move.
        if !present {
            found.refusal = None;
        }
        return found;
    }

    let remains = remains.is_some_and(|remains| remains.claims(&exe));
    match owner(present, found.listed, found.intent || remains) {
        Owner::Absent => {}
        Owner::Ours => {
            // The one call ever made to this command before the shell
            // drives it, and it is made only because uv's entry names it.
            found.engine_current = attach::engine_version(&exe)
                .ok()
                .as_deref()
                .and_then(|said| attach::at_least(said, &pins.engine_min_version))
                .unwrap_or(false);
            found.engine = Some(exe);
        }
        Owner::Leftover => found.leftover = true,
        Owner::Foreign => {
            found.refusal =
                Some(Slot::of(strings::FAIL_FOREIGN_EXE, exe.to_string_lossy()));
        }
    }
    found
}

/// The gate's block set: one block per step that will actually run.
///
/// Nothing is named that will not run, and nothing runs that was not
/// named, which is the whole of R6. An engine uv keeps an entry for and
/// that answers an old version is not named here: the opening look sends
/// it to the engine swap, which replaces it with no press (PLAN-0021
/// Decided 7), so the gate never names a step that takes anything out.
pub fn plan(found: &Discovery) -> Vec<&'static str> {
    let mut blocks = Vec::new();
    if found.uv.is_none() {
        blocks.push(strings::GATE_UV);
    }
    if found.engine.is_none() {
        blocks.push(strings::GATE_PYTHON);
        blocks.push(strings::GATE_ENGINE);
    }
    // The Path write exists on Windows alone. On Linux the step is a
    // report and writes nothing, so there is no machine change for the
    // gate to name (PLAN-0009 phase 3 prescription 3).
    #[cfg(windows)]
    if !blocks.is_empty() && !found.on_path {
        blocks.push(strings::GATE_PATH);
    }
    blocks
}

/// D6's stop for an engine older than this shell drives, naming the
/// version the engine answered with. An engine that answered none gets
/// the sentence that says so. The version this app needs is not the
/// stop's to carry: it rides on every picture (BUG-0017).
///
/// The opening look no longer reaches this stop: it replaces an old
/// engine instead. What still reaches it is the version check after an
/// install, when the engine just installed is itself too old, and its
/// press runs the engine swap.
pub fn engine_old(answered: Option<&str>) -> Slot {
    match answered.map(str::trim) {
        Some(version) if !version.is_empty() => Slot::of(strings::FAIL_ENGINE_OLD, version),
        _ => Slot::bare(strings::FAIL_ENGINE_UNREAD),
    }
}

/// Whether the opening look found an engine uv keeps an entry for that
/// is not current, which is the engine the swap replaces.
pub fn stale_engine(found: &Discovery) -> bool {
    found.engine.is_some() && !found.engine_current
}

/// Where the opening look sends the window.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Opening {
    /// One of the two stops discovery itself can reach, before a single
    /// byte on this machine has changed.
    Stop(Slot),
    /// An engine older than this app needs: replaced with no press.
    Swap,
    /// Nothing for the gate to name: the engine is started.
    Run,
    /// Steps to name, and the press that agrees to them.
    Gate,
}

/// The opening look's one decision, apart from the window it draws on.
///
/// An old engine leads to the swap, not to a stop (PLAN-0021 phase 2,
/// prescription 4). Replacing it needs no press, as Decided 7 rules; the
/// screen says what the swap does while it runs. The other first-run
/// steps keep the consent they had: an old engine is only ever found
/// where uv already keeps one, so the gate would name nothing there.
pub fn opening(found: &Discovery, blocks: &[&'static str]) -> Opening {
    if let Some(stop) = found.probe_failed.clone().or_else(|| found.refusal.clone()) {
        return Opening::Stop(stop);
    }
    if stale_engine(found) {
        return Opening::Swap;
    }
    if blocks.is_empty() {
        return Opening::Run;
    }
    Opening::Gate
}

// --- D1: the archive, its digest, and its two members ------------------

/// Which of the two members a zip entry is, if it is one of them.
///
/// The name is matched whole, with no directory part allowed in front of
/// it: the pinned Windows archive keeps its programs at the top, and a
/// name carrying a separator is the one shape that could write outside
/// the directory the extraction was aimed at. `uvw.exe` is not one of
/// the two and never matches, which is the point of naming them rather
/// than taking everything the archive happens to hold.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn zip_member_target(name: &str) -> Option<&'static str> {
    if name.contains('/') || name.contains('\\') {
        return None;
    }
    let folded = name.trim().to_ascii_lowercase();
    ZIP_MEMBERS.iter().copied().find(|member| *member == folded)
}

/// Which of the two members a tar entry is, if it is one of them.
///
/// The pinned Linux archive nests its programs under one directory, so
/// the whole nested name is matched, exactly and case-sensitively — the
/// file systems this reader writes to fold nothing. Only the two exact
/// names are ever written, so no entry name can steer a write anywhere
/// else.
#[cfg_attr(windows, allow(dead_code))]
pub fn tar_member_target(name: &str) -> Option<&'static str> {
    for member in TAR_MEMBERS {
        if name == format!("{TAR_DIR}/{member}") {
            return Some(member);
        }
    }
    None
}

/// The sha256 of some bytes, written the way the pin writes it.
pub fn digest(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// The bytes against the pin. A mismatch is the one stop a second press
/// cannot move: the same request answers with the same bytes.
pub fn verify(bytes: &[u8], pinned: &str) -> Result<(), Slot> {
    if digest(bytes).eq_ignore_ascii_case(pinned.trim()) {
        return Ok(());
    }
    Err(Slot::bare(strings::FAIL_CHECKSUM))
}

/// The pinned archive, from the first host whose bytes match the pin.
///
/// The digest is inside the loop, not after it. A host that answers 200
/// with bytes that are not the pinned archive has not delivered the
/// archive, so the next pinned host is asked — the same fall-through a
/// refused connection gets. Only when every pinned host answered and
/// every answer mismatched is this the deterministic stop, because only
/// then is a second press asking the same hosts for the same bytes.
pub fn from_hosts<F>(urls: &[String], pinned: &str, get: F) -> Result<Vec<u8>, Slot>
where
    F: Fn(&str) -> Result<Vec<u8>, String>,
{
    let mut answered = 0usize;
    let mut last = String::new();
    for url in urls {
        match get(url) {
            Ok(bytes) => {
                answered += 1;
                if digest(&bytes).eq_ignore_ascii_case(pinned.trim()) {
                    return Ok(bytes);
                }
            }
            Err(why) => last = why,
        }
    }
    if answered == urls.len() && answered > 0 {
        // Every host answered, and no answer was the pinned archive.
        return Err(Slot::bare(strings::FAIL_CHECKSUM));
    }
    Err(Slot::of(strings::FAIL_DOWNLOAD, last))
}

/// The pinned archive, over the wire, proved against the pin as each host
/// answers.
///
/// https and nothing else, on the first request and on every redirect
/// after it. The bytes are answered into memory and stay there: the
/// digest is taken over this buffer and the members are read out of the
/// same one, so there is no moment where a file on disk could be swapped
/// between the proving and the reading.
pub fn download(pins: &config::Pins) -> Result<Vec<u8>, Slot> {
    let client = https_client().map_err(|why| Slot::of(strings::FAIL_DOWNLOAD, why))?;

    from_hosts(&pins.archive_urls(), pins.archive_sha256(), |url| {
        fetch(&client, url)
    })
}

/// The one client every download this app makes goes through: https and
/// nothing else, on the first request and on every redirect after it,
/// with the same bound on hops and the same bound on time.
///
/// One builder rather than two, because the wheel is downloaded on the
/// same terms as the archive. A second builder beside it would be a
/// second place for the scheme rule to be relaxed in.
fn https_client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .https_only(true)
        .redirect(reqwest::redirect::Policy::custom(|attempt| {
            // A hop that leaves https is not followed, and neither is one
            // past the bound: a policy of its own replaces the built-in
            // limit, so the loop has to be closed here or not at all. Not
            // following leaves the 3xx as the answer, which is not a
            // success, which is a download that did not happen.
            if attempt.url().scheme() == HTTPS_SCHEME && attempt.previous().len() < ARCHIVE_HOPS {
                attempt.follow()
            } else {
                attempt.stop()
            }
        }))
        .timeout(ARCHIVE_WAIT)
        .build()
        .map_err(|why| why.to_string())
}

fn fetch(client: &reqwest::blocking::Client, url: &str) -> Result<Vec<u8>, String> {
    let answer = client.get(url).send().map_err(|why| why.to_string())?;
    let status = answer.status();
    if !status.is_success() {
        return Err(status.as_u16().to_string());
    }
    // Bounded, because the answer is held in memory by design: the digest
    // is taken over this buffer and the members are read out of it. The
    // pinned archive is a small fraction of this, and anything that runs
    // past it would fail the digest anyway — the bound is about what an
    // unanswerable host could make this process hold, not about bytes.
    let mut held = Vec::new();
    answer
        .take(ARCHIVE_CAP)
        .read_to_end(&mut held)
        .map_err(|why| why.to_string())?;
    if held.len() as u64 >= ARCHIVE_CAP {
        return Err(held.len().to_string());
    }
    Ok(held)
}

/// The platform's own reader over the buffer that was just proved: the
/// zip on Windows, the tar.gz on Linux. Both land the same two programs
/// in the one documented directory and hand back the uv that landed.
pub fn extract(bytes: &[u8], into: &Path) -> Result<PathBuf, Slot> {
    #[cfg(windows)]
    {
        extract_zip(bytes, into)
    }
    #[cfg(not(windows))]
    {
        extract_tar(bytes, into)
    }
}

/// The two members, out of the buffer that was just proved, into the one
/// documented directory. The path of the uv that landed comes back.
///
/// A member whose name is already a file there is left exactly as it is.
/// Discovery proved there was no `uv.exe`, so that only ever happens to
/// `uvx.exe`, and a `uvx.exe` this app did not write is not a byte this
/// app writes over (constraint 5).
#[cfg_attr(not(windows), allow(dead_code))]
pub fn extract_zip(bytes: &[u8], into: &Path) -> Result<PathBuf, Slot> {
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(bytes))
        .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;
    std::fs::create_dir_all(into)
        .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;

    let mut seen: Vec<&'static str> = Vec::new();
    for index in 0..archive.len() {
        let named = match archive.name_for_index(index) {
            Some(named) => named.to_string(),
            None => continue,
        };
        let target = match zip_member_target(&named) {
            Some(target) => target,
            None => continue,
        };
        if !seen.contains(&target) {
            seen.push(target);
        }
        let at = into.join(target);
        if at.exists() {
            continue;
        }
        let mut member = archive
            .by_index(index)
            .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;
        let mut held = Vec::new();
        member
            .read_to_end(&mut held)
            .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;
        write_new(&at, &held)?;
    }

    for member in ZIP_MEMBERS {
        if !seen.contains(&member) {
            return Err(Slot::of(strings::FAIL_EXTRACT, member));
        }
    }
    let uv = into.join(ZIP_UV);
    if !uv.is_file() {
        return Err(Slot::of(strings::FAIL_EXTRACT, uv.to_string_lossy()));
    }
    Ok(uv)
}

/// The tar.gz reader, beside the zip reader and held to the same rules:
/// the two named members and nothing else, only ever created and never
/// replaced, read out of the proved buffer. The one thing this reader
/// does that the zip one does not is set the execute bit, because a
/// program without it is not a program on the platform this archive is
/// for.
#[cfg_attr(windows, allow(dead_code))]
pub fn extract_tar(bytes: &[u8], into: &Path) -> Result<PathBuf, Slot> {
    let decoder = flate2::read::GzDecoder::new(std::io::Cursor::new(bytes));
    let mut archive = tar::Archive::new(decoder);
    std::fs::create_dir_all(into)
        .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;

    let mut seen: Vec<&'static str> = Vec::new();
    let entries = archive
        .entries()
        .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;
    for entry in entries {
        let mut entry = entry.map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;
        let named = match entry.path() {
            Ok(path) => path.to_string_lossy().to_string(),
            Err(_) => continue,
        };
        let target = match tar_member_target(&named) {
            Some(target) => target,
            None => continue,
        };
        if !seen.contains(&target) {
            seen.push(target);
        }
        let at = into.join(target);
        if at.exists() {
            continue;
        }
        let mut held = Vec::new();
        entry
            .read_to_end(&mut held)
            .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;
        write_new(&at, &held)?;
        set_executable(&at)?;
    }

    for member in TAR_MEMBERS {
        if !seen.contains(&member) {
            return Err(Slot::of(strings::FAIL_EXTRACT, member));
        }
    }
    let uv = into.join(TAR_UV);
    if !uv.is_file() {
        return Err(Slot::of(strings::FAIL_EXTRACT, uv.to_string_lossy()));
    }
    Ok(uv)
}

/// The execute bit, on the platform that has one. The mode is the one
/// the pinned archive itself records for both programs.
#[cfg(unix)]
fn set_executable(at: &Path) -> Result<(), Slot> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(at, std::fs::Permissions::from_mode(0o755))
        .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))
}

#[cfg(not(unix))]
#[allow(dead_code)]
fn set_executable(_at: &Path) -> Result<(), Slot> {
    Ok(())
}

/// One file that was not there a moment ago, or a stop.
///
/// Created rather than opened: a file that already exists is a file this
/// app did not write, and the create refuses instead of replacing it. A
/// write that fails part way through takes its own leavings with it,
/// which is a file this app did write and so is its to remove.
fn write_new(at: &Path, bytes: &[u8]) -> Result<(), Slot> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(at)
        .map_err(|why| Slot::of(strings::FAIL_EXTRACT, why.to_string()))?;
    if let Err(why) = file.write_all(bytes).and_then(|()| file.flush()) {
        drop(file);
        let _ = std::fs::remove_file(at);
        return Err(Slot::of(strings::FAIL_EXTRACT, why.to_string()));
    }
    Ok(())
}

/// A uv this shell may call, whether it was already there or was put
/// there by the steps the gate named.
pub fn provide_uv(
    found: &Discovery,
    pins: &config::Pins,
    step: &dyn Fn(&'static str),
) -> Result<PathBuf, Slot> {
    if let Some(uv) = &found.uv {
        return Ok(uv.clone());
    }
    let into = documented_dir().ok_or_else(|| Slot::bare(strings::FAIL_EXTRACT))?;
    step(strings::PROGRESS_DOWNLOAD);
    // The digest is taken inside the download, once per host that
    // answers, so a first host with the wrong bytes falls through to the
    // second rather than ending the run on a stop the second could have
    // moved. What comes back here has already been proved against the pin.
    let bytes = download(pins)?;
    step(strings::PROGRESS_VERIFY);
    verify(&bytes, pins.archive_sha256())?;
    step(strings::PROGRESS_EXTRACT);
    // No version probe on this one: its bytes were proved against the pin
    // this app carries, which is a stronger answer than the question a
    // probe asks, and the probe's own sentence is about a uv that was
    // already here.
    extract(&bytes, &into)
}

// --- D2: the one Path entry --------------------------------------------

/// What this app put on the user's Path, kept so the removal flow can
/// tell its own entry from someone else's.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct PathRecord {
    /// The registry type the value was stored under, preserved across the
    /// write so an expandable value stays expandable.
    pub kind: u32,
    /// The one segment this app put in front.
    pub segment: String,
    /// The exact full value written, byte for byte.
    pub value: String,
}

/// The record as it is kept on disk.
pub fn record_text(record: &PathRecord) -> String {
    serde_json::to_string_pretty(record).unwrap_or_default()
}

/// The record as it is read back. Anything that is not one is nothing.
///
/// The reader of this is the removal flow, which is prescription 4's
/// unit. It is written and proved here because the writer and the reader
/// have to agree on one shape, and the shape is settled by whichever of
/// them lands first.
pub fn parse_record(text: &str) -> Option<PathRecord> {
    serde_json::from_str(text).ok()
}

/// The segments of a Path value, in order, with the empty ones dropped.
pub fn segments(value: &str) -> Vec<&str> {
    value
        .split(';')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .collect()
}

/// A value's environment references resolved, for comparing only.
///
/// The registry holds `%USERPROFILE%\.local\bin` as those characters, and
/// the directory uv names is the resolved one. Comparing the two as
/// written says they are different places and puts a second copy of the
/// same directory on the Path every run. So each existing segment is
/// resolved here, and here only: what gets written back is the value as
/// the user wrote it, references and registry type intact (D2).
#[cfg(windows)]
pub fn expanded(said: &str) -> String {
    use windows::Win32::System::Environment::ExpandEnvironmentStringsW;

    if !said.contains('%') {
        return said.to_string();
    }
    let mut source: Vec<u16> = said.encode_utf16().collect();
    source.push(0);
    let wanted = unsafe {
        ExpandEnvironmentStringsW(windows::core::PCWSTR(source.as_ptr()), None)
    };
    if wanted == 0 {
        return said.to_string();
    }
    let mut held = vec![0u16; wanted as usize];
    let written = unsafe {
        ExpandEnvironmentStringsW(
            windows::core::PCWSTR(source.as_ptr()),
            Some(held.as_mut_slice()),
        )
    };
    if written == 0 || written as usize > held.len() {
        return said.to_string();
    }
    let end = held.iter().position(|unit| *unit == 0).unwrap_or(held.len());
    String::from_utf16_lossy(&held[..end])
}

#[cfg(not(windows))]
pub fn expanded(said: &str) -> String {
    said.to_string()
}

/// Whether a directory is already one of a Path value's segments.
///
/// Case-insensitively, with a trailing separator and surrounding quotes
/// ignored, and with each existing segment resolved before it is
/// compared. Decided that way because Windows resolves paths that way:
/// `C:\a`, `c:\a\`, `"C:\A"` and `%SOMEWHERE%\a` name one directory, and
/// treating them as four would put a second copy of the same entry on the
/// Path every time this ran. A bare drive keeps its separator, since `C:`
/// and `C:\` are two different places.
pub fn segment_present(value: &str, dir: &str) -> bool {
    let wanted = folded(&expanded(dir));
    !wanted.is_empty()
        && segments(value)
            .into_iter()
            .any(|part| folded(&expanded(part)) == wanted)
}

fn folded(said: &str) -> String {
    let bare = said.trim().trim_matches('"').trim();
    let cut = bare.trim_end_matches(['\\', '/']);
    let kept = if cut.len() < 3 { bare } else { cut };
    kept.to_ascii_lowercase()
}

/// The value with the directory in front of it, and the rest of it left
/// exactly as it was — expandable references and all.
pub fn prepended(value: &str, dir: &str) -> String {
    if value.is_empty() {
        return dir.to_string();
    }
    format!("{dir};{value}")
}

/// What one read of the Path value asks the writer to do: nothing when
/// the directory is already a segment, or the exact record of the write.
///
/// The registry type comes through untouched, so a value stored as
/// expandable stays expandable and a plain one stays plain.
pub fn path_change(kind: u32, value: &str, dir: &str) -> Option<PathRecord> {
    if segment_present(value, dir) {
        return None;
    }
    Some(PathRecord {
        kind,
        segment: dir.to_string(),
        value: prepended(value, dir),
    })
}

/// D2's step: read unexpanded, decide, re-read, record, write, announce.
/// Windows only, because it is the one platform where the step writes:
/// on Linux the step is a report and this function does not exist
/// (PLAN-0009 phase 3 prescription 3).
#[cfg(windows)]
pub fn provide_path(
    uv: &Path,
    data: Option<&Path>,
    step: &dyn Fn(&'static str),
) -> Result<(), Slot> {
    step(strings::PROGRESS_PATH);
    let dir = tool_bin_dir(uv)?;
    let dir = dir.to_string_lossy().to_string();

    let (kind, value) = read_user_path()?;
    if path_change(kind, &value, &dir).is_none() {
        return Ok(());
    }
    // Read again immediately before the write, so what is written is
    // built on the value that is there now rather than the one that was
    // there when the gate was drawn. The residual race with an installer
    // writing between this read and the write is named in D2 as accepted;
    // the record below is what bounds it to a refusal later, never a
    // silent overwrite.
    let (kind, value) = read_user_path()?;
    let record = match path_change(kind, &value, &dir) {
        Some(record) => record,
        None => return Ok(()),
    };
    let written = record.value.clone();
    // The record goes down first. A run that stops between the two leaves
    // a record of a value that was never written, and the removal flow
    // refuses on the mismatch; the other order would leave an entry this
    // app added with nothing saying so, which no removal could ever take
    // back out.
    keep_record(data, &record)?;
    write_user_path(kind, &written)?;
    announce();
    Ok(())
}

#[cfg(windows)]
fn keep_record(data: Option<&Path>, record: &PathRecord) -> Result<(), Slot> {
    let data = data.ok_or_else(|| Slot::bare(strings::FAIL_PATH_WRITE))?;
    std::fs::create_dir_all(data).map_err(|_| Slot::bare(strings::FAIL_PATH_WRITE))?;
    std::fs::write(data.join(PATH_RECORD), record_text(record))
        .map_err(|_| Slot::bare(strings::FAIL_PATH_WRITE))
}

/// A Path value as the registry holds it: sixteen-bit units, ended by a
/// zero. Read unexpanded, so `%USERPROFILE%` in a user's own value stays
/// the six-and-a-half characters they typed.
#[cfg(windows)]
fn decode(bytes: &[u8]) -> String {
    let units: Vec<u16> = bytes
        .chunks_exact(2)
        .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
        .collect();
    let end = units.iter().position(|unit| *unit == 0).unwrap_or(units.len());
    String::from_utf16_lossy(&units[..end])
}

#[cfg(windows)]
fn encode(said: &str) -> Vec<u8> {
    let mut units: Vec<u16> = said.encode_utf16().collect();
    units.push(0);
    units.iter().flat_map(|unit| unit.to_le_bytes()).collect()
}

/// The user's Path value and the registry type it is stored under, read
/// without expanding anything. A value that is not there at all is an
/// empty one that will be created expandable.
#[cfg(windows)]
pub(crate) fn read_user_path() -> Result<(u32, String), Slot> {
    use windows::core::w;
    use windows::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows::Win32::System::Registry::{
        RegGetValueW, HKEY_CURRENT_USER, REG_EXPAND_SZ, REG_VALUE_TYPE, RRF_NOEXPAND,
        RRF_RT_REG_EXPAND_SZ, RRF_RT_REG_SZ,
    };

    let flags = RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | RRF_NOEXPAND;
    let mut kind = REG_VALUE_TYPE::default();
    let mut size: u32 = 0;
    let asked = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            w!("Environment"),
            w!("Path"),
            flags,
            Some(&mut kind),
            None,
            Some(&mut size),
        )
    };
    if asked == ERROR_FILE_NOT_FOUND {
        return Ok((REG_EXPAND_SZ.0, String::new()));
    }
    if asked != ERROR_SUCCESS {
        return Err(Slot::bare(strings::FAIL_PATH_WRITE));
    }
    let mut held = vec![0u8; size as usize];
    let asked = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            w!("Environment"),
            w!("Path"),
            flags,
            Some(&mut kind),
            Some(held.as_mut_ptr().cast()),
            Some(&mut size),
        )
    };
    if asked != ERROR_SUCCESS {
        return Err(Slot::bare(strings::FAIL_PATH_WRITE));
    }
    held.truncate(size as usize);
    Ok((kind.0, decode(&held)))
}

/// The new value, under the type the old one carried.
#[cfg(windows)]
pub(crate) fn write_user_path(kind: u32, value: &str) -> Result<(), Slot> {
    use windows::core::w;
    use windows::Win32::Foundation::ERROR_SUCCESS;
    use windows::Win32::System::Registry::{
        RegCloseKey, RegOpenKeyExW, RegSetValueExW, HKEY, HKEY_CURRENT_USER, KEY_SET_VALUE,
        REG_VALUE_TYPE,
    };

    let mut key = HKEY::default();
    let opened = unsafe {
        RegOpenKeyExW(
            HKEY_CURRENT_USER,
            w!("Environment"),
            None,
            KEY_SET_VALUE,
            &mut key,
        )
    };
    if opened != ERROR_SUCCESS {
        return Err(Slot::bare(strings::FAIL_PATH_WRITE));
    }
    let bytes = encode(value);
    let set =
        unsafe { RegSetValueExW(key, w!("Path"), None, REG_VALUE_TYPE(kind), Some(&bytes)) };
    unsafe {
        let _ = RegCloseKey(key);
    }
    if set != ERROR_SUCCESS {
        return Err(Slot::bare(strings::FAIL_PATH_WRITE));
    }
    Ok(())
}

/// Tell the desktop the environment changed, so a terminal opened after
/// this reads the new value rather than the one it started with.
#[cfg(windows)]
pub(crate) fn announce() {
    use windows::core::w;
    use windows::Win32::Foundation::{LPARAM, WPARAM};
    use windows::Win32::UI::WindowsAndMessaging::{
        SendMessageTimeoutW, HWND_BROADCAST, SMTO_ABORTIFHUNG, WM_SETTINGCHANGE,
    };

    unsafe {
        SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            WPARAM(0),
            LPARAM(w!("Environment").as_ptr() as isize),
            SMTO_ABORTIFHUNG,
            5000,
            None,
        );
    }
}

#[cfg(not(windows))]
pub(crate) fn read_user_path() -> Result<(u32, String), Slot> {
    Err(Slot::bare(strings::FAIL_PATH_WRITE))
}

#[cfg(not(windows))]
pub(crate) fn write_user_path(_kind: u32, _value: &str) -> Result<(), Slot> {
    Err(Slot::bare(strings::FAIL_PATH_WRITE))
}

#[cfg(not(windows))]
pub(crate) fn announce() {}

// --- X2: the install-intent record -------------------------------------

/// What this app was part way through doing, kept in its own data
/// directory for exactly as long as the doing takes.
///
/// The reading this record carries, recorded: a `radialrig.exe` with no
/// uv entry behind it is refused, and that refusal is what constraint 5
/// asks for. But this app's own install step can leave exactly that state
/// behind — uv writes the command before it writes its entry, and a close
/// or a crash in between strands the file. Without a record the app's own
/// leftover is indistinguishable from a stranger's file, so the app would
/// refuse itself for good. The record makes the two distinguishable, and
/// only the one this app's own step created is ever removed.
///
/// A record that outlives the install it stood for must not speak for
/// whatever stands at the path later, which is why it carries the path
/// and the moment it went down: the install writes its command after the
/// record, so a command at another path, or one written before the
/// record, is not the one the record stands for.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct IntentRecord {
    /// The package address the install was running against.
    pub source: String,
    /// The command the install was going to put in place.
    pub exe: String,
    /// When the record went down, in whole seconds since the Unix epoch,
    /// on the same clock the file system stamps the command with.
    pub written: u64,
}

/// Now, as the record keeps it. A clock that cannot answer reads as the
/// end of time, so nothing written before it is ever taken for this app's
/// own: the refusal is the fallback, never the removal.
pub fn intent_clock() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|since| since.as_secs())
        .unwrap_or(u64::MAX)
}

/// When the file at `exe` was last written, on the record's clock.
/// Nothing, when there is no file there or its time cannot be read.
fn command_written(exe: &Path) -> Option<u64> {
    std::fs::metadata(exe)
        .ok()?
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()
        .map(|since| since.as_secs())
}

/// The record as it is kept on disk.
pub fn intent_text(record: &IntentRecord) -> String {
    serde_json::to_string_pretty(record).unwrap_or_default()
}

/// The record as it is read back. Anything that is not one is nothing.
pub fn parse_intent(text: &str) -> Option<IntentRecord> {
    serde_json::from_str(text).ok()
}

fn intent_at(data: Option<&Path>) -> Option<PathBuf> {
    data.map(|data| data.join(INTENT_RECORD))
}

/// Whether this app's own install step was writing the command at `exe`
/// when it last stopped.
///
/// Three things have to hold, and each one failing reads as no record.
/// The record parses, which a record from before it carried a time does
/// not. It names this very path, so a record left beside one directory
/// never speaks for a command in another. And the command standing there,
/// when one is, was written no earlier than the record went down, since
/// the install writes its command after the record: one written before it
/// was put there by something else, and the refusal that stands in front
/// of every foreign command is what fires. With nothing standing there the
/// record stands for the step alone, and there is nothing to remove.
pub fn intent_present(data: Option<&Path>, exe: &Path) -> bool {
    let record = intent_at(data)
        .and_then(|at| std::fs::read_to_string(at).ok())
        .and_then(|text| parse_intent(&text));
    let Some(record) = record else {
        return false;
    };
    if Path::new(&record.exe) != exe {
        return false;
    }
    match std::fs::metadata(exe) {
        Err(why) => why.kind() == std::io::ErrorKind::NotFound,
        Ok(_) => command_written(exe).is_some_and(|at| at >= record.written),
    }
}

/// The record down, before the install command runs.
pub fn keep_intent(data: Option<&Path>, record: &IntentRecord) -> Result<(), Slot> {
    let data = data.ok_or_else(|| Slot::bare(strings::FAIL_ENGINE_INSTALL))?;
    std::fs::create_dir_all(data)
        .map_err(|why| Slot::of(strings::FAIL_ENGINE_INSTALL, why.to_string()))?;
    std::fs::write(data.join(INTENT_RECORD), intent_text(record))
        .map_err(|why| Slot::of(strings::FAIL_ENGINE_INSTALL, why.to_string()))
}

/// The record away, once the install has answered.
pub fn clear_intent(data: Option<&Path>) {
    if let Some(at) = intent_at(data) {
        let _ = std::fs::remove_file(at);
    }
}

// --- D4: the wheel, its digest, and the install ------------------------

/// The wheel's own file name, off the end of the pinned address.
///
/// This name becomes a file this app writes, so only a last segment
/// that is a plain wheel name is taken: one carrying a separator, a
/// drive letter or a parent reference is refused rather than joined.
/// The pinned address is validated and compiled in, so this is a second
/// lock on a door that is already shut, which is where it belongs.
pub fn wheel_name(address: &str) -> Option<String> {
    let last = address.rsplit('/').next().unwrap_or_default();
    if !last.to_ascii_lowercase().ends_with(WHEEL_SUFFIX) {
        return None;
    }
    if last.len() <= WHEEL_SUFFIX.len() {
        return None;
    }
    if last.contains('\\') || last.contains(':') || last.contains("..") {
        return None;
    }
    Some(last.to_string())
}

/// The wheel's bytes against the wheel's pin.
///
/// The same comparison the archive gets, under the stop that names the
/// package: a reader who is told the archive failed when the wheel did
/// is told the wrong thing about their own machine.
pub fn verify_package(bytes: &[u8], pinned: &str) -> Result<(), Slot> {
    verify(bytes, pinned).map_err(|_| Slot::bare(strings::FAIL_PACKAGE_CHECKSUM))
}

/// A wheel this app put down, and takes back out when the install that
/// needed it has answered.
///
/// The removal is a drop rather than a line after the install, so it
/// runs on the stop as well as on the success: every path out of the
/// install step leaves this scope, and none of them leaves the file.
pub struct Staged {
    pub at: PathBuf,
}

impl Drop for Staged {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.at);
    }
}

/// The pinned wheel, over the wire, proved, and put where uv can read it.
///
/// The digest is taken over the bytes in memory, and the write happens
/// only once it holds, so a wheel that is not the pinned one never
/// reaches the disk at all. That is the whole of what this adds: uv was
/// being asked to install whatever the address answered with, and the
/// bytes are now the app's own to check first.
///
/// The file lands under the app's own data directory. Nothing the user
/// keeps is written near, and the drop above takes it away again.
pub fn stage_package(pins: &config::Pins, data: Option<&Path>) -> Result<Staged, Slot> {
    let address = config::address_of(&pins.package_source);
    let name = wheel_name(address)
        .ok_or_else(|| Slot::of(strings::FAIL_PACKAGE_DOWNLOAD, address.to_string()))?;
    let into = data
        .ok_or_else(|| Slot::bare(strings::FAIL_PACKAGE_DOWNLOAD))?
        .join(PACKAGE_DIR);

    let client =
        https_client().map_err(|why| Slot::of(strings::FAIL_PACKAGE_DOWNLOAD, why))?;
    let bytes =
        fetch(&client, address).map_err(|why| Slot::of(strings::FAIL_PACKAGE_DOWNLOAD, why))?;
    verify_package(&bytes, &pins.package_sha256)?;

    std::fs::create_dir_all(&into)
        .map_err(|why| Slot::of(strings::FAIL_PACKAGE_DOWNLOAD, why.to_string()))?;
    let at = into.join(name);
    // A wheel already standing here is one of this app's own, left by a
    // run that was killed between the write and the removal: this
    // directory is the app's and nothing else ever writes into it. So it
    // is replaced rather than refused, which is what keeps a killed run
    // from stopping every install after it.
    let _ = std::fs::remove_file(&at);
    let staged = Staged { at };
    // write_new says what the write refused in its own detail; what it
    // names the stop is the archive's key, and this is not the archive.
    write_new(&staged.at, &bytes)
        .map_err(|why| Slot::of(strings::FAIL_PACKAGE_DOWNLOAD, why.detail))?;
    Ok(staged)
}

/// The install command's arguments, exactly.
///
/// `--force` is not among them and never will be: a `radialrig.exe` this
/// app did not write is a named stop, not a file to write over. The
/// package is an address, or the named form that carries one; a bare name
/// would reach a public index instead of the release this app pins.
pub fn install_args<'a>(python_pin: &'a str, source: &'a str) -> Vec<&'a str> {
    vec!["tool", "install", "--python", python_pin, source]
}

/// The command that takes the engine back out, for the recovery D4
/// allows and for the engine swap that replaces an old engine.
pub fn uninstall_args() -> Vec<&'static str> {
    vec!["tool", "uninstall", ENGINE_TOOL]
}

/// Whether `uv tool list` names a tool uv itself keeps.
///
/// uv prints one line per tool with the commands it installed indented
/// under it, so a tool is a line that starts at the margin and leads with
/// the name. An indented line names a command, not a tool.
pub fn lists_tool(said: &str, name: &str) -> bool {
    said.lines().any(|line| {
        !line.starts_with(char::is_whitespace)
            && !line.trim_start().starts_with('-')
            && line.split_whitespace().next() == Some(name)
    })
}

/// The deterministic stop, and the one thing the install step needs to
/// know before it runs: who the command standing there belongs to.
///
/// This is asked before this run changes anything at all — before the
/// Path entry, before any uninstall, before the install — so a machine
/// this run refuses is a machine this run has not touched. The command is
/// never started to answer the question: uv's own tool list answers it,
/// and this app's own install record answers the one case uv cannot.
///
/// A uv that cannot say what it keeps is not evidence that the command
/// standing there belongs to a stranger. That case stops on what actually
/// failed, which pairs with the retry control, rather than on the refusal
/// that no second press can move.
pub fn guard_engine(uv: Option<&Path>, dir: &Path, data: Option<&Path>) -> Result<Owner, Slot> {
    let exe = dir.join(ENGINE_EXE);
    let present = exe.is_file();
    let intent = intent_present(data, &exe);
    let listed = match uv {
        None => false,
        Some(uv) => match run(uv, &["tool", "list"]) {
            Ok(said) => lists_tool(&said, ENGINE_TOOL),
            Err(why) if present => {
                return Err(Slot::of(strings::FAIL_ENGINE_INSTALL, why))
            }
            Err(_) => false,
        },
    };
    match owner(present, listed, intent) {
        Owner::Foreign => Err(Slot::of(strings::FAIL_FOREIGN_EXE, exe.to_string_lossy())),
        settled => Ok(settled),
    }
}

/// What the engine step already knows when it starts.
#[derive(Clone, Copy, Debug, Default)]
pub struct Standing {
    /// Whether uv keeps a tool entry for the engine.
    pub listed: bool,
    /// Whether the command already there is new enough for this shell.
    pub current: bool,
    /// Whether the command already there is what this app's own install
    /// step left behind, with no uv entry to go with it.
    pub leftover: bool,
    /// Whether this pass takes the engine uv keeps back out before it
    /// installs: the recovery press, or the engine swap.
    pub recovering: bool,
}

/// The engine's command, whether it was already installed or is installed
/// by the step the gate named.
///
/// The recovery D4 pins is a press: the engine uv already owns is taken
/// back out and installed again from a clean state. `guard_engine` has
/// already run, so the refusal it carries is never traded for that
/// removal. The one replacement that happens on its own is the engine
/// swap, `swap_engine`, for an engine older than this app needs; it runs
/// the same changes through `change_engine`.
///
/// The wheel the pinned address names is downloaded and proved against
/// the pinned digest here, before anything on this machine changes, and
/// uv is then given the proved file. The debug switch is the one way
/// past that, and only a debug build has one: it is pointed at a wheel
/// that has no released digest, so there is nothing for it to answer to.
pub fn provide_engine(
    uv: &Path,
    dir: &Path,
    data: Option<&Path>,
    standing: Standing,
    chosen: &config::Source,
    step: &dyn Fn(&'static str),
    arm: &dyn Fn(),
) -> Result<PathBuf, Slot> {
    let exe = dir.join(ENGINE_EXE);
    if !standing.recovering && standing.current && standing.listed && exe.is_file() {
        return Ok(exe);
    }

    step(strings::PROGRESS_INSTALL);
    let staged = stage_wheel(chosen, data)?;
    let package = package_of(&staged, chosen);
    change_engine(uv, dir, data, standing, chosen, &package, &Remains::default(), arm)?;
    // The install has answered, so the wheel has been read and its file
    // goes. A stop on the way here takes it the same way, on its way out.
    drop(staged);
    resolve_engine(uv, step)
}

/// The wheel first, and before any machine change: a package that does
/// not arrive, or arrives as bytes the pin does not answer for, stops a
/// run that has taken nothing out yet. A debug build's switch stages
/// nothing.
fn stage_wheel(chosen: &config::Source, data: Option<&Path>) -> Result<Option<Staged>, Slot> {
    if chosen.overridden {
        return Ok(None);
    }
    stage_package(config::pins(), data).map(Some)
}

/// What uv is given: the proved file when there is one, and the switch's
/// own value when a debug build set one.
fn package_of(staged: &Option<Staged>, chosen: &config::Source) -> String {
    match staged {
        Some(held) => held.at.to_string_lossy().to_string(),
        None => chosen.value.clone(),
    }
}

/// The changes one pass of the engine step makes, in order: uv's entry
/// out, when this pass takes it out; a swap's own remains out, when uv
/// keeps no entry for them; this app's own leftover out; and the install,
/// with its record down in front of it.
#[allow(clippy::too_many_arguments)]
fn change_engine(
    uv: &Path,
    dir: &Path,
    data: Option<&Path>,
    standing: Standing,
    chosen: &config::Source,
    package: &str,
    remains: &Remains,
    arm: &dyn Fn(),
) -> Result<(), Slot> {
    let pins = config::pins();
    let exe = dir.join(ENGINE_EXE);

    // The stop's key is chosen by what has happened by the time it fires.
    // The recovery's sentence is about taking the engine out, and it is
    // chosen only once the uninstall below has run; a recovery press on
    // a machine where uv keeps no entry takes nothing out, and its
    // failure is the install's own (app-bootstrap-03).
    let mut failed = strings::FAIL_ENGINE_INSTALL;

    // The one uninstall this shell runs. An ordinary first pass takes
    // nothing back out. It runs on the recovery press, which the reader
    // chose, and on the engine swap, which replaces an engine older than
    // this app needs with no press (PLAN-0021 Decided 7).
    if standing.recovering && standing.listed {
        failed = strings::FAIL_RECOVERY;
        run(uv, &uninstall_args()).map_err(|why| Slot::of(failed, why))?;
    }
    if !standing.listed {
        // A swap's own remains: the launchers of the engine it began
        // replacing, still byte for byte what it read, with no uv entry
        // left to take them out. Read on uv 0.12.5 (phase 2 review): once
        // uv's own record of the engine is gone too, it refuses an install
        // while any of them stands, and while that record survives it
        // writes over them. The swap takes them out itself either way, and
        // nothing else in the folder. Outside a swap this list is empty.
        for launcher in remains.standing() {
            std::fs::remove_file(&launcher).map_err(|why| Slot::of(failed, why.to_string()))?;
        }
    }
    if standing.leftover && exe.is_file() {
        // The command this app's own install step created and never got a
        // uv entry written for. Taking it back out is taking back this
        // app's own leftover, which is the one file constraint 5 leaves
        // this app free to remove. A command that neither the record nor
        // a running swap accounts for never reaches this line: the refusal
        // stands in front of it.
        std::fs::remove_file(&exe).map_err(|why| Slot::of(failed, why.to_string()))?;
    }

    // The record of what is about to run goes down first, so that a close
    // or a crash in the middle of the install leaves this app able to
    // recognise its own leavings on the next pass.
    //
    // The record names the pinned address, not the file uv was given:
    // the address is what this app installed from, whatever local copy
    // of it the install command read, and `guard_engine` and `discover`
    // read this field for that meaning.
    let intent = IntentRecord {
        source: chosen.value.clone(),
        exe: exe.to_string_lossy().to_string(),
        written: intent_clock(),
    };
    keep_intent(data, &intent)?;
    if let Err(why) = run(uv, &install_args(&pins.python_pin, package)) {
        // The install did not take. The record stands for a command this
        // app's own step may have written, and it stays down only while
        // one may be standing there: uv writes the command after the
        // environment it runs in and before the entry that names it, so
        // a failed install that left nothing at the recorded path has
        // nothing for the next press to take back out, and a record kept
        // past that point would speak for whatever someone else puts
        // there later. A command that is there is left for the next press
        // to decide about, record and all; a swap that gives up takes the
        // record away itself.
        if !exe.is_file() {
            clear_intent(data);
        }
        if !standing.recovering {
            arm();
        }
        return Err(Slot::of(failed, why));
    }
    clear_intent(data);
    Ok(())
}

/// The command uv installed, found where uv says it put it.
fn resolve_engine(uv: &Path, step: &dyn Fn(&'static str)) -> Result<PathBuf, Slot> {
    step(strings::PROGRESS_RESOLVE);
    let exe = tool_bin_dir(uv)?.join(ENGINE_EXE);
    if !exe.is_file() {
        return Err(Slot::of(strings::FAIL_TOOL_DIR, exe.to_string_lossy()));
    }
    Ok(exe)
}

// --- PLAN-0021 phase 2, prescription 4: the engine swap -----------------

/// How many times the swap tries to replace the engine before it gives
/// up: the first attempt and two more.
pub const SWAP_ATTEMPTS: usize = 3;

/// The old engine's launchers as they stood when a swap began: each one's
/// full path and the digest of its bytes.
///
/// This is how a retry knows the engine's remains as its own (the first
/// outcome prescription 4 asks for). It is read once, at the start of a
/// swap over an engine uv keeps an entry for, and it lives only as long
/// as that swap: nothing of it is written anywhere, so a swap that gives
/// up, or a close in the middle of one, leaves no record that could claim
/// a file later (the second outcome). It claims a file only at a path it
/// read, and only while that file is still byte for byte what it read.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Remains {
    launchers: Vec<(PathBuf, String)>,
}

impl Remains {
    /// The engine's launchers standing in uv's launcher folder now.
    pub fn read(bin_dir: &Path) -> Self {
        let launchers = holders::launcher_files(bin_dir)
            .into_iter()
            .filter_map(|path| {
                let bytes = std::fs::read(&path).ok()?;
                Some((path, digest(&bytes)))
            })
            .collect();
        Remains { launchers }
    }

    /// Whether the file at `path` is one of them, unchanged.
    pub fn claims(&self, path: &Path) -> bool {
        self.launchers.iter().any(|(at, then)| {
            at == path && std::fs::read(path).is_ok_and(|bytes| digest(&bytes) == *then)
        })
    }

    /// The ones still standing unchanged.
    fn standing(&self) -> Vec<PathBuf> {
        self.launchers
            .iter()
            .map(|(at, _)| at.clone())
            .filter(|at| self.claims(at))
            .collect()
    }
}

/// A stop, and the one sentence an engine swap that gave up adds under
/// it. Every other stop adds none.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Stop {
    pub failure: Slot,
    pub held: Option<Slot>,
}

impl From<Slot> for Stop {
    fn from(failure: Slot) -> Self {
        Stop { failure, held: None }
    }
}

/// The engine swap, against the programs this account runs.
pub fn swap_engine(
    uv: &Path,
    found: &Discovery,
    dir: &Path,
    data: Option<&Path>,
    chosen: &config::Source,
    step: &dyn Fn(&'static str),
) -> Result<PathBuf, Stop> {
    swap_engine_with(uv, found, dir, data, chosen, step, &holders::stop, &holders::list)
}

/// The engine swap: an engine older than this app needs is replaced with
/// no press, while programs that use RadialRig are running it (PLAN-0021
/// phase 2, prescription 4; Decided 7).
///
/// The screen says first, in plain words, that those programs lose
/// RadialRig for a moment and may need to reconnect. The wheel is fetched
/// and proved once, before anything changes. Each attempt then stops the
/// programs that hold the engine, right before the change, so one that
/// started a server again in between is stopped too, and runs the
/// recovery: the uninstall when uv keeps an entry, then the install. When
/// the uninstall or the install fails, the next attempt looks at the
/// machine again and goes on from what it finds, so a half-removed engine
/// is taken up where it was left. Three attempts in all.
///
/// A retry can meet the state a failed uninstall leaves: uv's entry gone
/// and the old launchers still in place. The install record cannot speak
/// for them, since they were written long before it, so the swap reads the
/// old engine's launchers as it begins, and a retry takes a launcher that
/// is still byte for byte what it read as this app's own. A launcher the
/// app never installed, or one changed since, is refused as it always was.
///
/// A swap that gives up takes away the install record the install step
/// may have kept, and it keeps nothing of its own, so no record is left
/// that could later claim a file this app did not write. Its stop is the
/// recovery's sentence with one added: the programs that still hold the
/// engine, by file name, or that none was found running from its folders.
///
/// `stop` and `list` are how the swap reaches those programs: `holders`
/// in the app, and stand-ins a test hands it.
#[allow(clippy::too_many_arguments)]
pub fn swap_engine_with(
    uv: &Path,
    found: &Discovery,
    dir: &Path,
    data: Option<&Path>,
    chosen: &config::Source,
    step: &dyn Fn(&'static str),
    stop: &dyn Fn(&Path, &Path) -> holders::Stopped,
    list: &dyn Fn(&Path, &Path) -> Vec<holders::Holder>,
) -> Result<PathBuf, Stop> {
    if let (Some(exe), true) = (&found.engine, found.engine_current) {
        return Ok(exe.clone());
    }
    // The tool folder is asked for before anything is shown or changed:
    // without it the programs running from the engine's own folder cannot
    // be found, and stopping only some of them is not this swap.
    let tool = tool_dir(uv)?;
    // Read only over an engine uv keeps an entry for. A swap that begins
    // anywhere else knows no remains, so it can claim nothing.
    let remains = match found.engine {
        Some(_) => Remains::read(dir),
        None => Remains::default(),
    };
    step(strings::SWAP_RUNNING);
    let staged = stage_wheel(chosen, data)?;
    let package = package_of(&staged, chosen);

    let mut standing = swap_standing(found);
    let mut last = Slot::bare(strings::FAIL_RECOVERY);
    for attempt in 1..=SWAP_ATTEMPTS {
        if attempt > 1 {
            let again =
                discover_with(config::pins(), data, Some(uv.to_path_buf()), Some(&remains));
            if let Some(refused) = again.probe_failed.clone().or_else(|| again.refusal.clone()) {
                clear_intent(data);
                return Err(refused.into());
            }
            if let (Some(exe), true) = (&again.engine, again.engine_current) {
                clear_intent(data);
                return Ok(exe.clone());
            }
            standing = swap_standing(&again);
        }
        stop(&tool, dir);
        match change_engine(uv, dir, data, standing, chosen, &package, &remains, &|| {}) {
            Ok(()) => {
                drop(staged);
                return resolve_engine(uv, step).map_err(Stop::from);
            }
            Err(failed) => last = failed,
        }
    }
    drop(staged);
    clear_intent(data);
    Err(gave_up(last, &list(&tool, dir)))
}

/// What one attempt starts from. The swap always takes out what uv keeps
/// before it installs, so it is a recovering pass whatever it found.
fn swap_standing(found: &Discovery) -> Standing {
    Standing {
        listed: found.listed,
        current: found.engine_current,
        leftover: found.leftover,
        recovering: true,
    }
}

/// The stop a swap that gave up shows: the recovery's sentence over the
/// last failure, with one sentence added under it.
fn gave_up(last: Slot, left: &[holders::Holder]) -> Stop {
    Stop {
        failure: Slot::of(strings::FAIL_RECOVERY, last.detail),
        held: held_sentence(left),
    }
}

/// What still holds the engine, by file name, each program once and in a
/// steady order, or that nothing running from the engine's folders was
/// found, in which case some other program may have a file open there.
/// On Linux an open file does not stop a replacement, so nothing is added.
fn held_sentence(left: &[holders::Holder]) -> Option<Slot> {
    if !cfg!(windows) {
        return None;
    }
    let mut names: Vec<String> = left
        .iter()
        .filter_map(|holder| holder.image.file_name())
        .map(|name| name.to_string_lossy().to_string())
        .collect();
    names.sort_by_key(|name| name.to_lowercase());
    names.dedup_by(|one, other| one.eq_ignore_ascii_case(other));
    Some(if names.is_empty() {
        Slot::bare(strings::SWAP_HELD_NONE)
    } else {
        Slot::of(strings::SWAP_HELD, names.join(", "))
    })
}

// --- the one-shot state machine ----------------------------------------

/// The shell's own state, and the one place the screen is written from.
pub struct Shell {
    view: Mutex<View>,
    /// The block set the gate rendered, kept exactly as the reader saw
    /// it. The run executes this set — not a set computed again later —
    /// so a step can only run because it was named (R6).
    consented: Mutex<Vec<&'static str>>,
    /// The local screen's own address, kept so the return navigation D7
    /// pins has somewhere to go once the window has left it.
    home: Mutex<Option<String>>,
    /// Whether the consent press has been accepted this launch.
    started: AtomicBool,
    /// Whether a run is in flight.
    busy: AtomicBool,
    /// Whether the next press takes the engine back out before installing
    /// it. D4's recovery is a press, not a thing that happens on its own:
    /// the reader is told what failed and chooses to run it.
    recover: AtomicBool,
    /// Whether the next pass is the engine swap. The opening look sets it
    /// when it finds an engine older than this app needs and starts the
    /// pass itself, with no press (PLAN-0021 Decided 7). A swap that gave
    /// up, or a version check that found the engine too old, sets it for
    /// the press its stop pairs with.
    swap: AtomicBool,
    /// The engine's lifetime, once there is one.
    pub engine: attach::Engine,
}

impl Shell {
    pub fn new() -> Self {
        Shell {
            view: Mutex::new(View::empty()),
            consented: Mutex::new(Vec::new()),
            home: Mutex::new(None),
            started: AtomicBool::new(false),
            busy: AtomicBool::new(false),
            recover: AtomicBool::new(false),
            swap: AtomicBool::new(false),
            engine: attach::Engine::new(),
        }
    }

    pub fn snapshot(&self) -> View {
        self.view.lock().expect("the shell state").clone()
    }

    /// The set the gate put in front of the reader, kept as the reader
    /// saw it.
    pub fn consent_to(&self, blocks: Vec<&'static str>) {
        *self.consented.lock().expect("the shell state") = blocks;
    }

    /// The set the run is allowed to execute.
    pub fn consented(&self) -> Vec<&'static str> {
        self.consented.lock().expect("the shell state").clone()
    }

    pub fn remember_home(&self, url: String) {
        let mut home = self.home.lock().expect("the shell state");
        if home.is_none() {
            *home = Some(url);
        }
    }

    pub fn home(&self) -> Option<String> {
        self.home.lock().expect("the shell state").clone()
    }

    /// Whether an engine step is running: a pass over the named steps,
    /// the engine swap among them. The update reads it before it asks
    /// (PLAN-0021 phase 3, prescription 3).
    pub fn step_running(&self) -> bool {
        self.busy.load(Ordering::SeqCst)
    }

    /// Take the flag a pass takes when it starts, for an update about to
    /// close the app. One exchange, as `launch` takes it: it fails while
    /// a pass runs, and while it is held no pass starts, so the app never
    /// closes in the middle of an engine step (PLAN-0021 phase 3,
    /// prescription 3).
    pub fn hold(&self) -> bool {
        self.busy
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
    }

    /// Give the flag back, after an update that installed nothing and
    /// closed nothing.
    pub fn let_go(&self) {
        self.busy.store(false, Ordering::SeqCst);
    }

    /// Put one picture on the screen and keep it, so a reload or the
    /// return navigation redraws the same thing.
    pub fn show(&self, app: &AppHandle, view: View) {
        {
            let mut held = self.view.lock().expect("the shell state");
            *held = view.clone();
        }
        let _ = app.emit(STATE_EVENT, view);
    }

    fn step(&self, app: &AppHandle, key: &'static str) {
        let mut view = self.snapshot();
        view.phase = PHASE_RUNNING;
        view.progress = Some(key);
        view.failure = None;
        view.held = None;
        view.retry = false;
        self.show(app, view);
    }

    fn stop(&self, app: &AppHandle, stop: impl Into<Stop>) {
        self.show(app, stopped_picture(self.snapshot(), stop.into()));
    }

    fn done(&self, app: &AppHandle) {
        // The reading and the writing happen under one hold of the lock.
        // An engine that ends between the navigation and this line puts
        // its own stop down from the watcher's thread, and a check that
        // let go of the lock before writing would step on it.
        let view = {
            let mut held = self.view.lock().expect("the shell state");
            // Nothing here takes a stop back.
            if held.failure.is_some() {
                return;
            }
            held.phase = PHASE_DONE;
            held.progress = None;
            held.retry = false;
            held.clone()
        };
        let _ = app.emit(STATE_EVENT, view);
    }

    /// Back to the gate, with the set a fresh look at the machine names.
    ///
    /// The consent a reader gave was to the set they read. A step that
    /// was not in it does not run on the strength of that press: the
    /// screen returns to the gate carrying the new set, and the press is
    /// asked for again.
    fn regate(&self, app: &AppHandle, blocks: Vec<&'static str>) {
        self.consent_to(blocks.clone());
        self.started.store(false, Ordering::SeqCst);
        let mut view = self.snapshot();
        view.phase = PHASE_GATE;
        view.blocks = blocks;
        view.progress = None;
        view.failure = None;
        view.held = None;
        view.retry = false;
        self.show(app, view);
    }
}

impl Default for Shell {
    fn default() -> Self {
        Shell::new()
    }
}

/// The stopped picture. The step the run reached stays on the view: the
/// page draws its count and its track only when a run actually got
/// somewhere, and a stop from the opening look at the machine carries no
/// progress at all — a count there would name a step that never
/// executed. Each fresh pass clears the field itself before its first
/// step, so nothing here can carry one run's progress into the next.
fn stopped_picture(mut view: View, stop: Stop) -> View {
    view.phase = PHASE_FAILED;
    view.retry = strings::retryable(stop.failure.key);
    view.failure = Some(stop.failure);
    view.held = stop.held;
    view
}

/// The gate, drawn from a discovery pass, or the run itself when the
/// pass found that nothing on this machine would change.
///
/// Consent to an empty list is not consent to anything, so a machine that
/// already carries everything the gate would name goes straight to the
/// engine. Nothing installs on that path unless the engine there is older
/// than this app needs: then the run is the engine swap, which replaces
/// it with no press (PLAN-0021 phase 2, prescription 4).
pub fn open(app: &AppHandle) {
    let pins = config::pins();
    let chosen = config::source(pins);
    let data = app.path().app_data_dir().ok();
    let found = discover(pins, data.as_deref());
    let blocks = plan(&found);

    let mut view = View::empty();
    view.found_uv = found.uv.as_ref().map(|p| p.to_string_lossy().to_string());
    // Before there is a uv to ask, the gate names the directory uv puts
    // commands in by default, which is where this run would put uv itself.
    view.bin_dir = found
        .bin_dir
        .clone()
        .or_else(documented_dir)
        .map(|dir| dir.to_string_lossy().to_string());
    // The PATH report, read before the gate is drawn, so what the step
    // will say is already on the screen where consent happens. The step
    // itself reads again when it runs (PLAN-0009 phase 3 prescription 3).
    #[cfg(not(windows))]
    if let Some(dir) = found.bin_dir.clone().or_else(documented_dir) {
        view.path_report = Some(path_report_of(&dir, found.on_path));
    }
    if chosen.overridden {
        view.notice = Some(Slot::of(strings::FAIL_DEBUG_OVERRIDE, chosen.value.clone()));
    }
    view.blocks = blocks.clone();

    let shell = app.state::<Shell>();
    shell.consent_to(blocks.clone());
    match opening(&found, &blocks) {
        // The two stops discovery itself can reach, both of them before a
        // single byte on this machine has changed.
        Opening::Stop(stop) => {
            shell.show(app, view);
            shell.stop(app, stop);
            // A stop is where the engine step ends on this launch, so the
            // app may ask about an update now (PLAN-0021 phase 3,
            // prescription 3).
            crate::update::finished(app);
        }
        // An engine uv keeps an entry for that answers a version older
        // than this shell drives. It is replaced with no press, and the
        // screen says what that does while it runs (Decided 7).
        Opening::Swap => {
            shell.swap.store(true, Ordering::SeqCst);
            view.phase = PHASE_RUNNING;
            shell.show(app, view);
            shell.started.store(true, Ordering::SeqCst);
            launch(app.clone());
        }
        Opening::Run => {
            view.phase = PHASE_RUNNING;
            shell.show(app, view);
            shell.started.store(true, Ordering::SeqCst);
            launch(app.clone());
        }
        Opening::Gate => {
            view.phase = PHASE_GATE;
            shell.show(app, view);
        }
    }
}

/// One pass over the steps the gate named, on its own thread.
fn launch(app: AppHandle) {
    let recovering;
    let swapping;
    {
        let shell = app.state::<Shell>();
        if shell.busy.swap(true, Ordering::SeqCst) {
            return;
        }
        // The recovery is spent by the press that runs it: if this pass
        // fails again for the same reason it arms itself again, and if it
        // succeeds nothing is left armed for the next launch. The swap is
        // spent the same way.
        recovering = shell.recover.swap(false, Ordering::SeqCst);
        swapping = shell.swap.swap(false, Ordering::SeqCst);
        // The gate comes down here rather than at the first step, because
        // the first step is conditional and a press that appears to do
        // nothing is a press a reader will make twice.
        let mut view = shell.snapshot();
        view.phase = PHASE_RUNNING;
        // A stop keeps the step its run reached; this pass has not
        // reached one yet.
        view.progress = None;
        view.failure = None;
        view.held = None;
        view.retry = false;
        shell.show(&app, view);
    }
    std::thread::spawn(move || {
        let outcome = work(&app, recovering, swapping);
        let shell = app.state::<Shell>();
        // A pass back at the gate has not run the engine step: the gate
        // asks for a press first. The other two ends are the engine's page
        // and a stop, which is where the engine step finishes.
        let finished = !matches!(outcome, Ok(Outcome::Regated(_)));
        match outcome {
            Ok(Outcome::Ran) => shell.done(&app),
            Ok(Outcome::Regated(blocks)) => shell.regate(&app, blocks),
            Err(stop) => shell.stop(&app, stop),
        }
        shell.busy.store(false, Ordering::SeqCst);
        // Reported once the flag is down, so the update reads no pass as
        // running (PLAN-0021 phase 3, prescription 3).
        if finished {
            crate::update::finished(&app);
        }
    });
}

/// What one pass over the consented set did.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// The named steps ran, and the engine is up.
    Ran,
    /// A step this machine now needs was never named, so nothing ran and
    /// the screen is back at the gate with the set that names it.
    Regated(Vec<&'static str>),
}

/// Whether a fresh look at the machine asks for a step the reader never
/// agreed to.
///
/// A step that was named and is no longer needed is simply skipped — the
/// reader agreed to a set, and doing less than they agreed to is not
/// doing something they did not agree to. A step that is needed and was
/// never named is the other direction, and the press does not carry it.
pub fn unnamed(named: &[&'static str], fresh: &[&'static str]) -> Vec<&'static str> {
    fresh
        .iter()
        .copied()
        .filter(|key| !named.contains(key))
        .collect()
}

/// The whole journey, in the order the gate named it: the uv, the Path
/// entry, the engine, the version check, the start.
fn work(app: &AppHandle, recovering: bool, swapping: bool) -> Result<Outcome, Stop> {
    let pins = config::pins();
    let chosen = config::source(pins);
    let handle = app.clone();
    let step = move |key: &'static str| {
        let shell = handle.state::<Shell>();
        shell.step(&handle, key);
    };
    let arming = app.clone();
    let arm = move || {
        arming.state::<Shell>().recover.store(true, Ordering::SeqCst);
    };
    let swapper = app.clone();
    let arm_swap = move || {
        swapper.state::<Shell>().swap.store(true, Ordering::SeqCst);
    };
    let data = app.path().app_data_dir().ok();

    // The set the gate rendered and the reader pressed. It is carried
    // here rather than worked out again: a step runs because it was
    // named, and for no other reason.
    let named = app.state::<Shell>().consented();

    if documented_uv().map(|uv| uv.is_file()).unwrap_or(false) {
        step(strings::PROGRESS_PROBE);
    }
    let found = discover(pins, data.as_deref());
    let reached = found.probe_failed.clone().or_else(|| found.refusal.clone());
    if let Some(stop) = reached {
        return Err(stop.into());
    }
    // A fresh look at the machine, for one question only: does anything
    // this run would now have to do stand outside the set the reader
    // read? If so, nothing runs, and the gate is asked again with the set
    // that names it. The recovery press and the engine swap replace the
    // engine whatever the gate named; every other step still needs its
    // own consent, and is skipped below when it was not given.
    let fresh = plan(&found);
    if !recovering && !swapping && !unnamed(&named, &fresh).is_empty() {
        return Ok(Outcome::Regated(fresh));
    }

    // The refusal goes in front of every machine change, the archive
    // included: a machine with no uv keeps no uv entry, so a command
    // standing in the documented directory is already answerable.
    let mut settled = match found.bin_dir.clone().or_else(documented_dir) {
        Some(dir) => guard_engine(found.uv.as_deref(), &dir, data.as_deref())?,
        None => Owner::Absent,
    };

    // Each named step re-checks its own precondition before it runs, and
    // skips when the machine no longer needs it.
    let uv = if named.contains(&strings::GATE_UV) || found.uv.is_some() {
        provide_uv(&found, pins, &step)?
    } else {
        // The uv step was not named and there is no uv to use. That is
        // exactly the case the fresh-set check above sends back to the
        // gate, so this line is reached only when the recovery press or
        // the swap is running, which needs a uv it can name.
        return Ok(Outcome::Regated(fresh));
    };

    let dir = match &found.bin_dir {
        Some(dir) => dir.clone(),
        None => tool_bin_dir(&uv)?,
    };
    if found.uv.is_none() {
        // The pass above had no uv to ask. This is the first moment the
        // question can be answered from what uv itself keeps.
        settled = guard_engine(Some(&uv), &dir, data.as_deref())?;
    }
    let standing = Standing {
        listed: settled == Owner::Ours,
        current: found.engine_current,
        leftover: settled == Owner::Leftover,
        recovering,
    };

    #[cfg(windows)]
    if named.contains(&strings::GATE_PATH) && !user_path_has(&dir) {
        provide_path(&uv, data.as_deref(), &step)?;
    }
    // On Linux the same step reads and reports instead of writing: it
    // runs on every pass because a read is an asking step, not a machine
    // change the gate would have to name (PLAN-0009 phase 3
    // prescription 3). The report lands on the view, where the gate
    // already showed the opening read.
    #[cfg(not(windows))]
    {
        step(strings::PROGRESS_PATH);
        let shell = app.state::<Shell>();
        let mut view = shell.snapshot();
        view.path_report = Some(path_report_of(&dir, user_path_has(&dir)));
        shell.show(app, view);
    }

    let engine = if swapping {
        // An engine older than this app needs, replaced with no press. A
        // swap that stops arms the next press to run the swap again.
        swap_engine(&uv, &found, &dir, data.as_deref(), &chosen, &step).map_err(|stop| {
            arm_swap();
            stop
        })?
    } else if named.contains(&strings::GATE_ENGINE) || recovering {
        provide_engine(
            &uv,
            &dir,
            data.as_deref(),
            standing,
            &chosen,
            &step,
            &arm,
        )?
    } else {
        // The engine step was not named because the engine was already
        // there and current. It still has to be the command this shell
        // drives, so the resolution runs and its own stop stands.
        let exe = dir.join(ENGINE_EXE);
        if !exe.is_file() {
            return Ok(Outcome::Regated(plan(&discover(pins, data.as_deref()))));
        }
        exe
    };

    // D6's launch check. Too old is its own named state, reached now only
    // when the engine just installed is itself too old, and the press it
    // pairs with runs the engine swap.
    step(strings::PROGRESS_VERSION);
    let installed = attach::engine_version(&engine).map_err(|stop| {
        // An engine that was just installed and cannot say its own version
        // is the half-installed state D4 names. The next press takes it
        // back out and installs it again from nothing.
        arm();
        stop
    })?;
    match attach::at_least(&installed, &pins.engine_min_version) {
        Some(true) => {}
        _ => {
            // The named state, and the press that pairs with its sentence
            // runs the swap: the programs that hold the engine stopped,
            // the entry out, then a fresh install. Nothing is taken out on
            // this pass.
            arm_swap();
            return Err(engine_old(Some(&installed)).into());
        }
    }

    step(strings::PROGRESS_START);
    attach::start(app, &engine)
        .map_err(|stop| {
            if stop.key == strings::FAIL_SPAWN {
                arm();
            }
            Stop::from(stop)
        })
        .map(|()| Outcome::Ran)
}

/// D12: accepted once per launch. A second press answers the state the
/// first press put the window in.
#[tauri::command]
pub fn bootstrap_proceed(app: AppHandle, shell: State<'_, Shell>) -> View {
    if !shell.started.swap(true, Ordering::SeqCst) {
        launch(app.clone());
    }
    shell.snapshot()
}

/// D12: from a named failure state only, which includes the stopped state
/// the return navigation D7 lands on. From anywhere else it answers the
/// state the window is already in and changes nothing.
#[tauri::command]
pub fn bootstrap_retry(app: AppHandle, shell: State<'_, Shell>) -> View {
    let view = shell.snapshot();
    if view.phase == PHASE_FAILED && view.retry {
        launch(app.clone());
    }
    shell.snapshot()
}

/// The picture the page should draw, asked for by the page itself the
/// moment it finishes loading — the launch, a retry's return, and D7's
/// return navigation all arrive here.
pub fn redraw(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let _ = app.emit(STATE_EVENT, shell.snapshot());
    // The page registers its listener as its script parses, which is
    // before the load this answers, but the registration is itself a
    // round trip. One repeat covers a registration that had not landed.
    let again = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(300));
        let shell = again.state::<Shell>();
        let _ = again.emit(STATE_EVENT, shell.snapshot());
    });
}

/// The stopped state D7's return navigation lands on.
pub fn stopped(app: &AppHandle, detail: String) {
    let shell = app.state::<Shell>();
    shell.stop(app, Slot::of(strings::FAIL_ENGINE_STOPPED, detail));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pins() -> config::Pins {
        config::pins().clone()
    }

    #[test]
    fn a_stop_keeps_the_step_its_run_reached_and_no_more() {
        // Mid-run: the stop rides on the step that was in flight, so the
        // page can count a run that actually happened.
        let mut view = View::empty();
        view.phase = PHASE_RUNNING;
        view.progress = Some(strings::PROGRESS_DOWNLOAD);
        let held = stopped_picture(view, Slot::bare(strings::FAIL_DOWNLOAD).into());
        assert_eq!(held.phase, PHASE_FAILED);
        assert_eq!(held.progress, Some(strings::PROGRESS_DOWNLOAD));
        assert_eq!(
            held.failure.as_ref().map(|s| s.key),
            Some(strings::FAIL_DOWNLOAD)
        );
        assert_eq!(held.held, None);

        // Before any run: the opening look at the machine has no step to
        // keep, and the page draws no count for a run that never started.
        let cold = stopped_picture(View::empty(), Slot::bare(strings::FAIL_UV_PROBE).into());
        assert_eq!(cold.phase, PHASE_FAILED);
        assert_eq!(cold.progress, None);

        // A swap that gave up carries its added sentence onto the picture,
        // and it pairs with the press like the recovery it stands on.
        let gave_up = Stop {
            failure: Slot::of(strings::FAIL_RECOVERY, "2: denied"),
            held: Some(Slot::bare(strings::SWAP_HELD_NONE)),
        };
        let picture = stopped_picture(View::empty(), gave_up);
        assert_eq!(picture.held, Some(Slot::bare(strings::SWAP_HELD_NONE)));
        assert!(picture.retry);
    }

    #[test]
    fn a_version_answer_is_read_by_the_name_that_leads_it() {
        assert_eq!(parse_named_version("uv", "uv 0.12.5\n").as_deref(), Some("0.12.5"));
        assert_eq!(
            parse_named_version("radialrig", "radialrig 0.1.0").as_deref(),
            Some("0.1.0")
        );
        // The name has to lead the line, not sit inside it.
        assert_eq!(parse_named_version("uv", "the uv 0.12.5"), None);
        assert_eq!(parse_named_version("uv", "uv"), None);
        assert_eq!(parse_named_version("uv", "uv "), None);
        assert_eq!(parse_named_version("uv", ""), None);
        // Anything after the version is not the version.
        assert_eq!(
            parse_named_version("uv", "uv 0.12.5 (abc 2026-08-20)").as_deref(),
            Some("0.12.5")
        );
    }

    #[test]
    fn a_tail_is_bounded_and_keeps_the_last_of_what_was_said() {
        assert_eq!(tail_of(""), "");
        assert_eq!(tail_of("one\n\ntwo\n"), "one two");
        assert_eq!(tail_of("a\nb\nc\nd\ne"), "c d e");
        let long = "x".repeat(900);
        assert_eq!(tail_of(&long).chars().count(), 400);
    }

    #[test]
    fn the_first_line_is_the_first_line() {
        assert_eq!(first_line("a\r\nb\r\n"), "a");
        assert_eq!(first_line("  a  "), "a");
        assert_eq!(first_line(""), "");
    }

    // --- PLAN-0021 phase 2, prescription 3: uv's two folders -----------
    //
    // The uv these tests ask is a stand-in script written into a scratch
    // folder of the test's own, never a uv on this machine: the folders a
    // real uv names are where the engine and its servers live.

    /// A stand-in uv: a script in a scratch folder of its own, taken away
    /// when the stand-in goes out of scope, a failed test included.
    #[cfg(windows)]
    struct StandIn {
        root: PathBuf,
        uv: PathBuf,
    }

    #[cfg(windows)]
    impl StandIn {
        /// The script's body is written by the caller, given the scratch
        /// folder, so an answer can name a folder inside it.
        fn new(name: &str, body: impl Fn(&Path) -> String) -> Self {
            let root = std::env::temp_dir()
                .join(format!("radialrig-tooldir-{}-{name}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(&root).expect("a scratch folder");
            let uv = root.join("uv.cmd");
            std::fs::write(&uv, format!("@echo off\r\n{}\r\n", body(&root)))
                .expect("the stand-in");
            StandIn { root, uv }
        }
    }

    #[cfg(windows)]
    impl Drop for StandIn {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    #[test]
    #[cfg(windows)]
    fn the_tool_folder_is_the_one_uv_names_beside_the_launcher_folder() {
        // The stand-in answers `tool dir --bin` with one folder and
        // `tool dir` with the other, so each ask is told by its answer.
        let uv = StandIn::new("answers", |root| {
            format!(
                "if \"%~3\"==\"--bin\" (echo {}) else (echo {})",
                root.join("tool-bin").display(),
                root.join("tools").display()
            )
        });
        assert_eq!(tool_dir(&uv.uv), Ok(uv.root.join("tools")));
        assert_eq!(tool_bin_dir(&uv.uv), Ok(uv.root.join("tool-bin")));
    }

    #[test]
    #[cfg(windows)]
    fn a_tool_folder_uv_cannot_name_stops_as_the_launcher_folder_does() {
        // Each way uv can fail to name a folder stops both asks with the
        // same sentence and the same detail: nothing said, a folder that
        // is not absolute, and a uv that ends in failure.
        let cases = [
            ("silent", "exit /b 0", ""),
            ("relative", "echo tools", "tools"),
            ("fails", "echo out of reach 1>&2\r\nexit /b 2", "2: out of reach"),
        ];
        for (name, body, detail) in cases {
            let uv = StandIn::new(name, |_| body.to_string());
            let stop = tool_dir(&uv.uv).unwrap_err();
            assert_eq!(stop, Slot::of(strings::FAIL_TOOL_DIR, detail), "{name}");
            assert_eq!(tool_bin_dir(&uv.uv), Err(stop), "{name}");
        }
    }

    // --- PLAN-0021 phase 2, prescription 4: the engine swap ---------------
    //
    // Every swap here runs against a stand-in uv script in a scratch folder
    // of the test's own. Its tool folder and its launcher folder are inside
    // that same scratch folder, so the programs a swap lists or stops can
    // only be ones a test started there. No test names a uv on this
    // machine, the real engine folders, or a process it did not start.

    /// The five launchers, as the stand-in's own loops spell them.
    #[cfg(windows)]
    const RIG_LAUNCHERS: &str =
        "radialrig radialrig-read radialrig-write radialrig-validate radialrig-index";

    /// A stand-in uv that keeps its state in files beside it, so a swap
    /// can be run against it end to end. `listed` is the entry uv keeps.
    /// Three files steer it: `stuck` makes every uninstall fail with the
    /// entry kept; `strand` makes the next uninstall fail after the entry
    /// went, the old launcher left in place (copying `stranger` over that
    /// launcher first, when there is one); `broken` makes every install
    /// write a launcher and then fail. Otherwise it behaves as uv does: an
    /// uninstall that meets a running program's file fails, and an install
    /// refuses while any of the five launchers is already there. That last
    /// rule is stricter than uv 0.12.5, which refuses only once its own
    /// record of the engine is gone and writes over the launchers while
    /// the record survives (phase 2 review), so a swap that left its
    /// remains standing fails here even where uv would let it through.
    #[cfg(windows)]
    struct Rig {
        root: PathBuf,
        uv: PathBuf,
        tools: PathBuf,
        bin: PathBuf,
        data: PathBuf,
        running: Vec<std::process::Child>,
    }

    #[cfg(windows)]
    impl Rig {
        fn new(name: &str) -> Self {
            let made = std::env::temp_dir()
                .join(format!("radialrig-swap-{}-{name}", std::process::id()));
            let _ = std::fs::remove_dir_all(&made);
            std::fs::create_dir_all(&made).expect("a scratch folder");
            // The folder as the system spells it, so the program paths
            // Windows reports for what runs here compare with it.
            let full = std::fs::canonicalize(&made).expect("the scratch folder");
            let spelled = full.to_string_lossy().to_string();
            let root = PathBuf::from(spelled.strip_prefix("\\\\?\\").unwrap_or(&spelled));
            let tools = root.join("tools");
            let bin = root.join("bin");
            let data = root.join("data");
            std::fs::create_dir_all(tools.join(ENGINE_TOOL).join("Scripts")).expect("tools");
            std::fs::create_dir_all(&bin).expect("bin");
            let script = [
                "@echo off",
                ">>\"@R@\\calls.txt\" echo %*",
                "if \"%~1\"==\"--version\" goto version",
                "if not \"%~1\"==\"tool\" goto unknown",
                "if \"%~2 %~3\"==\"dir --bin\" goto bin",
                "if \"%~2\"==\"dir\" goto tools",
                "if \"%~2\"==\"list\" goto list",
                "if \"%~2\"==\"uninstall\" goto uninstall",
                "if \"%~2\"==\"install\" goto install",
                ":unknown",
                "1>&2 echo error: not something this stand-in knows",
                "exit /b 9",
                ":version",
                "echo uv 0.9.9",
                "exit /b 0",
                ":bin",
                "echo @R@\\bin",
                "exit /b 0",
                ":tools",
                "echo @R@\\tools",
                "exit /b 0",
                ":list",
                "if exist \"@R@\\listed\" goto listed",
                "echo No tools installed",
                "exit /b 0",
                ":listed",
                "echo radialrig v0.1.16",
                "echo - radialrig.exe",
                "exit /b 0",
                ":uninstall",
                "if exist \"@R@\\stuck\" goto stuck",
                "if exist \"@R@\\strand\" goto strand",
                "rd /s /q \"@R@\\tools\\radialrig\" 2>nul",
                "if exist \"@R@\\tools\\radialrig\" goto held",
                "for %%n in (LAUNCHERS) do del /q \"@R@\\bin\\%%n.exe\" 2>nul",
                "for %%n in (LAUNCHERS) do if exist \"@R@\\bin\\%%n.exe\" goto held",
                "del /q \"@R@\\listed\"",
                "exit /b 0",
                ":stuck",
                "1>&2 echo error: failed to remove directory: Access is denied",
                "exit /b 2",
                ":strand",
                "del /q \"@R@\\strand\"",
                "del /q \"@R@\\listed\"",
                "if exist \"@R@\\stranger\" copy /y \"@R@\\stranger\" \"@R@\\bin\\radialrig.exe\" >nul",
                "1>&2 echo error: failed to remove directory: Access is denied",
                "exit /b 2",
                ":held",
                "1>&2 echo error: Access is denied",
                "exit /b 2",
                ":install",
                "if exist \"@R@\\broken\" goto broken",
                "for %%n in (LAUNCHERS) do if exist \"@R@\\bin\\%%n.exe\" goto exists",
                "mkdir \"@R@\\tools\\radialrig\" 2>nul",
                ">\"@R@\\bin\\radialrig.exe\" echo the new engine",
                ">\"@R@\\listed\" echo radialrig",
                "exit /b 0",
                ":exists",
                "1>&2 echo error: Executable already exists",
                "exit /b 2",
                ":broken",
                ">\"@R@\\bin\\radialrig.exe\" echo a half install",
                "1>&2 echo error: the install stopped part way",
                "exit /b 2",
            ]
            .join("\r\n")
            .replace("LAUNCHERS", RIG_LAUNCHERS)
            .replace("@R@", &root.to_string_lossy());
            let uv = root.join("uv.cmd");
            std::fs::write(&uv, script + "\r\n").expect("the stand-in");
            Rig { root, uv, tools, bin, data, running: Vec::new() }
        }

        /// A file the stand-in reads as a switch, or the launcher folder's
        /// own contents.
        fn put(&self, relative: &str, bytes: &[u8]) {
            let at = self.root.join(relative);
            std::fs::create_dir_all(at.parent().expect("a folder")).expect("a folder");
            std::fs::write(at, bytes).expect("a scratch file");
        }

        /// The old engine, as uv keeps it: its launcher and the entry.
        fn old_engine(&self) {
            self.put("bin\\radialrig.exe", b"the old engine");
            self.put("listed", b"radialrig");
        }

        /// A copy of the system's ping at `relative`, started so it waits
        /// about half a minute. Its own id comes back.
        fn start(&mut self, relative: &str) -> u32 {
            let system = std::env::var_os("SystemRoot").expect("the Windows folder");
            let program = PathBuf::from(system).join("System32").join("PING.EXE");
            let copy = self.root.join(relative);
            std::fs::create_dir_all(copy.parent().expect("a folder")).expect("a folder");
            std::fs::copy(&program, &copy).expect("a stand-in program");
            let child = std::process::Command::new(&copy)
                .args(["-n", "30", "127.0.0.1"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("the stand-in starts");
            let pid = child.id();
            self.running.push(child);
            pid
        }

        fn alive(&mut self, pid: u32) -> bool {
            self.running
                .iter_mut()
                .find(|child| child.id() == pid)
                .map(|child| matches!(child.try_wait(), Ok(None)))
                .unwrap_or(false)
        }

        /// What the stand-in was asked, one call per entry, by its first
        /// two words, read from the start of the log and then cleared.
        fn calls(&self) -> Vec<String> {
            let at = self.root.join("calls.txt");
            let said = std::fs::read_to_string(&at).unwrap_or_default();
            let _ = std::fs::remove_file(&at);
            said.lines()
                .map(|line| line.split_whitespace().take(2).collect::<Vec<_>>().join(" "))
                .filter(|line| !line.is_empty())
                .collect()
        }

        /// The first look, as `work` takes it before the swap begins.
        fn look(&self) -> Discovery {
            discover_with(config::pins(), Some(&self.data), Some(self.uv.clone()), None)
        }
    }

    #[cfg(windows)]
    impl Drop for Rig {
        fn drop(&mut self) {
            for child in &mut self.running {
                let _ = child.kill();
                let _ = child.wait();
            }
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    /// The package a swap test installs: a debug build's own override, so
    /// nothing is downloaded.
    #[cfg(windows)]
    fn overridden() -> config::Source {
        config::Source {
            value: "radialrig @ https://example.test/a.whl".to_string(),
            overridden: true,
        }
    }

    /// The calls a stand-in stop and a stand-in list were given, and what
    /// the list answers at the give-up.
    #[cfg(windows)]
    type Asked = std::cell::RefCell<Vec<(PathBuf, PathBuf)>>;

    #[test]
    #[cfg(windows)]
    fn a_stale_engine_found_on_the_opening_look_reads_as_the_old_engine_uv_keeps() {
        // The setup every swap test starts from, checked once: uv keeps an
        // entry, the launcher is there, and it answers no version this app
        // drives, so the engine is stale.
        let rig = Rig::new("setup");
        rig.old_engine();
        let found = rig.look();
        assert_eq!(found.refusal, None);
        assert_eq!(found.bin_dir.as_deref(), Some(rig.bin.as_path()));
        assert!(found.listed);
        assert_eq!(found.engine, Some(rig.bin.join(ENGINE_EXE)));
        assert!(!found.engine_current);
    }

    #[test]
    #[cfg(windows)]
    fn a_retry_reads_the_old_launcher_a_failed_uninstall_left_as_the_apps_own() {
        // The state BUG-0010 met: the uninstall failed after uv's entry
        // went, and the old engine's launchers are still in place. Read
        // with no knowledge of the swap, the launcher is another program's;
        // read with the old engine's launchers as the swap found them, it
        // is the app's own leftover, and no refusal stands.
        let rig = Rig::new("remains");
        rig.old_engine();
        rig.put("bin\\radialrig-read.exe", b"the old read launcher");
        let remains = Remains::read(&rig.bin);
        assert!(remains.claims(&rig.bin.join(ENGINE_EXE)));
        assert!(remains.claims(&rig.bin.join("radialrig-read.exe")));
        std::fs::remove_file(rig.root.join("listed")).expect("the entry goes");

        let blind = rig.look();
        assert_eq!(blind.refusal.map(|stop| stop.key), Some(strings::FAIL_FOREIGN_EXE));

        let pins = config::pins();
        let seen = discover_with(pins, Some(&rig.data), Some(rig.uv.clone()), Some(&remains));
        assert_eq!(seen.refusal, None);
        assert!(!seen.listed);
        assert!(seen.leftover);
        assert_eq!(seen.engine, None);
    }

    #[test]
    #[cfg(windows)]
    fn each_retry_looks_again_and_takes_the_leftover_path_when_uv_lists_no_engine() {
        let rig = Rig::new("retry");
        rig.old_engine();
        rig.put("bin\\radialrig-read.exe", b"the old read launcher");
        rig.put("strand", b"once");
        let found = rig.look();
        let _ = rig.calls();

        let shown = std::cell::RefCell::new(Vec::new());
        let step = |key: &'static str| shown.borrow_mut().push(key);
        let stops: Asked = Default::default();
        let stop = |tool: &Path, bin: &Path| {
            stops.borrow_mut().push((tool.to_path_buf(), bin.to_path_buf()));
            holders::Stopped::default()
        };
        let list = |_: &Path, _: &Path| Vec::new();
        let exe = swap_engine_with(
            &rig.uv, &found, &rig.bin, Some(&rig.data), &overridden(), &step, &stop, &list,
        )
        .expect("the second attempt replaces the engine");

        assert_eq!(exe, rig.bin.join(ENGINE_EXE));
        assert_eq!(std::fs::read_to_string(&exe).unwrap().trim(), "the new engine");
        // The old read launcher was the app's own engine's, so the retry
        // took it out before the install, which would refuse while it stood.
        assert!(!rig.bin.join("radialrig-read.exe").exists());
        assert_eq!(
            rig.calls(),
            vec![
                "tool dir",       // the tool folder, for the holders
                "tool uninstall", // attempt one, which strands
                "--version",      // discovery again, before attempt two
                "tool dir",
                "tool list",
                "tool install",   // attempt two, on the leftover path
                "tool dir",       // the command uv installed
            ]
        );
        // The holders were stopped before each attempt, in the folders uv
        // named, and the screen said what the swap does before it began.
        assert_eq!(*stops.borrow(), vec![(rig.tools.clone(), rig.bin.clone()); 2]);
        assert_eq!(*shown.borrow(), vec![strings::SWAP_RUNNING, strings::PROGRESS_RESOLVE]);
        assert!(!rig.data.join(INTENT_RECORD).exists());
    }

    #[test]
    #[cfg(windows)]
    fn a_launcher_the_app_never_installed_still_reads_as_another_programs() {
        // Between the attempts something else puts its own launcher where
        // the old one stood. Its bytes are not the old engine's, so the
        // retry's look refuses it, the swap stops there, and the file is
        // exactly as that program left it.
        let rig = Rig::new("stranger");
        rig.old_engine();
        rig.put("strand", b"once");
        rig.put("stranger", b"someone else's engine");
        let found = rig.look();
        let _ = rig.calls();

        let step = |_: &'static str| {};
        let stop = |_: &Path, _: &Path| holders::Stopped::default();
        let list = |_: &Path, _: &Path| Vec::new();
        let stopped = swap_engine_with(
            &rig.uv, &found, &rig.bin, Some(&rig.data), &overridden(), &step, &stop, &list,
        )
        .unwrap_err();

        assert_eq!(stopped.failure.key, strings::FAIL_FOREIGN_EXE);
        assert!(stopped.failure.detail.ends_with(ENGINE_EXE));
        assert_eq!(stopped.held, None);
        assert_eq!(
            std::fs::read(rig.bin.join(ENGINE_EXE)).unwrap(),
            b"someone else's engine"
        );
        assert!(!rig.calls().iter().any(|call| call == "tool install"));
        assert!(!rig.data.join(INTENT_RECORD).exists());

        // And a swap that began on a launcher uv did not name has no old
        // engine to know: the same file read against what it found is
        // nobody's remains.
        let remains = Remains::read(&rig.bin);
        assert!(!Remains::default().claims(&rig.bin.join(ENGINE_EXE)));
        assert!(remains.claims(&rig.bin.join(ENGINE_EXE)));
        rig.put("bin\\radialrig.exe", b"changed since");
        assert!(!remains.claims(&rig.bin.join(ENGINE_EXE)));
    }

    #[test]
    #[cfg(windows)]
    fn a_swap_that_gives_up_leaves_no_install_record_behind() {
        // Every install writes a launcher and then fails, which is the one
        // case the install step keeps its record for the next press. The
        // swap is the last press there is, so it takes the record away.
        let rig = Rig::new("record");
        rig.old_engine();
        rig.put("broken", b"always");
        let found = rig.look();
        let _ = rig.calls();

        let step = |_: &'static str| {};
        let stop = |_: &Path, _: &Path| holders::Stopped::default();
        let list = |_: &Path, _: &Path| Vec::new();
        let stopped = swap_engine_with(
            &rig.uv, &found, &rig.bin, Some(&rig.data), &overridden(), &step, &stop, &list,
        )
        .unwrap_err();

        assert_eq!(stopped.failure.key, strings::FAIL_RECOVERY);
        assert!(stopped.failure.detail.contains("the install stopped part way"));
        let installs = rig.calls().iter().filter(|call| *call == "tool install").count();
        assert_eq!(installs, SWAP_ATTEMPTS);
        assert!(!rig.data.join(INTENT_RECORD).exists(), "the swap left its record");
        assert!(!intent_present(Some(&rig.data), &rig.bin.join(ENGINE_EXE)));
    }

    #[test]
    #[cfg(windows)]
    fn after_three_attempts_the_swap_names_what_still_holds_the_engine() {
        let rig = Rig::new("named");
        rig.old_engine();
        rig.put("stuck", b"always");
        let found = rig.look();
        let _ = rig.calls();

        let step = |_: &'static str| {};
        let stops: Asked = Default::default();
        let stop = |tool: &Path, bin: &Path| {
            stops.borrow_mut().push((tool.to_path_buf(), bin.to_path_buf()));
            holders::Stopped::default()
        };
        let lists: Asked = Default::default();
        let left = vec![
            holders::Holder { pid: 11, image: rig.tools.join("radialrig\\Scripts\\python.exe") },
            holders::Holder { pid: 12, image: rig.bin.join("radialrig-read.exe") },
            holders::Holder { pid: 13, image: rig.tools.join("radialrig\\Scripts\\python.exe") },
        ];
        let list = |tool: &Path, bin: &Path| {
            lists.borrow_mut().push((tool.to_path_buf(), bin.to_path_buf()));
            left.clone()
        };
        let stopped = swap_engine_with(
            &rig.uv, &found, &rig.bin, Some(&rig.data), &overridden(), &step, &stop, &list,
        )
        .unwrap_err();

        let calls = rig.calls();
        let uninstalls = calls.iter().filter(|call| *call == "tool uninstall").count();
        assert_eq!(uninstalls, SWAP_ATTEMPTS, "{calls:?}");
        assert_eq!(calls.iter().filter(|call| *call == "--version").count(), SWAP_ATTEMPTS - 1);
        assert_eq!(stops.borrow().len(), SWAP_ATTEMPTS);
        assert_eq!(*lists.borrow(), vec![(rig.tools.clone(), rig.bin.clone())]);
        assert_eq!(
            stopped.failure,
            Slot::of(strings::FAIL_RECOVERY, "2: error: failed to remove directory: Access is denied")
        );
        // Each program once, by its file name, in a steady order.
        assert_eq!(
            stopped.held,
            Some(Slot::of(strings::SWAP_HELD, "python.exe, radialrig-read.exe"))
        );
        assert!(strings::retryable(stopped.failure.key));
    }

    #[test]
    #[cfg(windows)]
    fn after_three_attempts_with_nothing_running_the_swap_says_so() {
        let rig = Rig::new("none");
        rig.old_engine();
        rig.put("stuck", b"always");
        let found = rig.look();

        let step = |_: &'static str| {};
        let stop = |_: &Path, _: &Path| holders::Stopped::default();
        let list = |_: &Path, _: &Path| Vec::new();
        let stopped = swap_engine_with(
            &rig.uv, &found, &rig.bin, Some(&rig.data), &overridden(), &step, &stop, &list,
        )
        .unwrap_err();

        assert_eq!(stopped.failure.key, strings::FAIL_RECOVERY);
        assert_eq!(stopped.held, Some(Slot::bare(strings::SWAP_HELD_NONE)));
        let uninstalls = rig.calls().iter().filter(|call| *call == "tool uninstall").count();
        assert_eq!(uninstalls, SWAP_ATTEMPTS);
    }

    #[test]
    #[cfg(windows)]
    fn the_swap_stops_what_holds_the_engine_before_the_uninstall_and_leaves_a_bystander() {
        // Real programs, started from copies in the scratch folder: one
        // inside the engine's folder and one running as a launcher, which
        // an uninstall cannot take out from under them, and a bystander
        // in a sibling folder sharing the prefix (Invariant 3).
        let mut rig = Rig::new("holders");
        rig.old_engine();
        let inside = rig.start("tools\\radialrig\\Scripts\\python.exe");
        let launcher = rig.start("bin\\radialrig-read.exe");
        let bystander = rig.start("tools\\radialrig-old\\Scripts\\python.exe");
        let found = rig.look();
        let _ = rig.calls();

        let step = |_: &'static str| {};
        let exe = swap_engine(&rig.uv, &found, &rig.bin, Some(&rig.data), &overridden(), &step)
            .expect("one attempt replaces the engine once its holders are stopped");

        assert_eq!(std::fs::read_to_string(&exe).unwrap().trim(), "the new engine");
        assert!(!rig.alive(inside));
        assert!(!rig.alive(launcher));
        assert!(rig.alive(bystander), "a bystander was stopped");
        let uninstalls = rig.calls().iter().filter(|call| *call == "tool uninstall").count();
        assert_eq!(uninstalls, 1);
    }

    #[test]
    fn a_uv_that_cannot_be_started_names_no_tool_folder() {
        let mut root = std::env::temp_dir();
        root.push(format!("radialrig-tooldir-{}-absent", std::process::id()));
        let uv = root.join("no-such-uv.exe");
        let stop = tool_dir(&uv).unwrap_err();
        assert_eq!(stop.key, strings::FAIL_TOOL_DIR);
        assert!(!stop.detail.is_empty(), "the start's own error is the detail");
        assert_eq!(tool_bin_dir(&uv), Err(stop));
    }

    /// The expected block set, with the Path block appended on the one
    /// platform whose Path step is a write the gate must name.
    fn with_path_block(mut blocks: Vec<&'static str>) -> Vec<&'static str> {
        if cfg!(windows) {
            blocks.push(strings::GATE_PATH);
        }
        blocks
    }

    #[test]
    fn the_gate_names_the_steps_that_will_run_and_no_others() {
        let pins = pins();
        let _ = &pins;

        // A machine with nothing: every block — and on Windows alone
        // that includes the Path entry, because on Linux the Path step
        // writes nothing and so is never named.
        let bare = Discovery::default();
        assert_eq!(
            plan(&bare),
            with_path_block(vec![
                strings::GATE_UV,
                strings::GATE_PYTHON,
                strings::GATE_ENGINE
            ])
        );

        // A machine that already carries uv: the uv block does not run,
        // so it is not named.
        let mut carries_uv = Discovery { uv: Some(PathBuf::from("C:\\u\\uv.exe")), ..bare.clone() };
        carries_uv.uv_version = Some("0.12.5".to_string());
        assert_eq!(
            plan(&carries_uv),
            with_path_block(vec![strings::GATE_PYTHON, strings::GATE_ENGINE])
        );

        // And with the directory already resolving, no Path entry either.
        let settled = Discovery { on_path: true, ..carries_uv.clone() };
        assert_eq!(plan(&settled), vec![strings::GATE_PYTHON, strings::GATE_ENGINE]);

        // A machine that carries a current engine changes nothing at all.
        let done = Discovery {
            engine: Some(PathBuf::from("C:\\u\\radialrig.exe")),
            engine_current: true,
            listed: true,
            ..settled.clone()
        };
        assert!(plan(&done).is_empty());

        // An engine uv keeps an entry for that answers an older version is
        // not a block: the opening look sends it to the engine swap, which
        // needs no press. The gate names nothing that takes anything out.
        let old = Discovery { engine_current: false, ..done.clone() };
        assert!(plan(&old).is_empty());

        // A command this app's own install step left behind is installed
        // over, so the steps that do that are named.
        let stranded = Discovery {
            engine: None,
            engine_current: false,
            listed: false,
            intent: true,
            leftover: true,
            ..done.clone()
        };
        assert_eq!(plan(&stranded), vec![strings::GATE_PYTHON, strings::GATE_ENGINE]);
    }

    #[test]
    fn a_step_that_was_never_named_is_the_one_thing_a_press_does_not_carry() {
        let named = vec![strings::GATE_PYTHON, strings::GATE_ENGINE];

        // Less than was named is not more than was named: a step the
        // machine no longer needs is skipped, and nothing goes back.
        assert!(unnamed(&named, &[strings::GATE_ENGINE]).is_empty());
        assert!(unnamed(&named, &[]).is_empty());
        assert!(unnamed(&named, &named).is_empty());

        // A step that is needed now and was not on the list the reader
        // read is the branch that goes back to the gate.
        assert_eq!(
            unnamed(&named, &[strings::GATE_UV, strings::GATE_ENGINE]),
            vec![strings::GATE_UV]
        );
        assert_eq!(
            unnamed(&[], &[strings::GATE_UV, strings::GATE_PATH]),
            vec![strings::GATE_UV, strings::GATE_PATH]
        );
    }

    #[test]
    fn a_path_block_is_never_named_on_its_own() {
        // The entry exists to make the commands the gate lists resolve.
        // With no step running there is no command to make resolve.
        let settled = Discovery {
            uv: Some(PathBuf::from("C:\\u\\uv.exe")),
            engine: Some(PathBuf::from("C:\\u\\radialrig.exe")),
            engine_current: true,
            on_path: false,
            ..Discovery::default()
        };
        assert!(plan(&settled).is_empty());
    }

    #[test]
    fn the_registry_value_is_the_one_oracle_the_gate_and_the_writer_share() {
        // Whatever the user's own value says, a directory that is not in
        // it is not in it, and the answer comes from the same read the
        // write consults rather than from this process's inherited PATH.
        assert!(!user_path_has(Path::new("C:\\nowhere-at-all-1a2b3c")));
        if let Ok((_, value)) = read_user_path() {
            for part in segments(&value) {
                assert!(
                    user_path_has(Path::new(part)),
                    "{part} is on the value and was not recognised"
                );
            }
        }
    }

    #[test]
    fn the_documented_location_is_the_one_place_a_uv_is_looked_for() {
        if let Some(uv) = documented_uv() {
            assert!(uv.is_absolute());
            assert!(uv.ends_with(UV_EXE));
            let said = uv.to_string_lossy().to_ascii_lowercase();
            #[cfg(windows)]
            assert!(said.contains(".local\\bin\\uv.exe"));
            #[cfg(not(windows))]
            assert!(said.ends_with(".local/bin/uv"));
        }
    }

    #[test]
    fn a_posix_path_value_is_read_by_exact_segments() {
        let value = "/usr/bin:/home/a/.local/bin:/opt/x/";
        assert!(path_env_has(value, "/home/a/.local/bin"));
        // A trailing separator is forgiven on either side, because both
        // spell one directory.
        assert!(path_env_has(value, "/home/a/.local/bin/"));
        assert!(path_env_has(value, "/opt/x"));

        // Case matters on this platform, a prefix is not the directory,
        // and an empty segment answers for nothing.
        assert!(!path_env_has(value, "/home/a/.LOCAL/bin"));
        assert!(!path_env_has(value, "/home/a/.local"));
        assert!(!path_env_has(value, "/home/a/.local/bin/more"));
        assert!(!path_env_has("", "/home/a/.local/bin"));
        assert!(!path_env_has(value, ""));
        assert!(!path_env_has(":::", "/"));
    }

    #[test]
    fn the_path_report_says_what_the_read_found_and_no_more() {
        let dir = Path::new("/home/a/.local/bin");
        let on = path_report_of(dir, true);
        assert_eq!(on.key, strings::PATH_REPORT_ON);
        assert_eq!(on.detail, "/home/a/.local/bin");
        let off = path_report_of(dir, false);
        assert_eq!(off.key, strings::PATH_REPORT_OFF);
        assert_eq!(off.detail, "/home/a/.local/bin");
    }

    #[test]
    fn a_stop_carries_its_key_and_at_most_one_value() {
        let bare = Slot::bare(strings::FAIL_DOWNLOAD);
        assert_eq!(bare.detail, "");
        let named = Slot::of(strings::FAIL_FOREIGN_EXE, "C:\\a\\radialrig.exe");
        assert_eq!(named.key, strings::FAIL_FOREIGN_EXE);
        assert!(!strings::retryable(named.key));
    }

    #[test]
    fn an_old_engine_stop_names_the_version_the_engine_gave() {
        // BUG-0017. The installed version is the stop's one value; the
        // version this app needs rides on every picture instead.
        let named = engine_old(Some("0.1.14"));
        assert_eq!(named.key, strings::FAIL_ENGINE_OLD);
        assert_eq!(named.detail, "0.1.14");

        // An engine that gave no version gets the sentence that says so,
        // never the one that would name a blank.
        let unread = engine_old(None);
        assert_eq!(unread.key, strings::FAIL_ENGINE_UNREAD);
        assert_eq!(unread.detail, "");
        assert_eq!(engine_old(Some("  ")).key, strings::FAIL_ENGINE_UNREAD);

        // Both pair with the one press, which runs the engine swap.
        assert!(strings::retryable(strings::FAIL_ENGINE_OLD));
        assert!(strings::retryable(strings::FAIL_ENGINE_UNREAD));
    }

    #[test]
    fn the_engine_the_swap_replaces_is_one_uv_keeps_that_is_not_current() {
        let old = Discovery {
            engine: Some(PathBuf::from("C:\\u\\radialrig.exe")),
            engine_current: false,
            listed: true,
            ..Discovery::default()
        };
        assert!(stale_engine(&old));

        // A current engine, or none at all, is not one to replace.
        assert!(!stale_engine(&Discovery { engine_current: true, ..old.clone() }));
        assert!(!stale_engine(&Discovery { engine: None, ..old.clone() }));
    }

    #[test]
    fn a_stale_engine_now_leads_to_the_swap_not_a_stop() {
        // PLAN-0021 phase 2, prescription 4 (Decided 7): replacing an old
        // engine needs no press. An engine uv keeps that answers an older
        // version, or none, is replaced; nothing stops in front of it.
        let old = Discovery {
            uv: Some(PathBuf::from("C:\\u\\uv.exe")),
            engine: Some(PathBuf::from("C:\\u\\radialrig.exe")),
            engine_current: false,
            listed: true,
            on_path: true,
            ..Discovery::default()
        };
        let blocks = plan(&old);
        assert!(blocks.is_empty());
        assert_eq!(opening(&old, &blocks), Opening::Swap);

        // A current engine runs, a refusal still stops before anything
        // changes, and a machine that needs steps still asks at the gate.
        let current = Discovery { engine_current: true, ..old.clone() };
        assert_eq!(opening(&current, &plan(&current)), Opening::Run);
        let refused = Discovery {
            refusal: Some(Slot::of(strings::FAIL_FOREIGN_EXE, "C:\\u\\radialrig.exe")),
            ..old.clone()
        };
        assert_eq!(
            opening(&refused, &[]),
            Opening::Stop(Slot::of(strings::FAIL_FOREIGN_EXE, "C:\\u\\radialrig.exe"))
        );
        let bare = Discovery::default();
        assert_eq!(opening(&bare, &plan(&bare)), Opening::Gate);
    }

    #[test]
    fn every_picture_carries_the_engine_version_this_app_needs() {
        assert_eq!(View::empty().engine_needed, pins().engine_min_version);
    }

    #[test]
    fn a_uv_that_is_already_here_is_the_one_that_is_used() {
        // The one branch of `provide_uv` that reaches nothing: a machine
        // that already carries uv downloads no archive at all. The other
        // branch is the network, which no unit test runs.
        let nothing = |_key: &'static str| {};
        let pins = pins();
        let carried = PathBuf::from("C:\\u\\uv.exe");
        let has_uv = Discovery { uv: Some(carried.clone()), ..Discovery::default() };
        assert_eq!(provide_uv(&has_uv, &pins, &nothing).unwrap(), carried);
    }

    // --- D1: the two members, and the digest that gates them -----------

    #[test]
    fn exactly_two_members_are_taken_and_the_third_is_left_where_it_is() {
        assert_eq!(zip_member_target("uv.exe"), Some(ZIP_UV));
        assert_eq!(zip_member_target("uvx.exe"), Some(ZIP_UVX));
        // The archive's third program, which is the one with a recorded
        // quarantine history. It is named in the archive and never here.
        assert_eq!(zip_member_target("uvw.exe"), None);
        assert_eq!(zip_member_target("UVW.EXE"), None);

        // The names are folded the way the file system folds them, so a
        // shouted member is still the member it names.
        assert_eq!(zip_member_target("UV.EXE"), Some(ZIP_UV));
        assert_eq!(zip_member_target("Uvx.Exe"), Some(ZIP_UVX));

        // A name with a path in it is never a member, which is what keeps
        // an extraction inside the directory it was aimed at.
        assert_eq!(zip_member_target("bin/uv.exe"), None);
        assert_eq!(zip_member_target("..\\uv.exe"), None);
        assert_eq!(zip_member_target("\\uv.exe"), None);
        assert_eq!(zip_member_target("C:\\other\\uv.exe"), None);

        // And nothing else in an archive is anything to this shell.
        assert_eq!(zip_member_target("uv"), None);
        assert_eq!(zip_member_target("uv.exe.bak"), None);
        assert_eq!(zip_member_target("README.md"), None);
        assert_eq!(zip_member_target(""), None);
    }

    #[test]
    fn the_tar_members_are_the_two_nested_names_and_nothing_else() {
        assert_eq!(
            tar_member_target("uv-x86_64-unknown-linux-gnu/uv"),
            Some(TAR_UV)
        );
        assert_eq!(
            tar_member_target("uv-x86_64-unknown-linux-gnu/uvx"),
            Some(TAR_UVX)
        );

        // Exact and case-sensitive: this reader writes to file systems
        // that fold nothing.
        assert_eq!(tar_member_target("uv-x86_64-unknown-linux-gnu/UV"), None);
        assert_eq!(tar_member_target("UV-X86_64-UNKNOWN-LINUX-GNU/uv"), None);

        // A bare name, another directory, a deeper nesting, the
        // directory itself: none of them is a member.
        assert_eq!(tar_member_target("uv"), None);
        assert_eq!(tar_member_target("../uv"), None);
        assert_eq!(tar_member_target("other/uv"), None);
        assert_eq!(tar_member_target("uv-x86_64-unknown-linux-gnu/bin/uv"), None);
        assert_eq!(tar_member_target("uv-x86_64-unknown-linux-gnu/"), None);
        assert_eq!(tar_member_target("uv-x86_64-unknown-linux-gnu/uvw"), None);
        assert_eq!(tar_member_target(""), None);
    }

    #[test]
    fn bytes_that_do_not_match_the_pin_stop_and_say_so_once() {
        // The digest of nothing at all, which every sha256 agrees on.
        let empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        assert_eq!(digest(b""), empty);
        assert_eq!(digest(b"abc").len(), 64);

        assert_eq!(verify(b"", empty), Ok(()));
        // The pin is written lowercase and read either way, and the
        // surrounding space of a hand-edited file is not a mismatch.
        assert_eq!(verify(b"", &empty.to_ascii_uppercase()), Ok(()));
        assert_eq!(verify(b"", &format!(" {empty} ")), Ok(()));

        // One byte different is a different archive, and the stop it
        // lands on is the one a second press cannot move.
        let stop = verify(b"x", empty).unwrap_err();
        assert_eq!(stop.key, strings::FAIL_CHECKSUM);
        assert_eq!(stop.detail, "");
        assert!(!strings::retryable(stop.key));

        // A pin that is not a digest at all matches nothing.
        assert!(verify(b"", "").is_err());
        assert!(verify(b"", &empty[..63]).is_err());
    }

    /// An archive shaped like the one D1 downloads: the three programs at
    /// the top, deflated, which is how the pinned archive stores them.
    fn an_archive(members: &[(&str, &[u8])]) -> Vec<u8> {
        use zip::write::SimpleFileOptions;
        use std::io::Write;

        let mut writer = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
        let how = SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        for (name, bytes) in members {
            writer.start_file(*name, how).expect("a member");
            writer.write_all(bytes).expect("the member's bytes");
        }
        writer.finish().expect("the archive").into_inner()
    }

    #[test]
    fn only_the_two_members_land_and_a_file_already_there_is_left_alone() {
        let archive = an_archive(&[
            ("uv.exe", b"the uv program"),
            ("uvx.exe", b"the uvx program"),
            ("uvw.exe", b"the one with the quarantine history"),
        ]);

        let mut into = std::env::temp_dir();
        into.push(format!("radialrig-extract-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&into);

        // The directory does not exist yet, which is the fresh-machine
        // case: the extraction makes it.
        let uv = extract_zip(&archive, &into).expect("the two members");
        assert_eq!(uv, into.join("uv.exe"));
        assert_eq!(std::fs::read(&uv).unwrap(), b"the uv program");
        assert_eq!(
            std::fs::read(into.join("uvx.exe")).unwrap(),
            b"the uvx program"
        );
        // The third program is named in the archive and is not written.
        assert!(!into.join("uvw.exe").exists());
        let landed: Vec<String> = std::fs::read_dir(&into)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        assert_eq!(landed.len(), 2, "{landed:?}");

        // Constraint 5: a file that is already there is a file this app
        // did not write, and a second pass leaves its bytes exactly as
        // they were rather than replacing them.
        std::fs::write(into.join("uvx.exe"), b"someone else's uvx").unwrap();
        std::fs::remove_file(&uv).unwrap();
        let uv = extract_zip(&archive, &into).expect("the two members again");
        assert_eq!(std::fs::read(&uv).unwrap(), b"the uv program");
        assert_eq!(
            std::fs::read(into.join("uvx.exe")).unwrap(),
            b"someone else's uvx"
        );

        // An archive that is not the pinned one does not quietly do half
        // the job: a missing member is a stop.
        let short = an_archive(&[("uv.exe", b"only one")]);
        let mut elsewhere = std::env::temp_dir();
        elsewhere.push(format!("radialrig-extract-short-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&elsewhere);
        assert_eq!(
            extract_zip(&short, &elsewhere).unwrap_err().key,
            strings::FAIL_EXTRACT
        );

        // And bytes that are not an archive at all are the same stop.
        assert_eq!(
            extract_zip(b"not an archive", &elsewhere).unwrap_err().key,
            strings::FAIL_EXTRACT
        );

        let _ = std::fs::remove_dir_all(&into);
        let _ = std::fs::remove_dir_all(&elsewhere);
    }

    #[test]
    fn a_member_that_names_a_path_is_never_written_anywhere() {
        // The one shape that could write outside the directory the
        // extraction was aimed at. It is not one of the two names, so it
        // is skipped, and skipping every member is a stop rather than a
        // silent success.
        let archive = an_archive(&[
            ("..\\uv.exe", b"escaped"),
            ("bin/uv.exe", b"nested"),
            ("uvw.exe", b"the third program"),
        ]);
        let mut into = std::env::temp_dir();
        into.push(format!("radialrig-extract-escape-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&into);

        assert_eq!(
            extract_zip(&archive, &into).unwrap_err().key,
            strings::FAIL_EXTRACT
        );
        assert!(!into.join("uv.exe").exists());
        assert!(!into.join("uvw.exe").exists());
        assert!(!into.parent().unwrap().join("uv.exe").exists());

        let _ = std::fs::remove_dir_all(&into);
    }

    /// An archive shaped like the pinned Linux one: one directory, the
    /// programs nested under it, gzip over tar.
    fn a_tar_archive(members: &[(&str, &[u8])]) -> Vec<u8> {
        let encoder =
            flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        let mut builder = tar::Builder::new(encoder);
        for (name, bytes) in members {
            let mut header = tar::Header::new_gnu();
            header.set_size(bytes.len() as u64);
            header.set_mode(0o755);
            header.set_cksum();
            builder
                .append_data(&mut header, name, *bytes)
                .expect("a member");
        }
        builder
            .into_inner()
            .expect("the tar")
            .finish()
            .expect("the archive")
    }

    #[test]
    fn the_tar_reader_lands_the_two_nested_members_and_no_others() {
        let archive = a_tar_archive(&[
            ("uv-x86_64-unknown-linux-gnu/uv", b"the uv program"),
            ("uv-x86_64-unknown-linux-gnu/uvx", b"the uvx program"),
            ("uv-x86_64-unknown-linux-gnu/extra", b"nothing this shell takes"),
            ("uv", b"a bare name at the top"),
        ]);

        let mut into = std::env::temp_dir();
        into.push(format!("radialrig-tar-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&into);

        // The fresh-machine case: the directory is made, the two members
        // come out by their nested names, flattened to the two programs.
        let uv = extract_tar(&archive, &into).expect("the two members");
        assert_eq!(uv, into.join("uv"));
        assert_eq!(std::fs::read(&uv).unwrap(), b"the uv program");
        assert_eq!(std::fs::read(into.join("uvx")).unwrap(), b"the uvx program");
        let landed: Vec<String> = std::fs::read_dir(&into)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        assert_eq!(landed.len(), 2, "{landed:?}");

        // The execute bit is set, on the platform that has one.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            for name in TAR_MEMBERS {
                let mode = std::fs::metadata(into.join(name))
                    .expect(name)
                    .permissions()
                    .mode();
                assert!(mode & 0o111 != 0, "{name} is not executable");
            }
        }

        // Constraint 5: a file that is already there is left exactly as
        // it is on a second pass.
        std::fs::write(into.join("uvx"), b"someone else's uvx").unwrap();
        std::fs::remove_file(&uv).unwrap();
        let uv = extract_tar(&archive, &into).expect("the two members again");
        assert_eq!(std::fs::read(&uv).unwrap(), b"the uv program");
        assert_eq!(
            std::fs::read(into.join("uvx")).unwrap(),
            b"someone else's uvx"
        );

        // A missing member is a stop, and bytes that are not a gzip tar
        // at all are the same stop.
        let short = a_tar_archive(&[("uv-x86_64-unknown-linux-gnu/uv", b"only one")]);
        let mut elsewhere = std::env::temp_dir();
        elsewhere.push(format!("radialrig-tar-short-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&elsewhere);
        assert_eq!(
            extract_tar(&short, &elsewhere).unwrap_err().key,
            strings::FAIL_EXTRACT
        );
        assert_eq!(
            extract_tar(b"not an archive", &elsewhere).unwrap_err().key,
            strings::FAIL_EXTRACT
        );

        let _ = std::fs::remove_dir_all(&into);
        let _ = std::fs::remove_dir_all(&elsewhere);
    }

    #[test]
    fn a_tar_member_outside_the_named_directory_is_never_written() {
        // Names that are not the two exact nested names are skipped, and
        // skipping every member is a stop rather than a silent success.
        // A `..` name never reaches the writer for the same reason: only
        // the two exact names are ever written, which the matcher test
        // above pins, and the tar builder itself refuses to author one
        // here.
        let archive = a_tar_archive(&[
            ("uv", b"a bare name at the top"),
            ("other/uv", b"a stranger's directory"),
            ("uv-x86_64-unknown-linux-gnu/uvw", b"no third program exists"),
        ]);
        let mut into = std::env::temp_dir();
        into.push(format!("radialrig-tar-escape-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&into);

        assert_eq!(
            extract_tar(&archive, &into).unwrap_err().key,
            strings::FAIL_EXTRACT
        );
        assert!(!into.join("uv").exists());
        assert!(!into.join("uvw").exists());
        assert!(!into.parent().unwrap().join("uv").exists());

        let _ = std::fs::remove_dir_all(&into);
    }

    #[test]
    fn the_shipped_pin_is_the_shape_the_digest_is_compared_against() {
        let pins = pins();
        let sha = pins.archive_sha256();
        assert_eq!(sha.len(), digest(b"").len());
        assert_eq!(sha, sha.to_ascii_lowercase());
    }

    // --- D2: the Path segment and its record ---------------------------

    #[test]
    fn a_segment_already_on_the_value_is_recognised_however_it_is_spelled() {
        let value = "C:\\Windows;C:\\Users\\a\\.local\\bin;%JAVA_HOME%\\bin";
        assert!(segment_present(value, "C:\\Users\\a\\.local\\bin"));

        // Windows resolves a path case-insensitively and ignores a
        // trailing separator, so these are one entry and not four. A
        // stricter reading would add a second copy of the same directory
        // every time this ran.
        assert!(segment_present(value, "c:\\users\\a\\.local\\bin"));
        assert!(segment_present(value, "C:\\USERS\\A\\.LOCAL\\BIN"));
        assert!(segment_present(value, "C:\\Users\\a\\.local\\bin\\"));
        assert!(segment_present("\"C:\\Users\\a\\.local\\bin\"", "C:\\Users\\a\\.local\\bin"));
        assert!(segment_present("C:\\Windows; C:\\a ;C:\\b", "C:\\a"));

        // A different directory is a different directory, and a prefix of
        // one is not one.
        assert!(!segment_present(value, "C:\\Users\\b\\.local\\bin"));
        assert!(!segment_present(value, "C:\\Users\\a\\.local"));
        assert!(!segment_present(value, "C:\\Users\\a\\.local\\bin\\more"));
        assert!(!segment_present("", "C:\\a"));
        assert!(!segment_present(value, ""));
        assert!(!segment_present(value, "   "));

        // A bare drive keeps its separator: `C:` and `C:\` are two places.
        assert!(!segment_present("C:", "C:\\"));
        assert!(segment_present("C:\\", "C:\\"));
    }

    #[test]
    fn the_new_value_puts_the_directory_in_front_and_changes_nothing_else() {
        // The user's own value comes through byte for byte, expandable
        // references included: nothing here bakes one to its expansion.
        let value = "%USERPROFILE%\\bin;C:\\Windows";
        assert_eq!(prepended(value, "C:\\a"), "C:\\a;%USERPROFILE%\\bin;C:\\Windows");
        assert!(prepended(value, "C:\\a").ends_with(value));
        assert_eq!(prepended("", "C:\\a"), "C:\\a");

        // And the directory it just put in front is on the value now.
        assert!(segment_present(&prepended(value, "C:\\a"), "C:\\a"));
        assert!(segment_present(&prepended("", "C:\\a"), "C:\\a"));
    }

    #[test]
    fn what_was_written_comes_back_out_of_the_record_unchanged() {
        // The removal flow refuses unless the value minus the segment
        // matches what was recorded, so a record that cannot survive its
        // own round trip would turn every removal into a refusal.
        let awkward = [
            "C:\\Users\\a\\.local\\bin;%USERPROFILE%\\bin;C:\\Windows",
            "C:\\Users\\it's a folder\\bin;C:\\Windows",
            "C:\\Users\\naïve\\日本語\\bin;C:\\Windows",
            "C:\\a\\\\double\\back;C:\\Windows",
            "\"C:\\quoted path\";C:\\Windows",
            "C:\\a",
        ];
        for value in awkward {
            for kind in [1u32, 2u32] {
                let record = PathRecord {
                    kind,
                    segment: "C:\\Users\\a\\.local\\bin".to_string(),
                    value: value.to_string(),
                };
                let read = parse_record(&record_text(&record)).expect(value);
                assert_eq!(read, record, "{value}");
                assert_eq!(read.value, value);
                assert_eq!(read.kind, kind);
            }
        }
        assert_eq!(parse_record(""), None);
        assert_eq!(parse_record("{}"), None);
        assert_eq!(parse_record("not a record at all"), None);
    }

    #[test]
    fn the_record_holds_the_value_that_was_actually_written() {
        let value = "%USERPROFILE%\\bin;C:\\Windows";
        let dir = "C:\\Users\\a\\.local\\bin";
        let written = prepended(value, dir);
        let record = PathRecord {
            kind: 2,
            segment: dir.to_string(),
            value: written.clone(),
        };
        let read = parse_record(&record_text(&record)).expect("the record");
        assert_eq!(read.value, written);
        assert!(segment_present(&read.value, &read.segment));
        // The value minus the one segment is the value that was there
        // before, which is what the removal flow compares against.
        assert_eq!(read.value.strip_prefix(&format!("{dir};")), Some(value));
    }

    // --- D4: the install command and the stop in front of it -----------

    #[test]
    fn the_install_command_never_forces_and_never_names_a_bare_package() {
        let pins = pins();
        let args = install_args(&pins.python_pin, &pins.package_source);
        assert_eq!(
            args,
            vec![
                "tool",
                "install",
                "--python",
                pins.python_pin.as_str(),
                pins.package_source.as_str()
            ]
        );

        // The one argument that is never passed. A radialrig.exe this app
        // did not write is a stop, not a file to write over.
        assert!(!args.iter().any(|arg| arg.starts_with("--force")));
        assert!(!args.contains(&"-f"));

        // The package is the last word, and it is an address or the named
        // form that carries one — never a name a public index would answer.
        let package = args.last().copied().expect("the package");
        assert!(config::package_source(package) || config::local_wheel(package));
        assert!(!config::package_source("radialrig"));

        // The shape the install actually takes now: the wheel this app
        // downloaded and proved, named by its own path on this machine.
        // The command is built the one way whichever it is given, and the
        // local file passes the same argument rule the address passes.
        let mut made = std::env::temp_dir();
        made.push(format!(
            "radialrig-{}-args-py3-none-any.whl",
            std::process::id()
        ));
        std::fs::write(&made, b"").expect("a wheel to point at");
        let local = made.to_string_lossy().to_string();
        let args = install_args(&pins.python_pin, &local);
        assert_eq!(
            args,
            vec![
                "tool",
                "install",
                "--python",
                pins.python_pin.as_str(),
                local.as_str()
            ]
        );
        let package = args.last().copied().expect("the package");
        assert!(config::local_wheel(package));
        assert!(!args.iter().any(|arg| arg.starts_with("--force")));
        std::fs::remove_file(&made).ok();

        // The removal is the other half of the same pair, and it names the
        // tool entry uv keeps rather than a file on disk.
        assert_eq!(uninstall_args(), vec!["tool", "uninstall", "radialrig"]);
        assert!(!uninstall_args().iter().any(|arg| arg.starts_with("--")));
    }

    #[test]
    fn the_wheels_bytes_are_proved_against_the_wheels_own_pin() {
        let bytes = b"the pinned wheel";
        let pin = digest(bytes);
        assert_eq!(verify_package(bytes, &pin), Ok(()));
        // The pin is compared without regard to case, and with the
        // whitespace a file might carry around it trimmed off.
        assert_eq!(verify_package(bytes, &pin.to_ascii_uppercase()), Ok(()));
        assert_eq!(verify_package(bytes, &format!(" {pin}\n")), Ok(()));

        // One byte different is a different wheel, and the stop says so.
        let mut nearly = bytes.to_vec();
        nearly[0] ^= 1;
        let stop = verify_package(&nearly, &pin).unwrap_err();
        assert_eq!(stop.key, strings::FAIL_PACKAGE_CHECKSUM);
        assert_eq!(stop.detail, "");
        // The same reason the archive's mismatch cannot be pressed past:
        // the same address answers with the same bytes.
        assert!(!strings::retryable(stop.key));

        let stop = verify_package(b"something else entirely", &pin).unwrap_err();
        assert_eq!(stop.key, strings::FAIL_PACKAGE_CHECKSUM);

        // The archive's own mismatch keeps its own sentence: a reader
        // told the archive failed when the wheel did is told the wrong
        // thing about their machine.
        assert_eq!(verify(&nearly, &pin).unwrap_err().key, strings::FAIL_CHECKSUM);

        // And the shipped pin is the shape this comparison is made in.
        let pins = pins();
        assert_eq!(pins.package_sha256.len(), digest(b"").len());
        assert_eq!(pins.package_sha256, pins.package_sha256.to_ascii_lowercase());
    }

    #[test]
    fn only_a_plain_wheel_name_comes_off_the_pinned_address() {
        let pins = pins();
        let address = config::address_of(&pins.package_source);
        let name = wheel_name(address).expect("the pinned address ends in a wheel");
        assert!(address.ends_with(&name));
        assert!(name.ends_with(WHEEL_SUFFIX));
        assert!(!name.contains('/'));

        assert_eq!(
            wheel_name("https://a.test/b/c-1.0-py3-none-any.whl").as_deref(),
            Some("c-1.0-py3-none-any.whl")
        );

        // Nothing that could steer a write out of the app's own
        // directory is ever taken as a file name.
        for said in [
            "https://a.test/b/",
            "https://a.test/b/c.zip",
            "https://a.test/b/.whl",
            "https://a.test/b/..whl",
            "https://a.test/b/c:x.whl",
            "https://a.test/b/c\\x.whl",
            "",
        ] {
            assert_eq!(wheel_name(said), None, "{said}");
        }
    }

    #[test]
    fn a_staged_wheel_is_a_file_that_goes_when_the_install_has_answered() {
        let mut at = std::env::temp_dir();
        at.push(format!("radialrig-staged-{}.whl", std::process::id()));
        std::fs::write(&at, b"the pinned wheel").expect("a wheel to stage");
        {
            let staged = Staged { at: at.clone() };
            assert!(staged.at.is_file());
        }
        // Leaving the scope is what takes it away, so every path out of
        // the install step takes it: the stop as well as the success.
        assert!(!at.exists());

        // A file that is already gone is not a second failure on the way
        // out; the removal is the app taking back its own leaving.
        drop(Staged { at: at.clone() });
        assert!(!at.exists());
    }

    #[test]
    fn an_engine_uv_does_not_keep_is_one_this_app_leaves_alone() {
        let listed = "radialrig v0.1.0\n- radialrig.exe\nruff v0.14.0\n- ruff.exe\n";
        assert!(lists_tool(listed, ENGINE_TOOL));
        assert!(lists_tool(listed, "ruff"));
        assert!(!lists_tool(listed, "uv"));

        // The indented lines name commands, not tools, so a command that
        // shares a tool's name is never read as the tool.
        assert!(!lists_tool("something v1\n- radialrig\n", ENGINE_TOOL));
        assert!(!lists_tool("No tools installed\n", ENGINE_TOOL));
        assert!(!lists_tool("", ENGINE_TOOL));
        // A name that only starts the same is a different tool.
        assert!(!lists_tool("radialrigger v1\n", ENGINE_TOOL));

        assert!(!strings::retryable(strings::FAIL_FOREIGN_EXE));
    }

    #[test]
    fn who_the_command_belongs_to_is_settled_without_ever_running_it() {
        // Nothing standing there is nothing to decide about.
        assert_eq!(owner(false, false, false), Owner::Absent);
        assert_eq!(owner(false, true, true), Owner::Absent);

        // uv keeps an entry naming it, so it arrived the one way this app
        // installs anything. That is the only case the command is ever
        // asked for its version.
        assert_eq!(owner(true, true, false), Owner::Ours);
        assert_eq!(owner(true, true, true), Owner::Ours);

        // No entry, but this app's own record beside it: the file its own
        // install step wrote and never got an entry for.
        assert_eq!(owner(true, false, true), Owner::Leftover);

        // No entry and no record. Someone else's, and it is refused where
        // it stands rather than started to find out what it is.
        assert_eq!(owner(true, false, false), Owner::Foreign);
    }

    #[test]
    fn the_install_record_survives_its_own_round_trip() {
        // The reader of this record decides whether a command standing in
        // the documented directory is this app's leftover or a stranger's
        // file, so a record that cannot be read back would turn every one
        // of this app's own half-installs into a refusal for good.
        let awkward = [
            "https://example.test/radialrig-0.1.0-py3-none-any.whl",
            "radialrig @ https://example.test/a.whl",
            "C:\\Users\\it's a folder\\dist\\a.whl",
            "C:\\Users\\naïve\\日本語\\a.whl",
        ];
        for source in awkward {
            let record = IntentRecord {
                source: source.to_string(),
                exe: "C:\\Users\\a\\.local\\bin\\radialrig.exe".to_string(),
                written: 1_757_700_000,
            };
            let read = parse_intent(&intent_text(&record)).expect(source);
            assert_eq!(read, record, "{source}");
            assert_eq!(read.source, source);
        }
        assert_eq!(parse_intent(""), None);
        assert_eq!(parse_intent("{}"), None);
        assert_eq!(parse_intent("not a record at all"), None);
    }

    #[test]
    fn the_record_tells_this_apps_own_leftover_from_a_strangers_file() {
        let mut root = std::env::temp_dir();
        root.push(format!("radialrig-intent-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let dir = root.join("bin");
        let data = root.join("data");
        std::fs::create_dir_all(&dir).expect("a directory");
        let exe = dir.join(ENGINE_EXE);
        std::fs::write(&exe, b"a command standing there").unwrap();

        // Nothing written yet, and nowhere to write it: neither is a
        // record, and neither turns a stranger's file into this app's.
        assert!(!intent_present(None, &exe));
        assert!(!intent_present(Some(&data), &exe));
        assert_eq!(
            owner(true, false, intent_present(Some(&data), &exe)),
            Owner::Foreign
        );

        // The record names this command and went down no later than the
        // command was written, which is the order the install keeps.
        let record = IntentRecord {
            source: "https://example.test/a.whl".to_string(),
            exe: exe.to_string_lossy().to_string(),
            written: command_written(&exe).expect("a time"),
        };
        keep_intent(Some(&data), &record).expect("the record goes down");
        assert!(intent_present(Some(&data), &exe));
        assert_eq!(
            owner(true, false, intent_present(Some(&data), &exe)),
            Owner::Leftover
        );
        // An entry uv keeps outranks the record either way: that command
        // is uv's, and this app installs over it rather than removing it.
        assert_eq!(
            owner(true, true, intent_present(Some(&data), &exe)),
            Owner::Ours
        );

        // And once the install has answered, the record is gone and the
        // refusal is back in force.
        clear_intent(Some(&data));
        assert!(!intent_present(Some(&data), &exe));
        assert_eq!(
            owner(true, false, intent_present(Some(&data), &exe)),
            Owner::Foreign
        );

        // Bytes that are not a record are not a record.
        std::fs::write(data.join(INTENT_RECORD), b"not a record at all").unwrap();
        assert!(!intent_present(Some(&data), &exe));

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_command_older_than_the_record_is_not_the_one_the_record_stands_for() {
        // app-bootstrap-01. The first install fails and its record stays
        // down; the user then installs the engine some other way, which
        // puts a command at the very path the record names. That command
        // was written before the record was, so it cannot be what the
        // record's install wrote, and the refusal fires where the removal
        // used to.
        let mut root = std::env::temp_dir();
        root.push(format!("radialrig-older-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let dir = root.join("bin");
        let data = root.join("data");
        std::fs::create_dir_all(&dir).expect("a directory");
        let exe = dir.join(ENGINE_EXE);
        std::fs::write(&exe, b"someone else's engine").unwrap();
        let born = command_written(&exe).expect("a time");

        let record = IntentRecord {
            source: "https://example.test/a.whl".to_string(),
            exe: exe.to_string_lossy().to_string(),
            written: born + 1,
        };
        keep_intent(Some(&data), &record).expect("the record goes down");
        assert!(!intent_present(Some(&data), &exe));
        let stop = guard_engine(None, &dir, Some(&data)).unwrap_err();
        assert_eq!(stop.key, strings::FAIL_FOREIGN_EXE);
        assert!(stop.detail.ends_with(ENGINE_EXE));
        assert_eq!(
            std::fs::read(&exe).unwrap(),
            b"someone else's engine",
            "the refusal is reached with the file untouched"
        );

        // A record that went down in the same second as the command, or
        // before it, is the order the install keeps, and that command is
        // this app's own leftover.
        for written in [born, born.saturating_sub(1)] {
            let record = IntentRecord { written, ..record.clone() };
            keep_intent(Some(&data), &record).expect("the record goes down");
            assert!(intent_present(Some(&data), &exe), "{written}");
            assert_eq!(guard_engine(None, &dir, Some(&data)), Ok(Owner::Leftover));
        }

        // A record from before it carried a time is no record: it cannot
        // say which command it stands for, so it stands for none.
        let undated = serde_json::json!({
            "source": "https://example.test/a.whl",
            "exe": exe.to_string_lossy(),
        });
        std::fs::write(data.join(INTENT_RECORD), undated.to_string()).unwrap();
        assert!(!intent_present(Some(&data), &exe));
        let stop = guard_engine(None, &dir, Some(&data)).unwrap_err();
        assert_eq!(stop.key, strings::FAIL_FOREIGN_EXE);

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_record_naming_another_path_does_not_speak_for_this_one() {
        // app-bootstrap-01. A record is read against the path the app is
        // about to decide about, and one that names some other path counts
        // as absent there: the command is refused, not removed.
        let mut root = std::env::temp_dir();
        root.push(format!("radialrig-other-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let dir = root.join("bin");
        let other = root.join("elsewhere");
        let data = root.join("data");
        std::fs::create_dir_all(&dir).expect("a directory");
        std::fs::create_dir_all(&other).expect("a directory");
        let exe = dir.join(ENGINE_EXE);
        std::fs::write(&exe, b"someone else's engine").unwrap();
        std::fs::write(other.join(ENGINE_EXE), b"this app's half-install").unwrap();

        // Written at the dawn of the clock, so only the path can decide.
        let record = IntentRecord {
            source: "https://example.test/a.whl".to_string(),
            exe: other.join(ENGINE_EXE).to_string_lossy().to_string(),
            written: 0,
        };
        keep_intent(Some(&data), &record).expect("the record goes down");

        assert!(!intent_present(Some(&data), &exe));
        let stop = guard_engine(None, &dir, Some(&data)).unwrap_err();
        assert_eq!(stop.key, strings::FAIL_FOREIGN_EXE);
        assert!(stop.detail.ends_with(ENGINE_EXE));
        assert_eq!(std::fs::read(&exe).unwrap(), b"someone else's engine");

        // Read against the path it names, the same record is this app's.
        assert!(intent_present(Some(&data), &other.join(ENGINE_EXE)));
        assert_eq!(guard_engine(None, &other, Some(&data)), Ok(Owner::Leftover));

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_failed_install_that_left_no_command_leaves_no_record_either() {
        // app-bootstrap-01, prescription 1. The record goes down before
        // the install runs; when the install fails and nothing stands at
        // the recorded path, the record has nothing to stand for and goes
        // with it, so it cannot later claim a command someone else puts
        // there. A uv that cannot be started is that failure path.
        let mut root = std::env::temp_dir();
        root.push(format!("radialrig-failed-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let dir = root.join("bin");
        let data = root.join("data");
        std::fs::create_dir_all(&dir).expect("a directory");
        let uv = root.join("no-such-uv.exe");
        let exe = dir.join(ENGINE_EXE);
        let chosen = config::Source {
            value: "radialrig @ https://example.test/a.whl".to_string(),
            overridden: true,
        };
        let armed = std::cell::Cell::new(false);
        let arm = || armed.set(true);
        let step = |_: &'static str| {};

        let stop = provide_engine(
            &uv,
            &dir,
            Some(&data),
            Standing::default(),
            &chosen,
            &step,
            &arm,
        )
        .unwrap_err();
        assert_eq!(stop.key, strings::FAIL_ENGINE_INSTALL);
        assert!(armed.get());
        assert!(!exe.exists());
        assert!(
            !data.join(INTENT_RECORD).exists(),
            "nothing at the recorded path, so no record either"
        );
        assert!(!intent_present(Some(&data), &exe));

        // With a command standing at the recorded path when the install
        // fails, the record stays: the next press decides about that one.
        std::fs::write(&exe, b"what the install wrote").unwrap();
        let stop = provide_engine(
            &uv,
            &dir,
            Some(&data),
            Standing::default(),
            &chosen,
            &step,
            &arm,
        )
        .unwrap_err();
        assert_eq!(stop.key, strings::FAIL_ENGINE_INSTALL);
        let kept = std::fs::read_to_string(data.join(INTENT_RECORD)).expect("the record stays");
        let kept = parse_intent(&kept).expect("and it is a record");
        assert_eq!(Path::new(&kept.exe), exe);
        assert_eq!(std::fs::read(&exe).unwrap(), b"what the install wrote");

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_recovery_press_with_no_uv_entry_fails_under_the_install_key() {
        // app-bootstrap-03. The recovery sentence says the engine was
        // taken out and put back, and the uninstall it describes runs
        // only when uv keeps an entry. A retry after a first install
        // that failed is a recovery press against a machine with no
        // entry: nothing is taken out, so the failure is the install's
        // own and is named as such.
        let mut root = std::env::temp_dir();
        root.push(format!("radialrig-recovery-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let dir = root.join("bin");
        let data = root.join("data");
        std::fs::create_dir_all(&dir).expect("a directory");
        let uv = root.join("no-such-uv.exe");
        let chosen = config::Source {
            value: "radialrig @ https://example.test/a.whl".to_string(),
            overridden: true,
        };
        let armed = std::cell::Cell::new(false);
        let arm = || armed.set(true);
        let step = |_: &'static str| {};

        let unlisted = Standing { recovering: true, listed: false, ..Standing::default() };
        let stop = provide_engine(&uv, &dir, Some(&data), unlisted, &chosen, &step, &arm)
            .unwrap_err();
        assert_eq!(stop.key, strings::FAIL_ENGINE_INSTALL);
        // The recovery press arms nothing: the flag is the first failure's.
        assert!(!armed.get());

        // With an entry the uninstall runs, and what it says is the
        // recovery's own sentence.
        let listed = Standing { recovering: true, listed: true, ..Standing::default() };
        let stop = provide_engine(&uv, &dir, Some(&data), listed, &chosen, &step, &arm)
            .unwrap_err();
        assert_eq!(stop.key, strings::FAIL_RECOVERY);
        assert!(!armed.get());

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn the_refusal_is_answerable_before_there_is_a_uv_to_ask() {
        // With no uv there is no uv entry, so the whole question is
        // settled by whether a command is standing there — which is what
        // lets the refusal happen before the archive is asked for.
        let mut dir = std::env::temp_dir();
        dir.push(format!("radialrig-guard-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a directory");

        // Nothing there: nothing to refuse, and no entry to report.
        assert_eq!(guard_engine(None, &dir, None), Ok(Owner::Absent));

        // A command this app did not put there, on a machine with no uv
        // that could have put it there.
        std::fs::write(dir.join(ENGINE_EXE), b"someone else's engine").unwrap();
        let stop = guard_engine(None, &dir, None).unwrap_err();
        assert_eq!(stop.key, strings::FAIL_FOREIGN_EXE);
        assert!(stop.detail.ends_with(ENGINE_EXE));
        assert!(!strings::retryable(stop.key));

        // And the bytes are still exactly the bytes that were there: the
        // refusal is reached without the command ever being started.
        assert_eq!(
            std::fs::read(dir.join(ENGINE_EXE)).unwrap(),
            b"someone else's engine"
        );

        // The same file with this app's own record beside it, naming it
        // and down no later than it was written, is this app's own
        // leftover, and that one is not refused.
        let data = dir.join("data");
        let record = IntentRecord {
            source: "https://example.test/a.whl".to_string(),
            exe: dir.join(ENGINE_EXE).to_string_lossy().to_string(),
            written: command_written(&dir.join(ENGINE_EXE)).expect("a time"),
        };
        keep_intent(Some(&data), &record).expect("the record goes down");
        assert_eq!(guard_engine(None, &dir, Some(&data)), Ok(Owner::Leftover));

        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- X4: the Path oracle both halves consult -----------------------

    #[test]
    fn a_segment_written_with_a_variable_is_the_directory_it_resolves_to() {
        // The registry holds what the user typed. uv names the resolved
        // directory. Comparing the two as written would say they are
        // different places and put a second copy of one directory on the
        // Path every time this ran.
        let home = match std::env::var("USERPROFILE") {
            Ok(home) if !home.trim().is_empty() => home,
            _ => return,
        };
        let resolved = format!("{home}\\.local\\bin");
        let written = "%USERPROFILE%\\.local\\bin";
        assert_eq!(expanded(written), resolved);
        assert!(segment_present(written, &resolved));
        assert!(segment_present(&format!("C:\\Windows;{written}"), &resolved));

        // Case and a trailing separator are folded on top of that, and a
        // quoted segment is the same segment.
        assert!(segment_present(&written.to_ascii_uppercase(), &resolved));
        assert!(segment_present(&format!("{written}\\"), &resolved));
        assert!(segment_present(written, &format!("{resolved}\\")));
        assert!(segment_present(&format!("\"{written}\""), &resolved));

        // A variable that resolves somewhere else is somewhere else.
        assert!(!segment_present(written, &format!("{home}\\.local")));
        assert!(!segment_present("%NOT_A_VARIABLE_1A2B3C%\\bin", &resolved));
    }

    #[test]
    fn the_registry_type_comes_through_the_write_untouched() {
        // REG_SZ is 1 and REG_EXPAND_SZ is 2. Whichever the value was
        // stored under is the one it is written back under, so a value
        // that expands keeps expanding and a plain one stays plain.
        let value = "%USERPROFILE%\\bin;C:\\Windows";
        let dir = "C:\\Users\\a\\.local\\bin";
        for kind in [1u32, 2u32] {
            let change = path_change(kind, value, dir).expect("a change");
            assert_eq!(change.kind, kind);
            assert_eq!(change.segment, dir);
            assert_eq!(change.value, prepended(value, dir));
            // The user's own value comes through byte for byte behind the
            // one new segment: nothing here bakes a reference to its text.
            assert!(change.value.ends_with(value));
            assert_eq!(parse_record(&record_text(&change)).unwrap(), change);
        }

        // A directory that is already a segment asks for no write at all,
        // whichever way it is spelled there.
        assert_eq!(path_change(1, &prepended(value, dir), dir), None);
        assert_eq!(path_change(2, &format!("{dir}\\;C:\\Windows"), dir), None);
        if let Ok(home) = std::env::var("USERPROFILE") {
            let resolved = format!("{home}\\.local\\bin");
            assert_eq!(
                path_change(2, "%USERPROFILE%\\.local\\bin;C:\\Windows", &resolved),
                None
            );
        }
    }

    // --- X5: the digest inside the host loop ---------------------------

    #[test]
    fn a_first_host_with_the_wrong_bytes_falls_through_to_the_second() {
        let hosts = [
            "https://one.example.test/uv.zip".to_string(),
            "https://two.example.test/uv.zip".to_string(),
        ];
        let good = b"the pinned archive".to_vec();
        let pin = digest(&good);

        // Both hosts carry the archive: the first one answers.
        let asked = std::cell::RefCell::new(Vec::new());
        let bytes = from_hosts(&hosts, &pin, |url| {
            asked.borrow_mut().push(url.to_string());
            Ok(good.clone())
        })
        .expect("the archive");
        assert_eq!(bytes, good);
        assert_eq!(asked.borrow().len(), 1);

        // The first host answers with something else. That is not the
        // archive, so the second host is asked, exactly as it would be
        // after a refused connection.
        let asked = std::cell::RefCell::new(Vec::new());
        let bytes = from_hosts(&hosts, &pin, |url| {
            asked.borrow_mut().push(url.to_string());
            if url == hosts[0] {
                return Ok(b"something else entirely".to_vec());
            }
            Ok(good.clone())
        })
        .expect("the archive from the second host");
        assert_eq!(bytes, good);
        assert_eq!(*asked.borrow(), hosts);

        // Every host answered and no answer was the archive. Now, and
        // only now, is this the stop a second press cannot move.
        let stop = from_hosts(&hosts, &pin, |_| Ok(b"something else".to_vec()))
            .unwrap_err();
        assert_eq!(stop.key, strings::FAIL_CHECKSUM);
        assert!(!strings::retryable(stop.key));

        // One host did not answer at all, so what its bytes would have
        // been is unknown, and a second press may yet reach it. That is
        // the download stop, and it takes the retry control.
        let stop = from_hosts(&hosts, &pin, |url| {
            if url == hosts[0] {
                return Ok(b"something else".to_vec());
            }
            Err("connection refused".to_string())
        })
        .unwrap_err();
        assert_eq!(stop.key, strings::FAIL_DOWNLOAD);
        assert!(strings::retryable(stop.key));

        // And a host that did not answer is simply passed over when a
        // later one carries the archive.
        let bytes = from_hosts(&hosts, &pin, |url| {
            if url == hosts[0] {
                return Err("connection refused".to_string());
            }
            Ok(good.clone())
        })
        .expect("the archive from the second host");
        assert_eq!(bytes, good);
    }
}

/// The PLAN-0009 phase 3 continuous-integration proof, run only when
/// asked for by name: `cargo test -- --ignored`, on the gate's container
/// leg. It reaches the network, downloads a Python through uv, and
/// writes under a scratch directory of its own, which is nothing a unit
/// test may do — so it is ignored everywhere the suite runs unasked.
#[cfg(all(test, unix))]
mod proof {
    use super::*;

    /// The whole headless half of the phase 3 done-condition, in the
    /// order the steps run behind the window: the archive over https,
    /// the digest against the pin, the two programs out of their nested
    /// directory with their execute bits, `uv tool install`, the spawn,
    /// the address line — and then the run's process group is ended and
    /// the engine is proved gone, which is what closing the window does.
    #[test]
    #[ignore]
    fn the_first_run_journey_holds_headlessly() {
        use std::io::BufRead;
        use std::os::unix::fs::PermissionsExt;

        let pins = config::pins();
        let bytes = download(pins).expect("the pinned archive downloads");
        verify(&bytes, pins.archive_sha256()).expect("the digest matches the pin");

        let scratch =
            std::env::temp_dir().join(format!("radialrig-proof-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&scratch);
        let into = scratch.join("bin");
        let uv = extract(&bytes, &into).expect("the two members land");
        for name in TAR_MEMBERS {
            let mode = std::fs::metadata(into.join(name))
                .expect(name)
                .permissions()
                .mode();
            assert!(mode & 0o111 != 0, "{name} is not executable");
        }

        // uv's own state is kept under the scratch directory, so the
        // proof is its own machine and leaves the runner's home alone.
        std::env::set_var("UV_TOOL_DIR", scratch.join("tools"));
        std::env::set_var("UV_TOOL_BIN_DIR", scratch.join("tool-bin"));
        std::env::set_var("UV_PYTHON_INSTALL_DIR", scratch.join("pythons"));
        std::env::set_var("UV_CACHE_DIR", scratch.join("cache"));

        let chosen = config::source(pins);
        run(&uv, &install_args(&pins.python_pin, &chosen.value))
            .expect("uv tool install lands the engine");
        let dir = tool_bin_dir(&uv).expect("uv names its bin directory");
        let engine = dir.join(ENGINE_EXE);
        assert!(engine.is_file(), "no engine at {}", engine.display());

        // The spawn, exactly as attach::start makes it: its own process
        // group, both pipes read, the address matched at start of line.
        let mut command = std::process::Command::new(&engine);
        command
            .arg("manage")
            .arg("--no-browser")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        let mut child = crate::attach::grouped(crate::attach::quiet(&mut command))
            .spawn()
            .expect("the engine spawns");
        let job = crate::attach::own_job(&child).expect("the run's own group");
        let pid = child.id() as i32;

        let out = child.stdout.take().expect("a stdout pipe");
        let url = std::io::BufReader::new(out)
            .lines()
            .map_while(Result::ok)
            .find_map(|line| crate::attach::manage_line(&line).map(str::to_string))
            .expect("the engine prints its address");
        assert!(crate::attach::valid_launch_url(&url), "unreadable address: {url}");

        // Ending the run's process group is what closing the window
        // does, and it must leave no engine process behind.
        drop(job);
        let status = child.wait().expect("the child is reaped");
        assert!(!status.success(), "the engine outlived its group");
        let gone_by = std::time::Instant::now() + std::time::Duration::from_secs(5);
        loop {
            let answer = unsafe { libc::kill(-pid, 0) };
            if answer == -1
                && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
            {
                break;
            }
            assert!(
                std::time::Instant::now() < gone_by,
                "a process in the engine's group is still standing"
            );
            std::thread::sleep(std::time::Duration::from_millis(50));
        }

        let _ = std::fs::remove_dir_all(&scratch);
    }
}
