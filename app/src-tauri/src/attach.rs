//! D13 `attach.rs`: start the engine and follow the line it prints.
//!
//! The version check (D6), the spawn with no console window, the reader
//! threads that drain both pipes, the one start-of-line match on the
//! address the engine prints, the single navigation to it (D7), the
//! quiesce on close and the Job object behind it (D9).
//!
//! Two navigations exist in this shell and no others: the one to the
//! address the engine printed, and the one back to the local screen when
//! the engine stops or fails. The launch address is spent by the first of
//! them — it is never kept, never re-used, and its authority is never
//! rewritten, because the engine's own guards refuse every rewriting.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::bootstrap::{self, Slot};
use crate::strings;
use tauri::{AppHandle, Manager};

/// The engine prints this at the start of a line, and only at the start.
/// Its stopped line carries the same eight bytes further in, so a
/// substring match would read the wrong line.
const MANAGE_PREFIX: &str = "manage: ";

/// The authority the engine binds and the only one this shell talks to.
const LOOPBACK: &str = "http://127.0.0.1:";

/// The route the address names, and the route a close posts back to.
const MANAGE_QUERY: &str = "manage?t=";

/// The session token, as the engine's own redirect writes it into the
/// address bar. The launch token is spent by the navigation; this is the
/// one a close carries.
const SESSION_PARAM: &str = "s";
const LAUNCH_PARAM: &str = "t";

/// CREATE_NO_WINDOW: no console flashes behind the window.
#[cfg(windows)]
const NO_WINDOW: u32 = 0x08000000;

/// D6: the address has thirty seconds to arrive. The engine binds its
/// port before it prints, and nothing downloads inside `manage`.
const LINE_WAIT: Duration = Duration::from_secs(30);

/// D9: the clean close waits this long for the engine to finish what it
/// was doing. The Job object is what ends it if the wait runs out.
const QUIESCE_WAIT: Duration = Duration::from_secs(5);

/// How long a child gets to be seen exiting after its output ended.
///
/// Output that ends before the address arrives is nearly always a child
/// that is on its way out, and the sentence about a child that stopped is
/// a truer sentence than the one about thirty seconds that did not pass.
/// The wait is short because the exit has already happened; what is being
/// waited on is only the reading of it.
const EXIT_WAIT: Duration = Duration::from_secs(2);

/// How long a close post may take before the wait gives up on it.
const POST_WAIT: Duration = Duration::from_secs(2);

/// How much of the engine's own error output a sentence's slot carries.
const STDERR_LINES: usize = 8;

// --- the pure readings -------------------------------------------------

/// The address off one stdout line, matched at the start of the line.
///
/// Start of line, always: the stopped line carries the same eight bytes
/// at an offset, so a shell that searched for them anywhere would follow
/// a line that was never an invitation to follow it.
pub fn manage_line(line: &str) -> Option<&str> {
    let line = line.strip_suffix('\n').unwrap_or(line);
    let line = line.strip_suffix('\r').unwrap_or(line);
    line.strip_prefix(MANAGE_PREFIX)
}

/// Whether an address is the exact shape D6 pins. Anything else is engine
/// failure and this window never navigates to it.
pub fn valid_launch_url(url: &str) -> bool {
    let rest = match url.strip_prefix(LOOPBACK) {
        Some(rest) => rest,
        None => return false,
    };
    let (port, rest) = match rest.split_once('/') {
        Some(split) => split,
        None => return false,
    };
    if port.is_empty() || !port.bytes().all(|byte| byte.is_ascii_digit()) {
        return false;
    }
    let token = match rest.strip_prefix(MANAGE_QUERY) {
        Some(token) => token,
        None => return false,
    };
    !token.is_empty() && token.bytes().all(is_token_byte)
}

fn is_token_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-'
}

/// The bytes a query field may carry into a request this shell builds.
///
/// The same class the token check uses, widened by exactly the bytes a
/// percent-encoded query field needs, and by the one byte a browser's
/// own serialiser leaves literal in a folder name: `*`. Everything else
/// — a space, a control byte, a carriage return, a second question mark
/// — is refused, because every one of them changes the request line
/// rather than the query inside it.
fn is_query_byte(byte: u8) -> bool {
    is_token_byte(byte)
        || matches!(byte, b'.' | b'~' | b'%' | b'+' | b'=' | b'&' | b':' | b',' | b'*')
}

/// The bytes a path may carry into the same request.
fn is_path_byte(byte: u8) -> bool {
    is_query_byte(byte) || byte == b'/'
}

/// The launch address's token, when the address is the shape D6 pins.
pub fn launch_token(url: &str) -> Option<&str> {
    let rest = url.strip_prefix(LOOPBACK)?;
    let (port, rest) = rest.split_once('/')?;
    if port.is_empty() || !port.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let token = rest.strip_prefix(MANAGE_QUERY)?;
    if token.is_empty() || !token.bytes().all(is_token_byte) {
        return None;
    }
    Some(token)
}

/// A launch token's fingerprint: its digest, and nothing that can be read
/// back into it.
///
/// The reading, recorded. D7 says the shell never stores the launch
/// address, and what that forbids is reuse and disclosure: the address
/// must not be navigated to twice, handed to another program, or written
/// anywhere a reader or a log could pick it up. A one-way digest is none
/// of those. It cannot be turned back into the token, it is never sent
/// anywhere, and its only use is to recognise the spent address in order
/// to refuse it. Keeping it serves the same purpose the sentence does.
pub fn fingerprint(token: &str) -> String {
    crate::bootstrap::digest(token.as_bytes())
}

/// One address's origin — its scheme and its authority, folded.
pub fn origin_of(url: &str) -> Option<String> {
    let (scheme, rest) = url.split_once("://")?;
    if scheme.is_empty() || scheme.contains(['/', '?', '#']) {
        return None;
    }
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.is_empty() {
        return None;
    }
    Some(format!(
        "{}://{}",
        scheme.to_ascii_lowercase(),
        authority.to_ascii_lowercase()
    ))
}

/// Whether this window may go to an address.
///
/// Two rules, and nothing else passes. The spent launch address is
/// refused however it is reached — a history Back, a bookmark, a script —
/// because it is good for exactly one navigation and that one has
/// happened. And the only origins this window goes to at all are its own
/// and the one the engine bound; anything else is somewhere this shell
/// never sends a reader.
///
/// `home` not being known yet means the window has not yet drawn its own
/// screen, so the address it is going to is the one it was configured
/// with. That is the only navigation that can happen before the first
/// document finishes loading.
pub fn navigable(
    url: &str,
    home: Option<&str>,
    authority: Option<&str>,
    spent: Option<&str>,
) -> bool {
    let here = match origin_of(url) {
        Some(here) => here,
        None => return false,
    };
    if let (Some(spent), Some(token)) = (spent, launch_token(url)) {
        if fingerprint(token) == spent {
            return false;
        }
    }
    let home = match home {
        Some(home) => home,
        None => return true,
    };
    if origin_of(home).as_deref() == Some(here.as_str()) {
        return true;
    }
    match authority {
        Some(authority) => here == format!("http://{}", authority.to_ascii_lowercase()),
        None => false,
    }
}

/// A version written as numbers, for comparing one against another.
pub fn version_parts(said: &str) -> Option<Vec<u64>> {
    let said = said.trim();
    if said.is_empty() {
        return None;
    }
    let mut parts = Vec::new();
    for piece in said.split('.') {
        let digits: String = piece.chars().take_while(char::is_ascii_digit).collect();
        if digits.is_empty() {
            return None;
        }
        parts.push(digits.parse::<u64>().ok()?);
    }
    Some(parts)
}

/// Whether one version is at least another. `None` when either side is
/// not a version at all, which is its own kind of answer.
pub fn at_least(found: &str, wanted: &str) -> Option<bool> {
    let (found, wanted) = (version_parts(found)?, version_parts(wanted)?);
    let width = found.len().max(wanted.len());
    for index in 0..width {
        let a = found.get(index).copied().unwrap_or(0);
        let b = wanted.get(index).copied().unwrap_or(0);
        if a != b {
            return Some(a > b);
        }
    }
    Some(true)
}

/// Where a close goes and what it carries, read off the address the
/// window is on right now.
#[derive(Debug, PartialEq, Eq)]
pub struct CloseTarget {
    pub authority: String,
    pub target: String,
    pub token: String,
}

/// The close's target, read from the window's current address.
///
/// The session token is the one the engine's own redirect put in the
/// address bar. A window still on the launch address has not been
/// answered yet and carries nothing a close could be honoured with.
pub fn close_target(url: &str) -> Option<CloseTarget> {
    close_target_or(url, None)
}

/// The close's target, with a remembered token standing in when the
/// address carries none.
///
/// A walkthrough press on the manage page leaves the window at
/// `/manage?root=...`: the form posts there and is answered with a page,
/// not a redirect, so the address bar has lost the session field the
/// redirect once put in it. The token that redirect carried is still the
/// one the engine honours a close with, and the run keeps it for exactly
/// this address. The address's own token, when it has one, wins, under
/// its own rule.
pub fn close_target_or(url: &str, remembered: Option<&str>) -> Option<CloseTarget> {
    let rest = url.strip_prefix(LOOPBACK)?;
    let (port, rest) = rest.split_once('/')?;
    if port.is_empty() || !port.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let (path, query) = match rest.split_once('?') {
        Some((path, query)) => (path, query),
        None => (rest, ""),
    };
    let path = path.split('#').next().unwrap_or(path);
    let query = query.split('#').next().unwrap_or(query);
    // The path is copied into the request line this shell builds, so it
    // is checked byte for byte before it goes there.
    if !path.bytes().all(is_path_byte) {
        return None;
    }

    let mut token = None;
    let mut kept: Vec<&str> = Vec::new();
    for field in query.split('&').filter(|field| !field.is_empty()) {
        let (name, value) = field.split_once('=').unwrap_or((field, ""));
        match name {
            SESSION_PARAM => token = Some(value.to_string()),
            LAUNCH_PARAM => {}
            _ => {
                // Every field copied forward is checked the same way the
                // token is: a byte outside the class would land in the
                // request line rather than in the query it belongs to.
                // A field that fails is left out of the request, and the
                // close still goes: a close that is not posted at all is
                // an engine ended mid-write, which is the worse outcome.
                if field.bytes().all(is_query_byte) {
                    kept.push(field)
                }
            }
        }
    }
    let token = match token {
        Some(token) => token,
        None => remembered?.to_string(),
    };
    if token.is_empty() || !token.bytes().all(is_token_byte) {
        return None;
    }
    let target = if kept.is_empty() {
        format!("/{path}")
    } else {
        format!("/{path}?{}", kept.join("&"))
    };
    Some(CloseTarget {
        authority: format!("127.0.0.1:{port}"),
        target,
        token,
    })
}

/// The close post, byte for byte. No render id: D8 honours a close with
/// none, which is what this one and a terminal probe both post.
pub fn close_request(target: &CloseTarget) -> String {
    let body = format!("token={}&action=close", target.token);
    format!(
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        target.target,
        target.authority,
        body.len(),
        body
    )
}

// --- running things ----------------------------------------------------

/// One command, with no console window behind it and none of this
/// process's Python environment under it.
///
/// The AppImage's stock AppRun exports PYTHONHOME and PYTHONPATH for
/// the mount it runs from, and a child that inherits the pair dies
/// before its first import: the engine's Python looks for its standard
/// library inside a mount that carries none. Proved in
/// reports/release-0.1.4-2026-08-23. The pair is scrubbed here, for
/// every child on every platform and package form, which also covers a
/// reader who set the variables for reasons of their own: uv and the
/// engine each find their library beside their own binary and need
/// neither variable.
pub fn quiet(command: &mut Command) -> &mut Command {
    command.env_remove("PYTHONHOME").env_remove("PYTHONPATH");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(NO_WINDOW);
    }
    command
}

/// The engine's command, put at the head of a process group of its own.
///
/// Linux only in effect, and for the engine alone: the group is what a
/// run's own Job ends there (PLAN-0009 phase 3 prescription 4), set
/// before exec so there is no moment the child runs outside it. The
/// bootstrap children never come through here — with no app-lifetime
/// job on Linux, a group of their own would only cut them off from the
/// terminal's signals without gaining anything.
pub fn grouped(command: &mut Command) -> &mut Command {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            command.pre_exec(|| {
                if libc::setpgid(0, 0) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }
    command
}

/// `radialrig --version`, which prints `radialrig <version>`.
pub fn engine_version(exe: &Path) -> Result<String, Slot> {
    let said = bootstrap::run(exe, &["--version"])
        .map_err(|why| Slot::of(strings::FAIL_SPAWN, why))?;
    match bootstrap::parse_named_version("radialrig", &said) {
        Some(version) => Ok(version),
        None => Err(Slot::of(strings::FAIL_SPAWN, bootstrap::first_line(&said))),
    }
}

/// Whether a watcher's stop is the one this window should be told about.
///
/// Two ways it is not. This shell asked for the stop, so the reader knows
/// already. Or a newer run has started since this watcher's child was
/// spawned, and a stop from the run before it would send a live window
/// back to a stopped screen.
pub fn watcher_speaks(mine: u64, current: u64, closing: bool) -> bool {
    mine == current && !closing
}

/// Whether a give-up path ends the child it gave up on.
///
/// Every stop that leaves this shell without an address to follow is a
/// stop that leaves a child running with nobody reading it. A retry from
/// any of them would spawn a second engine beside the first, so the first
/// one's own job is closed and its whole tree ends with it.
pub fn gives_up(key: &str) -> bool {
    matches!(
        key,
        strings::FAIL_SPAWN | strings::FAIL_LINE_TIMEOUT | strings::FAIL_LINE_UNPARSED
    )
}

/// One run of the engine, and everything that belongs to that run alone.
/// Which run is current lives in the counter beside this, not here: a
/// watcher carries the number it was started under and compares it
/// against that counter, which is the one place the answer can be read
/// without holding this state.
#[derive(Default)]
struct Run {
    /// Whether this run's child was started and has not been seen to exit.
    live: bool,
    /// Set by the watcher when the child ends, however it ends.
    exited: Arc<AtomicBool>,
    /// Set when this shell is the one ending it.
    closing: Arc<AtomicBool>,
    /// This run's own job object. Closing it ends the whole tree the
    /// engine's command started, the shim's children included.
    job: Option<Job>,
    /// The authority the engine bound, once its address parsed.
    authority: Option<String>,
    /// The fingerprint of the launch address this run spent.
    spent: Option<String>,
    /// Whether the one navigation that address is good for is still to
    /// happen. It is set the moment before the navigation is asked for
    /// and cleared by that navigation, so every later arrival at the same
    /// address is refused.
    pending: bool,
    /// The session token, kept from the first admitted address on the
    /// engine's authority that carried one. A later address may have
    /// lost it — a walkthrough press leaves the window on the route it
    /// posted to — and the close is honoured with this one all the same.
    token: Option<String>,
}

/// The engine's lifetime, as this shell holds it.
pub struct Engine {
    generation: AtomicU64,
    run: Mutex<Run>,
}

impl Engine {
    pub fn new() -> Self {
        Engine {
            generation: AtomicU64::new(0),
            run: Mutex::new(Run::default()),
        }
    }

    fn held(&self) -> std::sync::MutexGuard<'_, Run> {
        self.run.lock().expect("the engine's own state")
    }

    /// A new run, with its own generation and its own flags. Whatever the
    /// run before it left is dropped here, its job object included.
    fn begin(&self) -> (u64, Arc<AtomicBool>, Arc<AtomicBool>) {
        let generation = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        let exited = Arc::new(AtomicBool::new(false));
        let closing = Arc::new(AtomicBool::new(false));
        let mut held = self.held();
        *held = Run {
            live: true,
            exited: Arc::clone(&exited),
            closing: Arc::clone(&closing),
            job: None,
            authority: None,
            spent: None,
            pending: false,
            token: None,
        };
        (generation, exited, closing)
    }

    /// Which run is current.
    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }

    pub fn is_live(&self) -> bool {
        let held = self.held();
        held.live && !held.exited.load(Ordering::SeqCst)
    }

    /// The claim on the closing, made once. The exchange is the claim: a
    /// second close request that arrives while the first is still running
    /// gets `false` and starts no second wait.
    pub fn claim_closing(&self) -> bool {
        let held = self.held();
        held.closing
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
    }

    /// Give back a claim on the closing that was never spent. The update
    /// takes the claim before it installs, so a window close pressed while
    /// it installs waits on it; an install that fails before anything was
    /// closed gives it back here, or the next window close would find it
    /// taken and leave the window standing (PLAN-0021 phase 3 review).
    pub(crate) fn release_closing(&self) {
        self.held().closing.store(false, Ordering::SeqCst);
    }

    /// This run's child is in its own job now.
    fn hold_job(&self, job: Option<Job>) {
        self.held().job = job;
    }

    /// This run has no child worth waiting on any more.
    fn no_longer_live(&self) {
        self.held().live = false;
    }

    /// End this run's tree, now, by closing the job it was put in. The
    /// close path calls it after the quiesce: on Windows the app-lifetime
    /// job would end the tree at process exit anyway, but on Linux
    /// nothing else would, and `std::process::exit` runs no destructor.
    pub(crate) fn end_job(&self) {
        let job = self.held().job.take();
        drop(job);
    }

    /// The run's job, stood down without firing, at the moment its
    /// child's exit is read.
    ///
    /// On Linux the job holds a process-group id, and the reap that
    /// just happened frees that id for reissue. A drop that fires later
    /// — a retry hours on, the close path — would aim SIGKILL at
    /// whatever unrelated group holds the reissued id by then, so the
    /// job is disarmed here, at the one moment the exit is known and
    /// the id is provably not yet a stranger's. On Windows the handle
    /// cannot be reissued while held, and closing it here ends any
    /// leftover of the tree, which is what the drop is for.
    ///
    /// Guarded by generation: a watcher from a replaced run finds its
    /// own job already dropped by `begin` and must not touch the run
    /// that is up.
    pub(crate) fn job_exited(&self, generation: u64) {
        if generation != self.generation() {
            return;
        }
        if let Some(job) = self.held().job.take() {
            stand_down(job);
        }
    }

    /// The address is about to be navigated to, once.
    fn spend(&self, url: &str, authority: &str) {
        let mut held = self.held();
        held.authority = Some(authority.to_ascii_lowercase());
        held.spent = launch_token(url).map(fingerprint);
        held.pending = held.spent.is_some();
    }

    /// Whether this window may go to an address, and the spending of the
    /// one navigation the launch address is good for.
    pub fn may_navigate(&self, url: &str, home: Option<&str>) -> bool {
        let mut held = self.held();
        if held.pending {
            if let (Some(spent), Some(token)) = (held.spent.as_deref(), launch_token(url)) {
                if fingerprint(token) == spent {
                    held.pending = false;
                    return true;
                }
            }
        }
        let admitted = navigable(url, home, held.authority.as_deref(), held.spent.as_deref());
        // The session token, kept the first time an admitted address on
        // the engine's own authority carries one. It is read with the
        // close's own reading, so what is kept is exactly what a close
        // would have carried off this address.
        if admitted && held.token.is_none() {
            if let Some(target) = close_target(url) {
                if held.authority.as_deref() == Some(target.authority.as_str()) {
                    held.token = Some(target.token);
                }
            }
        }
        admitted
    }

    /// D9's clean close: post the close, then wait a bounded five seconds
    /// for the child to finish what it was doing. The Job object ends it
    /// if the wait runs out or if this process never gets this far.
    pub fn quiesce(&self, url: &str) {
        let (exited, remembered) = {
            let held = self.held();
            held.closing.store(true, Ordering::SeqCst);
            (Arc::clone(&held.exited), held.token.clone())
        };
        // Without a session token — the address's own, or the one this
        // run remembered — there is nothing to ask, so there is nothing
        // to wait for either; the Job object ends the child.
        let target = match close_target_or(url, remembered.as_deref()) {
            Some(target) => target,
            None => return,
        };
        post(&target);
        let until = Instant::now() + QUIESCE_WAIT;
        while Instant::now() < until {
            if exited.load(Ordering::SeqCst) {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }
}

impl Default for Engine {
    fn default() -> Self {
        Engine::new()
    }
}

/// One close post over loopback, its answer read and thrown away.
fn post(target: &CloseTarget) {
    let addressed = target.authority.clone();
    let request = close_request(target);
    let stream = TcpStream::connect(&addressed).ok();
    if let Some(mut stream) = stream {
        let _ = stream.set_write_timeout(Some(POST_WAIT));
        let _ = stream.set_read_timeout(Some(POST_WAIT));
        if stream.write_all(request.as_bytes()).is_ok() {
            let _ = stream.flush();
            let mut answer = Vec::new();
            let _ = stream.take(4096).read_to_end(&mut answer);
        }
    }
}

/// D6 and D7: spawn, read the line, navigate to it once.
pub fn start(app: &AppHandle, exe: &Path) -> Result<(), Slot> {
    let mut command = Command::new(exe);
    command
        .arg("manage")
        .arg("--no-browser")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(home) = bootstrap::profile_dir() {
        command.current_dir(home);
    }
    let mut child = grouped(quiet(&mut command))
        .spawn()
        .map_err(|why| Slot::of(strings::FAIL_SPAWN, why.to_string()))?;

    // Two backstops, and they answer two different questions. The
    // app-lifetime job ends every child when this process ends, however
    // it ends. This run's own job ends this run's tree on its own, which
    // is what a give-up path needs: a child nobody is reading any more
    // has to be gone before a retry can spawn a second one beside it.
    join_job(&child);
    let mine = own_job(&child);

    let said: Arc<Mutex<VecDeque<String>>> = Arc::new(Mutex::new(VecDeque::new()));
    let (tx, rx) = mpsc::channel::<String>();

    if let Some(out) = child.stdout.take() {
        std::thread::spawn(move || {
            let mut sent = false;
            for line in BufReader::new(out).lines() {
                let line = match line {
                    Ok(line) => line,
                    Err(_) => break,
                };
                if !sent {
                    if let Some(url) = manage_line(&line) {
                        sent = tx.send(url.to_string()).is_ok();
                    }
                }
            }
        });
    }
    if let Some(err) = child.stderr.take() {
        let kept = Arc::clone(&said);
        std::thread::spawn(move || {
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                let mut held = kept.lock().expect("the engine output");
                held.push_back(line);
                while held.len() > STDERR_LINES {
                    held.pop_front();
                }
            }
        });
    }

    let shell = app.state::<bootstrap::Shell>();
    let (generation, exited, closing) = shell.engine.begin();
    shell.engine.hold_job(mine);

    // A stop with no address to follow: the reader is told what happened,
    // and this run's tree ends here rather than waiting for a process
    // that nobody is reading any more. A child that never named an
    // address handed out no token either, so there is no close to post.
    let give_up = |stop: Slot, child: Child, said: &Arc<Mutex<VecDeque<String>>>| -> Slot {
        closing.store(true, Ordering::SeqCst);
        shell.engine.no_longer_live();
        watch(
            app.clone(),
            child,
            generation,
            Arc::clone(&exited),
            Arc::clone(&closing),
            Arc::clone(said),
        );
        if gives_up(stop.key) {
            shell.engine.end_job();
        }
        with_tail(stop, said)
    };

    let url = match wait_for_line(&rx, &exited, &mut child) {
        Ok(url) => url,
        Err(stop) => return Err(give_up(stop, child, &said)),
    };
    if !valid_launch_url(&url) {
        let stop = Slot::of(strings::FAIL_LINE_UNPARSED, url);
        return Err(give_up(stop, child, &said));
    }

    watch(
        app.clone(),
        child,
        generation,
        Arc::clone(&exited),
        Arc::clone(&closing),
        said,
    );

    // The child is watched by now, so a stop past this point cannot hand
    // it back; it still ends this run's tree the way the earlier stops
    // do, because a page nobody reached is a server nobody holds.
    let give_up_watched = |stop: Slot| -> Slot {
        closing.store(true, Ordering::SeqCst);
        shell.engine.no_longer_live();
        if gives_up(stop.key) {
            shell.engine.end_job();
        }
        stop
    };

    let window = app
        .get_webview_window(bootstrap::MAIN_WINDOW)
        .ok_or_else(|| give_up_watched(Slot::bare(strings::FAIL_SPAWN)))?;
    let parsed = tauri::Url::parse(&url)
        .map_err(|_| give_up_watched(Slot::of(strings::FAIL_LINE_UNPARSED, url.clone())))?;
    // The address is spent by the navigation that is about to happen. Its
    // fingerprint goes down first, so a Back that reaches for it later —
    // and every other way of reaching for it — is refused by the guard on
    // the window rather than answered by the engine.
    let authority = parsed.host_str().unwrap_or_default().to_string();
    let authority = match parsed.port() {
        Some(port) => format!("{authority}:{port}"),
        None => authority,
    };
    shell.engine.spend(&url, &authority);
    window
        .navigate(parsed)
        .map_err(|why| give_up_watched(Slot::of(strings::FAIL_LINE_UNPARSED, why.to_string())))?;
    Ok(())
}

/// The address, or the reason there is not one within the window D6 gives.
fn wait_for_line(
    rx: &mpsc::Receiver<String>,
    exited: &Arc<AtomicBool>,
    child: &mut Child,
) -> Result<String, Slot> {
    let until = Instant::now() + LINE_WAIT;
    // The engine's output ended without the line. That is nearly always a
    // child on its way out, so the exit is given a short moment to become
    // readable and then named for what it is: a command that did not
    // start, with the last of what it said. The thirty seconds belong to
    // a child that is still running and has not printed yet; a sentence
    // about thirty seconds that did not pass would be a false one.
    let mut ended: Option<Instant> = None;
    loop {
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(url) => return Ok(url),
            Err(RecvTimeoutError::Disconnected) => {
                if ended.is_none() {
                    ended = Some(Instant::now());
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(RecvTimeoutError::Timeout) => {}
        }
        if let Ok(Some(status)) = child.try_wait() {
            exited.store(true, Ordering::SeqCst);
            let code = status.code().map(|code| code.to_string()).unwrap_or_default();
            return Err(Slot::of(strings::FAIL_SPAWN, code));
        }
        if ended.map(|at| at.elapsed() >= EXIT_WAIT).unwrap_or(false) {
            return Err(Slot::bare(strings::FAIL_SPAWN));
        }
        if Instant::now() >= until {
            return Err(Slot::bare(strings::FAIL_LINE_TIMEOUT));
        }
    }
}

fn with_tail(stop: Slot, said: &Arc<Mutex<VecDeque<String>>>) -> Slot {
    let held = said.lock().expect("the engine output");
    let tail = bootstrap::tail_of(&held.iter().cloned().collect::<Vec<_>>().join("\n"));
    if tail.is_empty() {
        return stop;
    }
    let detail = if stop.detail.is_empty() {
        tail
    } else {
        format!("{}: {tail}", stop.detail)
    };
    Slot::of(stop.key, detail)
}

/// The one other navigation: back to the local screen, in the stopped
/// state, when the engine ends while the window is open.
fn watch(
    app: AppHandle,
    mut child: Child,
    generation: u64,
    exited: Arc<AtomicBool>,
    closing: Arc<AtomicBool>,
    said: Arc<Mutex<VecDeque<String>>>,
) {
    std::thread::spawn(move || {
        let status = child.wait();
        exited.store(true, Ordering::SeqCst);
        // The child was just reaped, so the run's job is stood down
        // before anything else can fire it at a reissued id.
        app.state::<bootstrap::Shell>().engine.job_exited(generation);
        let current = app
            .state::<bootstrap::Shell>()
            .engine
            .generation();
        // A watcher left over from a run that has been replaced says
        // nothing: its child's ending is old news, and sending the window
        // back to a stopped screen would take down the run that is up.
        if !watcher_speaks(generation, current, closing.load(Ordering::SeqCst)) {
            return;
        }
        let code = status
            .ok()
            .and_then(|status| status.code())
            .map(|code| code.to_string())
            .unwrap_or_default();
        // The state is put down before the window is sent back to it, so
        // the page draws the stop the moment it finishes loading rather
        // than drawing whatever stood there and being corrected after.
        let stop = with_tail(Slot::of(strings::FAIL_ENGINE_STOPPED, code), &said);
        bootstrap::stopped(&app, stop.detail);
        go_home(&app);
    });
}

/// The window, back on the screen this shell serves itself.
pub fn go_home(app: &AppHandle) {
    let shell = app.state::<bootstrap::Shell>();
    let home = match shell.home() {
        Some(home) => home,
        None => return,
    };
    if let (Some(window), Ok(url)) = (
        app.get_webview_window(bootstrap::MAIN_WINDOW),
        tauri::Url::parse(&home),
    ) {
        let _ = window.navigate(url);
    }
}

// --- D9's backstop -----------------------------------------------------

/// The Job object, made once and held for as long as this process runs.
///
/// `KILL_ON_JOB_CLOSE` means Windows ends everything in the job when the
/// last handle to it closes, which happens when this process ends however
/// it ends — a crash and a kill included. Bootstrap children join the
/// same job, so no installer is left running either.
#[cfg(windows)]
pub fn join_job(child: &Child) -> bool {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JobObjectExtendedLimitInformation,
        SetInformationJobObject,
    };
    use windows::Win32::System::JobObjects::CreateJobObjectW;
    use windows::core::PCWSTR;

    static JOB: std::sync::OnceLock<isize> = std::sync::OnceLock::new();
    let job = *JOB.get_or_init(|| unsafe {
        let created = match CreateJobObjectW(None, PCWSTR::null()) {
            Ok(created) => created,
            Err(_) => return 0,
        };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set = SetInformationJobObject(
            created,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const core::ffi::c_void,
            core::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if set.is_err() {
            return 0;
        }
        created.0 as isize
    });
    if job == 0 {
        return false;
    }
    unsafe {
        AssignProcessToJobObject(
            HANDLE(job as *mut core::ffi::c_void),
            HANDLE(child.as_raw_handle() as *mut core::ffi::c_void),
        )
        .is_ok()
    }
}

/// There is no app-lifetime job on Linux: nothing there ends every child
/// when this process is killed outright. What stands in for it is the
/// run's own process group below, ended by the close path and by every
/// give-up path — the same paths that close the run's job on Windows.
#[cfg(not(windows))]
pub fn join_job(_child: &Child) -> bool {
    false
}

/// One run's own job object, held for exactly as long as that run.
///
/// Dropping it closes the handle, and `KILL_ON_JOB_CLOSE` means Windows
/// ends everything inside it at that moment. A child is in two jobs: this
/// one and the app-lifetime one, which is what Windows has allowed since
/// its jobs could nest. The app job still ends every child when this
/// process ends; this one ends one run's tree while the process lives on.
#[cfg(windows)]
pub struct Job(isize);

#[cfg(windows)]
impl Drop for Job {
    fn drop(&mut self) {
        use windows::Win32::Foundation::{CloseHandle, HANDLE};
        if self.0 != 0 {
            unsafe {
                let _ = CloseHandle(HANDLE(self.0 as *mut core::ffi::c_void));
            }
        }
    }
}

/// A job of this run's own, with the child in it.
#[cfg(windows)]
pub fn own_job(child: &Child) -> Option<Job> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JobObjectExtendedLimitInformation,
        SetInformationJobObject,
    };
    use windows::core::PCWSTR;

    unsafe {
        let created = CreateJobObjectW(None, PCWSTR::null()).ok()?;
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set = SetInformationJobObject(
            created,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const core::ffi::c_void,
            core::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if set.is_err() {
            let _ = CloseHandle(created);
            return None;
        }
        let joined = AssignProcessToJobObject(
            created,
            HANDLE(child.as_raw_handle() as *mut core::ffi::c_void),
        );
        if joined.is_err() {
            let _ = CloseHandle(created);
            return None;
        }
        Some(Job(created.0 as isize))
    }
}

/// One run's own process group, held for exactly as long as that run.
///
/// `quiet` put the child at the head of a group of its own before it
/// ran, so the child and everything it starts share one group id.
/// Dropping this sends SIGKILL to that group, which is the Linux answer
/// to closing the run's job object: the engine and every child it
/// started end together (PLAN-0009 phase 3 prescription 4).
#[cfg(not(windows))]
pub struct Job(i32);

#[cfg(not(windows))]
impl Drop for Job {
    fn drop(&mut self) {
        if self.0 > 0 {
            unsafe {
                let _ = libc::kill(-self.0, libc::SIGKILL);
            }
        }
    }
}

#[cfg(not(windows))]
pub fn own_job(child: &Child) -> Option<Job> {
    let pid = child.id() as i32;
    if pid <= 0 {
        return None;
    }
    Some(Job(pid))
}

/// One job taken out of a run without firing it, for the moment the
/// run's child has just been reaped.
#[cfg(windows)]
fn stand_down(job: Job) {
    // Closing the handle ends any leftover of the tree. A held handle
    // cannot have been reissued, so the close aims only at this run's.
    drop(job);
}

#[cfg(not(windows))]
fn stand_down(mut job: Job) {
    // The group id is free for reissue from the reap onward, so the
    // kill is disarmed rather than fired: a SIGKILL aimed at a
    // reissued id would land on a stranger's tree.
    job.0 = 0;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn every_child_loses_the_python_pair_and_keeps_the_rest() {
        // The AppImage's AppRun exports PYTHONHOME and PYTHONPATH, and
        // a child that inherits them dies before its first import. All
        // four spawn sites come through `quiet`, so the scrub is
        // asserted on it: both variables marked removed, and an
        // unrelated variable left standing.
        let mut command = std::process::Command::new("engine");
        command.env("KEPT", "1");
        quiet(&mut command);
        for name in ["PYTHONHOME", "PYTHONPATH"] {
            assert!(
                command
                    .get_envs()
                    .any(|(key, value)| key == name && value.is_none()),
                "{name} still reaches the child"
            );
        }
        assert!(command
            .get_envs()
            .any(|(key, value)| key == "KEPT" && value.is_some()));
    }

    #[test]
    fn the_address_is_read_at_the_start_of_the_line_and_nowhere_else() {
        assert_eq!(
            manage_line("manage: http://127.0.0.1:5000/manage?t=abc"),
            Some("http://127.0.0.1:5000/manage?t=abc")
        );
        assert_eq!(manage_line("manage: x\r\n"), Some("x"));
        assert_eq!(manage_line("manage: x\n"), Some("x"));

        // The stopped line carries the same eight bytes ten in. A shell
        // that searched anywhere in the line would follow this one.
        let stopped = "[stopped] manage: http://127.0.0.1:5000/manage?t=abc";
        assert_eq!(&stopped[10..18], MANAGE_PREFIX);
        assert_eq!(manage_line(stopped), None);

        assert_eq!(manage_line(" manage: x"), None);
        assert_eq!(manage_line("manage:x"), None);
        assert_eq!(manage_line("manage:"), None);
        assert_eq!(manage_line(""), None);
    }

    #[test]
    fn only_the_exact_address_shape_is_ever_followed() {
        assert!(valid_launch_url("http://127.0.0.1:5000/manage?t=abc"));
        assert!(valid_launch_url("http://127.0.0.1:1/manage?t=A-b_9"));

        // Another authority, another scheme, another route, another
        // shape of token: every one of these is engine failure.
        assert!(!valid_launch_url("http://localhost:5000/manage?t=abc"));
        assert!(!valid_launch_url("http://127.0.0.2:5000/manage?t=abc"));
        assert!(!valid_launch_url("https://127.0.0.1:5000/manage?t=abc"));
        assert!(!valid_launch_url("http://[::1]:5000/manage?t=abc"));
        assert!(!valid_launch_url("http://127.0.0.1:/manage?t=abc"));
        assert!(!valid_launch_url("http://127.0.0.1:50x0/manage?t=abc"));
        assert!(!valid_launch_url("http://127.0.0.1:5000/manage/?t=abc"));
        assert!(!valid_launch_url("http://127.0.0.1:5000/setup?t=abc"));
        assert!(!valid_launch_url("http://127.0.0.1:5000/manage?t="));
        assert!(!valid_launch_url("http://127.0.0.1:5000/manage?t=a b"));
        assert!(!valid_launch_url("http://127.0.0.1:5000/manage?t=a&x=1"));
        assert!(!valid_launch_url("http://127.0.0.1:5000/manage?s=abc"));
        assert!(!valid_launch_url(""));
    }

    #[test]
    fn one_version_is_compared_against_another_by_its_numbers() {
        assert_eq!(at_least("0.1.0", "0.1.0"), Some(true));
        assert_eq!(at_least("0.2.0", "0.1.9"), Some(true));
        assert_eq!(at_least("0.1.9", "0.2.0"), Some(false));
        assert_eq!(at_least("1.0", "0.9.9"), Some(true));
        assert_eq!(at_least("0.1", "0.1.0"), Some(true));
        assert_eq!(at_least("0.1", "0.1.1"), Some(false));
        assert_eq!(at_least("10.0.0", "9.0.0"), Some(true));
        assert_eq!(at_least("0.1.0rc1", "0.1.0"), Some(true));
        assert_eq!(at_least("", "0.1.0"), None);
        assert_eq!(at_least("nothing", "0.1.0"), None);
        assert_eq!(at_least("0.1.0", "x"), None);
    }

    #[test]
    fn a_close_reads_the_session_token_off_the_window_address() {
        let target = close_target("http://127.0.0.1:5000/manage?s=abc123").unwrap();
        assert_eq!(target.authority, "127.0.0.1:5000");
        assert_eq!(target.target, "/manage");
        assert_eq!(target.token, "abc123");

        // A project view names its own folder in the query, and the post
        // has to reach the same route the page posts to.
        let project =
            close_target("http://127.0.0.1:5000/manage?root=C%3A%5Ca&s=abc123").unwrap();
        assert_eq!(project.target, "/manage?root=C%3A%5Ca");
        assert_eq!(project.token, "abc123");

        // The launch token is spent by the navigation; a window still on
        // it has nothing a close could be honoured with.
        assert_eq!(close_target("http://127.0.0.1:5000/manage?t=abc"), None);
        assert_eq!(close_target("http://127.0.0.1:5000/manage"), None);
        assert_eq!(close_target("http://tauri.localhost/index.html"), None);
        assert_eq!(close_target("http://127.0.0.1:5000/manage?s="), None);
        assert_eq!(close_target("http://127.0.0.1:5000/manage?s=a b"), None);
    }

    // --- app-shell-01: a walkthrough view still gets the clean close ---

    #[test]
    fn a_remembered_token_stands_in_for_an_address_that_lost_it() {
        // The walkthrough shape: the route with its folder, and no token.
        // On its own it is no target, as the test above pins; with the
        // token the run remembered it is the close the engine honours.
        let bare = "http://127.0.0.1:5000/manage?root=C%3A%5Ca";
        assert_eq!(close_target(bare), None);
        let target = close_target_or(bare, Some("abc123")).unwrap();
        assert_eq!(target.authority, "127.0.0.1:5000");
        assert_eq!(target.target, "/manage?root=C%3A%5Ca");
        assert_eq!(target.token, "abc123");

        // The address's own token wins, under its own rule: an empty or
        // a malformed one is still no target, remembered or not.
        assert_eq!(
            close_target_or("http://127.0.0.1:5000/manage?s=own", Some("abc123"))
                .unwrap()
                .token,
            "own"
        );
        assert_eq!(close_target_or("http://127.0.0.1:5000/manage?s=", Some("abc123")), None);
        assert_eq!(close_target_or("http://127.0.0.1:5000/manage?t=launch", Some("a b")), None);
        assert_eq!(close_target_or("http://127.0.0.1:5000/manage?t=launch", Some("")), None);
        // And an address off the engine's authority is no target at all.
        assert_eq!(close_target_or("http://tauri.localhost/index.html", Some("abc123")), None);
    }

    #[test]
    fn a_walkthrough_view_still_gets_the_clean_close() {
        // After a press on the manage page the window sits at
        // /manage?root=... with no session field: the walkthrough's form
        // posts to that address and is answered with a page, not a
        // redirect. The token the engine's own redirect put in the
        // address bar earlier is the one a close is honoured with, so
        // the run remembers it the first time the guard admits an address
        // carrying one, and the close falls back to it. The listener here
        // stands in for the engine: what it reads is the close post.
        let listener = TcpListener::bind("127.0.0.1:0").expect("a loopback port");
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel::<String>();
        std::thread::spawn(move || {
            let (mut stream, _) = match listener.accept() {
                Ok(accepted) => accepted,
                Err(_) => return,
            };
            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
            let mut request = Vec::new();
            let mut chunk = [0u8; 1024];
            while !request.ends_with(b"action=close") {
                match stream.read(&mut chunk) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => request.extend_from_slice(&chunk[..n]),
                }
            }
            let _ = stream.write_all(
                b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
            );
            let _ = tx.send(String::from_utf8_lossy(&request).to_string());
        });

        let home = "http://tauri.localhost/index.html";
        let authority = format!("127.0.0.1:{port}");
        let launch = format!("http://{authority}/manage?t=launch1");
        let engine = Engine::new();
        engine.begin();
        engine.spend(&launch, &authority);
        assert!(engine.may_navigate(&launch, Some(home)));
        // The engine's redirect: the session token arrives in the address.
        let session = format!("http://{authority}/manage?s=abc123");
        assert!(engine.may_navigate(&session, Some(home)));
        // The walkthrough press leaves the window here, with no token.
        let walkthrough = format!("http://{authority}/manage?root=C%3A%5Ca");
        assert!(engine.may_navigate(&walkthrough, Some(home)));

        // The child is already gone, so the wait after the post is nil.
        engine.held().exited.store(true, Ordering::SeqCst);
        engine.quiesce(&walkthrough);

        let request = rx
            .recv_timeout(Duration::from_secs(3))
            .expect("the close was posted, not skipped");
        assert!(
            request.starts_with("POST /manage?root=C%3A%5Ca HTTP/1.1\r\n"),
            "{request}"
        );
        assert!(request.ends_with("token=abc123&action=close"), "{request}");
    }

    #[test]
    fn the_close_post_is_the_body_the_engine_honours() {
        let target = close_target("http://127.0.0.1:5000/manage?s=abc123").unwrap();
        let request = close_request(&target);
        let (head, body) = request.split_once("\r\n\r\n").unwrap();
        assert_eq!(body, "token=abc123&action=close");
        assert!(head.starts_with("POST /manage HTTP/1.1\r\n"));
        assert!(head.contains("\r\nHost: 127.0.0.1:5000\r\n"));
        assert!(head.contains("\r\nContent-Length: 25\r\n"));
        assert!(head.contains("application/x-www-form-urlencoded"));
        // No render id: D8 honours a close that carries none, and this
        // one is the shell's own, not a document's.
        assert!(!request.contains("render"));
    }

    #[test]
    fn a_stop_takes_the_last_of_what_the_engine_said() {
        let said: Arc<Mutex<VecDeque<String>>> = Arc::new(Mutex::new(VecDeque::new()));
        said.lock().unwrap().push_back("one".to_string());
        said.lock().unwrap().push_back("two".to_string());
        let stop = with_tail(Slot::of(strings::FAIL_ENGINE_STOPPED, "1"), &said);
        assert_eq!(stop.detail, "1: one two");

        let quiet: Arc<Mutex<VecDeque<String>>> = Arc::new(Mutex::new(VecDeque::new()));
        let stop = with_tail(Slot::of(strings::FAIL_ENGINE_STOPPED, "1"), &quiet);
        assert_eq!(stop.detail, "1");
    }

    // --- X10e: every field copied into a request is checked ------------

    #[test]
    fn a_query_field_that_could_reach_the_request_line_is_refused() {
        // The class is the token check's, widened by the bytes a
        // percent-encoded field needs and by nothing else. A byte outside
        // it would land in the request line rather than in the query.
        assert!(close_target("http://127.0.0.1:5000/manage?root=C%3A%5Ca&s=a").is_some());
        assert!(close_target("http://127.0.0.1:5000/manage?page=2&s=a").is_some());

        // A space, a carriage return, a line feed: each one ends the
        // request line early and writes whatever follows as its own. The
        // field is left out of the request; the close itself still goes
        // to the route, because a close that is not posted is an engine
        // ended mid-write (app-shell-01).
        for bad in ["a b", "a\rb", "a\nb", "a\"b", "a<b"] {
            let target =
                close_target(&format!("http://127.0.0.1:5000/manage?root={bad}&s=a")).unwrap();
            assert_eq!(target.target, "/manage", "{bad:?}");
            assert_eq!(target.token, "a", "{bad:?}");
        }
        // A `*` is one a browser's serialiser leaves literal in a folder
        // name, and it changes nothing about the request line.
        assert_eq!(
            close_target("http://127.0.0.1:5000/manage?root=my*proj&s=a").unwrap().target,
            "/manage?root=my*proj"
        );

        // The path is copied forward too, and is checked the same way.
        assert_eq!(close_target("http://127.0.0.1:5000/man age?s=a"), None);
        assert_eq!(close_target("http://127.0.0.1:5000/man\rage?s=a"), None);

        // A field the target drops never reaches the request at all, so
        // the launch token's own bytes are not what refuses this one.
        assert_eq!(close_target("http://127.0.0.1:5000/manage?t=a b&s=abc").unwrap().target, "/manage");
    }

    // --- X6 and X7: a given-up child ends, and an old watcher is quiet -

    #[test]
    fn every_stop_with_no_address_to_follow_ends_the_child_it_gave_up_on() {
        // The three ways this shell can be left holding a child it will
        // never read again. Each one closes that run's own job, so a
        // retry cannot spawn a second engine beside the first.
        assert!(gives_up(strings::FAIL_SPAWN));
        assert!(gives_up(strings::FAIL_LINE_TIMEOUT));
        assert!(gives_up(strings::FAIL_LINE_UNPARSED));

        // An engine that stopped on its own has already ended, and the
        // stop that names it is not a give-up path.
        assert!(!gives_up(strings::FAIL_ENGINE_STOPPED));
        assert!(!gives_up(strings::FAIL_ENGINE_INSTALL));
        assert!(!gives_up(strings::FAIL_CHECKSUM));
    }

    #[test]
    fn a_watcher_from_a_replaced_run_says_nothing() {
        // Its own run is the current one and nobody asked for the stop:
        // this is the one case the window is sent back to the screen.
        assert!(watcher_speaks(1, 1, false));
        assert!(watcher_speaks(7, 7, false));

        // This shell asked for the stop, so the reader knows already.
        assert!(!watcher_speaks(1, 1, true));

        // A newer run is up. The old child's ending is old news, and
        // reporting it would take down the run that is live.
        assert!(!watcher_speaks(1, 2, false));
        assert!(!watcher_speaks(1, 9, false));
        assert!(!watcher_speaks(1, 2, true));
    }

    // PLAN-0021 phase 3 review: the update claims the close before it
    // installs, so a window close pressed meanwhile waits on it rather than
    // racing it. An install that fails before anything was closed gives
    // the claim back, or the next window close would find it taken and
    // leave the window standing.
    #[test]
    fn a_close_claim_given_back_can_be_taken_again() {
        let engine = Engine::new();
        assert!(engine.claim_closing());
        assert!(!engine.claim_closing(), "a claim is taken once");
        engine.release_closing();
        assert!(engine.claim_closing(), "a claim given back is free");
    }

    // --- X8: the spent address, and the two origins this window knows --

    #[test]
    fn an_address_is_read_for_its_origin_and_nothing_else() {
        assert_eq!(
            origin_of("http://127.0.0.1:5000/manage?s=a").as_deref(),
            Some("http://127.0.0.1:5000")
        );
        assert_eq!(
            origin_of("http://TAURI.localhost/index.html").as_deref(),
            Some("http://tauri.localhost")
        );
        assert_eq!(origin_of("http://127.0.0.1:5000").as_deref(), Some("http://127.0.0.1:5000"));
        assert_eq!(origin_of("about:blank"), None);
        assert_eq!(origin_of("https://"), None);
        assert_eq!(origin_of(""), None);
    }

    #[test]
    fn the_spent_launch_address_is_refused_however_it_is_reached() {
        let home = "http://tauri.localhost/index.html";
        let launch = "http://127.0.0.1:5000/manage?t=abc123";
        let spent = fingerprint(launch_token(launch).expect("the token"));

        // The fingerprint is a digest and nothing that can be read back
        // into the token it was taken over.
        assert_eq!(spent.len(), 64);
        assert!(!spent.contains("abc123"));

        // Before it is spent the address is one this window may go to.
        assert!(navigable(launch, Some(home), Some("127.0.0.1:5000"), None));

        // After, it is refused — a history Back reaches for it first, and
        // a refused navigation is a navigation that does not happen.
        assert!(!navigable(launch, Some(home), Some("127.0.0.1:5000"), Some(&spent)));

        // The engine's own pages on the same authority are still reached.
        assert!(navigable(
            "http://127.0.0.1:5000/manage?s=session",
            Some(home),
            Some("127.0.0.1:5000"),
            Some(&spent)
        ));
        assert!(navigable(home, Some(home), Some("127.0.0.1:5000"), Some(&spent)));
        assert!(navigable(
            "http://tauri.localhost/index.html?x=1",
            Some(home),
            Some("127.0.0.1:5000"),
            Some(&spent)
        ));

        // A different token on the same route is a different address, and
        // it is refused for the other reason: this window goes to the two
        // origins it knows and to nowhere else.
        assert!(navigable(
            "http://127.0.0.1:5000/manage?t=other",
            Some(home),
            Some("127.0.0.1:5000"),
            Some(&spent)
        ));
        assert!(!navigable(
            "http://127.0.0.1:5001/manage?t=abc123",
            Some(home),
            Some("127.0.0.1:5000"),
            Some(&spent)
        ));
        assert!(!navigable(
            "https://example.test/",
            Some(home),
            Some("127.0.0.1:5000"),
            Some(&spent)
        ));
        assert!(!navigable(
            "http://localhost:5000/manage?s=session",
            Some(home),
            Some("127.0.0.1:5000"),
            Some(&spent)
        ));
        assert!(!navigable("about:blank", Some(home), Some("127.0.0.1:5000"), None));

        // Before the engine is up there is no second origin at all.
        assert!(!navigable(
            "http://127.0.0.1:5000/manage?s=session",
            Some(home),
            None,
            None
        ));

        // And before the window has drawn its own screen there is no
        // remembered address to compare against: the only navigation that
        // can happen then is the one the window was configured with.
        assert!(navigable(home, None, None, None));
    }

    /// The Linux half of D9: the child leads its own group, and dropping
    /// the run's Job ends it. The Windows half needs a real job object
    /// and window and is proved by use rather than by a unit.
    #[cfg(unix)]
    #[test]
    fn dropping_the_run_job_ends_the_child_it_held() {
        let mut command = std::process::Command::new("/bin/sh");
        command
            .arg("-c")
            .arg("sleep 30")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        let mut child = grouped(quiet(&mut command)).spawn().expect("a child to end");
        let job = own_job(&child).expect("the run's own group");
        drop(job);
        let status = child.wait().expect("the child is reaped");
        assert!(!status.success(), "the child outlived its group");
    }

    /// The other half of the same hold: a job stood down after the reap
    /// fires no kill, so a reissued id is never aimed at.
    #[cfg(unix)]
    #[test]
    fn a_job_stood_down_after_the_reap_fires_no_kill() {
        let mut command = std::process::Command::new("/bin/sh");
        command
            .arg("-c")
            .arg("exit 0")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        let mut child = grouped(quiet(&mut command)).spawn().expect("a child");
        let job = own_job(&child).expect("the run's own group");
        let status = child.wait().expect("the child is reaped");
        assert!(status.success());
        // Disarmed, then dropped: the drop must not signal anything.
        stand_down(job);
    }

    #[test]
    fn a_launch_token_is_read_only_out_of_the_shape_that_carries_one() {
        assert_eq!(launch_token("http://127.0.0.1:5000/manage?t=abc"), Some("abc"));
        assert_eq!(launch_token("http://127.0.0.1:5000/manage?s=abc"), None);
        assert_eq!(launch_token("http://127.0.0.1:5000/manage?t="), None);
        assert_eq!(launch_token("http://127.0.0.1:5000/manage?t=a b"), None);
        assert_eq!(launch_token("http://tauri.localhost/index.html"), None);
        assert_ne!(fingerprint("abc"), fingerprint("abd"));
        assert_eq!(fingerprint("abc"), fingerprint("abc"));
    }
}
