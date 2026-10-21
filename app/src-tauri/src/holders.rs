//! PLAN-0021 phase 2 `holders.rs`: the programs that hold the engine,
//! found and stopped so the engine can be replaced while they run.
//!
//! On Windows a running program keeps its own file and the files it has
//! loaded locked, so uv cannot take an engine out from under the servers
//! that programs using RadialRig started from it. BUG-0010 met exactly
//! that. Decided 7 settles what may be stopped, and this module holds to
//! it word for word: a process of the person's own account whose full
//! image path lies inside the engine's folder in uv's tool folder, or is
//! one of the engine's five launchers in uv's launcher folder, and never
//! this app's own process. Nothing is inferred from a name alone: a
//! `radialrig-read.exe` anywhere else is somebody else's program.
//!
//! The stop is immediate, because Windows gives these servers no gentler
//! one. A program saving a change through RadialRig at that moment can
//! leave it half written; Decided 7 accepts that trade, and the screen
//! that runs the swap is the place that says so.
//!
//! On Linux nothing is listed and nothing is stopped. A running program
//! there does not stop its files being replaced, so the swap has nothing
//! to clear out of the way.
//!
//! The engine swap in `bootstrap.rs` is the one caller: it stops these
//! programs before each attempt to replace the engine, and names the ones
//! still running when it gives up (prescription 4).

use std::path::{Path, PathBuf};

use crate::bootstrap::ENGINE_TOOL;

/// The engine's five launchers, as the package names them
/// (`pyproject.toml`, `[project.scripts]`). uv writes each one into its
/// launcher folder, with `.exe` on Windows.
pub const LAUNCHERS: [&str; 5] = [
    "radialrig",
    "radialrig-read",
    "radialrig-write",
    "radialrig-validate",
    "radialrig-index",
];

/// The five launchers' full paths in uv's launcher folder, as this
/// platform names them. The swap reads them as they stand when it begins,
/// to know its own engine's remains later.
pub fn launcher_files(bin_dir: &Path) -> Vec<PathBuf> {
    LAUNCHERS
        .iter()
        .map(|name| bin_dir.join(launcher_name(name, cfg!(windows))))
        .collect()
}

/// One launcher's file name: with `.exe` on Windows, bare on Linux.
fn launcher_name(name: &str, windows: bool) -> String {
    if windows {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

/// One process that holds the engine: its id, and the full path of the
/// program it runs, which is what a sentence names it by.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Holder {
    pub pid: u32,
    pub image: PathBuf,
}

/// What one stop did.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Stopped {
    /// The processes that were stopped and are gone.
    pub stopped: Vec<Holder>,
    /// What still holds the engine once the stop is over, read afresh:
    /// a process that would not stop, and any that started meanwhile.
    /// Empty means nothing running from the engine's folders was found.
    pub left: Vec<Holder>,
}

/// The processes of this account that hold the engine, read now.
///
/// `tool_dir` is the folder `uv tool dir` names and `bin_dir` the one
/// `uv tool dir --bin` names. The engine's own folder is `radialrig`
/// inside the first; its launchers are the five above inside the second.
/// This app's own process is never on the list.
pub fn list(tool_dir: &Path, bin_dir: &Path) -> Vec<Holder> {
    let rule = Engine::new(tool_dir, bin_dir, cfg!(windows));
    scan(&rule, LOOK)
        .into_iter()
        .map(|(holder, _)| holder)
        .collect()
}

/// Stop every process `list` would name, wait for each to be gone, and
/// read the list again.
///
/// Each process is stopped through the same open process object its
/// image and account were read through, so the stop lands on the process
/// that was checked even if its id is reused meanwhile. What is still
/// running afterwards comes back in `left` for the swap to name.
pub fn stop(tool_dir: &Path, bin_dir: &Path) -> Stopped {
    let rule = Engine::new(tool_dir, bin_dir, cfg!(windows));
    let stopped = end_all(scan(&rule, END));
    let left = scan(&rule, LOOK)
        .into_iter()
        .map(|(holder, _)| holder)
        .collect();
    Stopped { stopped, left }
}

// --- which images hold the engine ---------------------------------------

/// The rule, with both folders already read into the form paths are
/// compared in.
struct Engine {
    /// Whether paths fold the way Windows spells them.
    windows: bool,
    /// The engine's folder, `<tool dir>/radialrig`, by component. Empty
    /// when the tool folder given was not an absolute path, which names
    /// nothing.
    folder: Vec<String>,
    /// Each launcher's full path, by component.
    launchers: Vec<Vec<String>>,
}

impl Engine {
    fn new(tool_dir: &Path, bin_dir: &Path, windows: bool) -> Self {
        let folder = if tool_dir.is_absolute() {
            components(&tool_dir.join(ENGINE_TOOL).to_string_lossy(), windows)
        } else {
            Vec::new()
        };
        let launchers = if bin_dir.is_absolute() {
            LAUNCHERS
                .iter()
                .map(|name| {
                    let file = launcher_name(name, windows);
                    components(&bin_dir.join(file).to_string_lossy(), windows)
                })
                .collect()
        } else {
            Vec::new()
        };
        Engine { windows, folder, launchers }
    }

    /// Whether a program at this full path holds the engine: it lies
    /// inside the engine's folder, below it rather than being it, or it
    /// is one of the five launchers exactly.
    fn holds(&self, image: &Path) -> bool {
        let image = components(&image.to_string_lossy(), self.windows);
        let inside = !self.folder.is_empty()
            && image.len() > self.folder.len()
            && image[..self.folder.len()] == self.folder[..];
        inside || self.launchers.iter().any(|launcher| *launcher == image)
    }
}

/// A path's components as this rule compares them.
///
/// On Windows both separators split, case folds, and the long-path
/// prefix `\\?\` is the same folder as the path without it. Empty
/// components and `.` drop out, so a trailing separator or a doubled one
/// changes nothing. Comparing whole components is what keeps a folder
/// named `radialrig-old` from reading as inside `radialrig`.
fn components(path: &str, windows: bool) -> Vec<String> {
    if !windows {
        return path
            .split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .map(str::to_string)
            .collect();
    }
    let path = if let Some(rest) = path.strip_prefix("\\\\?\\UNC\\") {
        format!("\\\\{rest}")
    } else if let Some(rest) = path.strip_prefix("\\\\?\\") {
        rest.to_string()
    } else {
        path.to_string()
    };
    path.split(['\\', '/'])
        .filter(|part| !part.is_empty() && *part != ".")
        .map(str::to_lowercase)
        .collect()
}

// --- the processes, on Windows ------------------------------------------

/// What a scan opens each process for: reading only, or reading and
/// ending. Both include waiting on it, which is how a process that has
/// already ended is told apart from one still running.
#[cfg(windows)]
#[derive(Clone, Copy)]
struct Access(windows::Win32::System::Threading::PROCESS_ACCESS_RIGHTS);

#[cfg(windows)]
const LOOK: Access = {
    use windows::Win32::System::Threading::{
        PROCESS_ACCESS_RIGHTS, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
    };
    Access(PROCESS_ACCESS_RIGHTS(
        PROCESS_QUERY_LIMITED_INFORMATION.0 | PROCESS_SYNCHRONIZE.0,
    ))
};

#[cfg(windows)]
const END: Access = {
    use windows::Win32::System::Threading::{PROCESS_ACCESS_RIGHTS, PROCESS_TERMINATE};
    Access(PROCESS_ACCESS_RIGHTS(LOOK.0 .0 | PROCESS_TERMINATE.0))
};

/// How long the stop waits, all told, for the processes it ended to be
/// gone. The same five seconds the app gives its own engine to close.
#[cfg(windows)]
const WAIT: std::time::Duration = std::time::Duration::from_secs(5);

/// The exit code a stopped process is given.
#[cfg(windows)]
const ENDED: u32 = 1;

/// One open process or token, closed when it goes out of scope.
#[cfg(windows)]
struct Open(windows::Win32::Foundation::HANDLE);

#[cfg(windows)]
impl Drop for Open {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

/// Every process of this account, other than this one, whose image the
/// rule takes, each with the open process object its image and account
/// were read through.
#[cfg(windows)]
fn scan(rule: &Engine, access: Access) -> Vec<(Holder, Open)> {
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows::Win32::System::Threading::{GetCurrentProcess, GetCurrentProcessId};

    let mut found = Vec::new();
    let own = unsafe { GetCurrentProcessId() };
    // This app's own account, read the way every other process's is.
    // Without it there is nothing to compare against, so nothing is
    // taken: a stop that cannot tell whose a process is does not stop it.
    let me = match account(unsafe { GetCurrentProcess() }) {
        Some(me) => me,
        None => return found,
    };
    let snapshot = match unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) } {
        Ok(snapshot) => Open(snapshot),
        Err(_) => return found,
    };
    let mut entry = PROCESSENTRY32W {
        dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
        ..Default::default()
    };
    // One buffer for every image path, as long as Windows lets a path be.
    let mut buffer = vec![0u16; 32768];
    let mut more = unsafe { Process32FirstW(snapshot.0, &mut entry) }.is_ok();
    while more {
        let pid = entry.th32ProcessID;
        if pid != own && pid != 0 {
            if let Some(held) = examine(pid, access, rule, &me, &mut buffer) {
                found.push(held);
            }
        }
        more = unsafe { Process32NextW(snapshot.0, &mut entry) }.is_ok();
    }
    found
}

/// One process, opened once and judged through that one open object: it
/// is still running, its image is one the rule takes, and it runs as
/// this account. A process this account may not open is not this
/// account's to stop.
#[cfg(windows)]
fn examine(
    pid: u32,
    access: Access,
    rule: &Engine,
    me: &[u8],
    buffer: &mut [u16],
) -> Option<(Holder, Open)> {
    use std::os::windows::ffi::OsStringExt;
    use windows::core::PWSTR;
    use windows::Win32::Foundation::WAIT_OBJECT_0;
    use windows::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, WaitForSingleObject, PROCESS_NAME_WIN32,
    };

    let open = Open(unsafe { OpenProcess(access.0, false, pid) }.ok()?);
    // A process that has already ended holds nothing, even while some
    // other program still keeps it open.
    if unsafe { WaitForSingleObject(open.0, 0) } == WAIT_OBJECT_0 {
        return None;
    }
    let mut size = buffer.len() as u32;
    unsafe {
        QueryFullProcessImageNameW(open.0, PROCESS_NAME_WIN32, PWSTR(buffer.as_mut_ptr()), &mut size)
    }
    .ok()?;
    let image = PathBuf::from(std::ffi::OsString::from_wide(&buffer[..size as usize]));
    if !rule.holds(&image) {
        return None;
    }
    if account(open.0)? != me {
        return None;
    }
    Some((Holder { pid, image }, open))
}

/// The account a process runs as, as the bytes of its security
/// identifier, or nothing when it cannot be read.
#[cfg(windows)]
fn account(process: windows::Win32::Foundation::HANDLE) -> Option<Vec<u8>> {
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::Security::{
        GetLengthSid, GetTokenInformation, IsValidSid, TokenUser, TOKEN_QUERY, TOKEN_USER,
    };
    use windows::Win32::System::Threading::OpenProcessToken;

    let mut token = HANDLE::default();
    unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut token) }.ok()?;
    let token = Open(token);
    let mut size = 0u32;
    let _ = unsafe { GetTokenInformation(token.0, TokenUser, None, 0, &mut size) };
    if size == 0 {
        return None;
    }
    // Whole words, so the record at the front is aligned for the pointer
    // it carries.
    let mut words = vec![0u64; (size as usize).div_ceil(8)];
    unsafe {
        GetTokenInformation(
            token.0,
            TokenUser,
            Some(words.as_mut_ptr().cast()),
            size,
            &mut size,
        )
    }
    .ok()?;
    let user = unsafe { &*(words.as_ptr() as *const TOKEN_USER) };
    let sid = user.User.Sid;
    if sid.is_invalid() || !unsafe { IsValidSid(sid) }.as_bool() {
        return None;
    }
    let length = unsafe { GetLengthSid(sid) } as usize;
    Some(unsafe { std::slice::from_raw_parts(sid.0 as *const u8, length) }.to_vec())
}

/// Stop every process found, then wait for each to be gone.
///
/// Every stop is asked for first and the waits share one deadline, so a
/// hundred servers cost one wait rather than a hundred. A process is
/// counted as stopped only once it has ended; one that has not by the
/// deadline is left for the fresh look that follows to name.
#[cfg(windows)]
fn end_all(found: Vec<(Holder, Open)>) -> Vec<Holder> {
    use windows::Win32::Foundation::WAIT_OBJECT_0;
    use windows::Win32::System::Threading::{TerminateProcess, WaitForSingleObject};

    for (_, open) in &found {
        let _ = unsafe { TerminateProcess(open.0, ENDED) };
    }
    let deadline = std::time::Instant::now() + WAIT;
    found
        .into_iter()
        .filter_map(|(holder, open)| {
            let left = deadline.saturating_duration_since(std::time::Instant::now());
            let wait = u32::try_from(left.as_millis()).unwrap_or(u32::MAX);
            let gone = unsafe { WaitForSingleObject(open.0, wait) } == WAIT_OBJECT_0;
            gone.then_some(holder)
        })
        .collect()
}

// --- Linux: nothing to stop ---------------------------------------------

#[cfg(not(windows))]
type Access = ();
#[cfg(not(windows))]
const LOOK: Access = ();
#[cfg(not(windows))]
const END: Access = ();
#[cfg(not(windows))]
type Open = ();

#[cfg(not(windows))]
fn scan(_rule: &Engine, _access: Access) -> Vec<(Holder, Open)> {
    Vec::new()
}

#[cfg(not(windows))]
fn end_all(_found: Vec<(Holder, Open)>) -> Vec<Holder> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(windows)]
    const TOOLS: &str = "C:\\Users\\reader\\AppData\\Roaming\\uv\\tools";
    #[cfg(windows)]
    const BIN: &str = "C:\\Users\\reader\\.local\\bin";

    #[cfg(windows)]
    fn windows_rule() -> Engine {
        Engine::new(Path::new(TOOLS), Path::new(BIN), true)
    }

    fn at(path: &str) -> PathBuf {
        PathBuf::from(path)
    }

    // The folding is tested on every platform, so the Linux leg of the
    // suite holds the Windows spelling too. The rule itself only takes an
    // absolute folder, and `Path` reads a drive letter as absolute only on
    // Windows, so the rule's Windows cases run there and its Linux cases
    // run on Linux.

    #[test]
    #[cfg(windows)]
    fn a_program_inside_the_engine_folder_holds_it_and_a_sibling_does_not() {
        let rule = windows_rule();
        assert!(rule.holds(&at(&format!("{TOOLS}\\radialrig\\Scripts\\python.exe"))));
        assert!(rule.holds(&at(&format!("{TOOLS}\\radialrig\\Lib\\x\\y.exe"))));

        // A sibling sharing the prefix is a different folder, compared
        // whole: neither the name nor its first letters are enough.
        assert!(!rule.holds(&at(&format!("{TOOLS}\\radialrig-old\\Scripts\\python.exe"))));
        assert!(!rule.holds(&at(&format!("{TOOLS}\\radialrigs\\python.exe"))));
        assert!(!rule.holds(&at(&format!("{TOOLS}\\radialrig.exe"))));
        // The engine folder must sit directly in uv's tool folder.
        assert!(!rule.holds(&at(&format!("{TOOLS}\\other\\radialrig\\python.exe"))));
        assert!(!rule.holds(&at(&format!("{TOOLS}\\other-tool\\Scripts\\python.exe"))));
        // The folder itself is not a program inside it.
        assert!(!rule.holds(&at(&format!("{TOOLS}\\radialrig"))));
    }

    #[test]
    #[cfg(windows)]
    fn each_launcher_holds_it_and_nothing_else_in_the_launcher_folder_does() {
        let rule = windows_rule();
        for name in LAUNCHERS {
            assert!(rule.holds(&at(&format!("{BIN}\\{name}.exe"))), "{name}");
            // The same name anywhere else is somebody else's program.
            assert!(!rule.holds(&at(&format!("C:\\elsewhere\\{name}.exe"))), "{name}");
            assert!(!rule.holds(&at(&format!("{BIN}\\nested\\{name}.exe"))), "{name}");
            // And on Windows a launcher is its `.exe`, not a bare name.
            assert!(!rule.holds(&at(&format!("{BIN}\\{name}"))), "{name}");
        }
        assert!(!rule.holds(&at(&format!("{BIN}\\uv.exe"))));
        assert!(!rule.holds(&at(&format!("{BIN}\\radialrig-old.exe"))));
        assert!(!rule.holds(&at(&format!("{BIN}\\radialrig-reader.exe"))));
    }

    #[test]
    #[cfg(windows)]
    fn windows_spelling_folds_case_separators_and_the_long_path_prefix() {
        let rule = windows_rule();
        let spellings = [
            "c:\\users\\READER\\appdata\\roaming\\UV\\TOOLS\\RadialRig\\scripts\\python.exe",
            "C:/Users/reader/AppData/Roaming/uv/tools/radialrig/Scripts/python.exe",
            "C:\\Users\\reader\\AppData\\Roaming\\uv\\tools\\\\radialrig\\.\\Scripts\\python.exe",
            "\\\\?\\C:\\Users\\reader\\AppData\\Roaming\\uv\\tools\\radialrig\\Scripts\\python.exe",
            "\\\\?\\c:\\users\\reader\\.LOCAL\\bin\\RADIALRIG-WRITE.EXE",
        ];
        for image in spellings {
            assert!(rule.holds(&at(image)), "{image}");
        }

        // The folders may be spelled any of those ways too.
        let rule = Engine::new(
            Path::new("c:/users/reader/appdata/roaming/uv/tools/"),
            Path::new("\\\\?\\C:\\Users\\reader\\.local\\bin\\"),
            true,
        );
        assert!(rule.holds(&at(&format!("{TOOLS}\\radialrig\\Scripts\\python.exe"))));
        assert!(rule.holds(&at(&format!("{BIN}\\radialrig-read.exe"))));
        assert!(!rule.holds(&at(&format!("{TOOLS}\\radialrig-old\\Scripts\\python.exe"))));
    }

    #[test]
    fn the_components_are_compared_whole_and_folded_only_on_windows() {
        assert_eq!(
            components("C:\\A\\b/C\\\\.\\d\\", true),
            vec!["c:", "a", "b", "c", "d"]
        );
        assert_eq!(components("\\\\?\\C:\\A", true), components("C:\\a", true));
        assert_eq!(components("/home/A/./b//c/", false), vec!["home", "A", "b", "c"]);
        // Without the Windows fold, case and the backslash are the path's
        // own letters.
        assert_ne!(components("/home/A", false), components("/home/a", false));
        assert_eq!(components("/home/a\\b", false), vec!["home", "a\\b"]);
    }

    #[test]
    #[cfg(not(windows))]
    fn a_linux_launcher_is_its_bare_name_and_case_is_its_own() {
        let rule = Engine::new(
            Path::new("/home/reader/.local/share/uv/tools"),
            Path::new("/home/reader/.local/bin"),
            false,
        );
        let folder = "/home/reader/.local/share/uv/tools";
        assert!(rule.holds(&at(&format!("{folder}/radialrig/bin/python"))));
        assert!(!rule.holds(&at(&format!("{folder}/radialrig-old/bin/python"))));
        assert!(!rule.holds(&at(&format!("{folder}/RadialRig/bin/python"))));
        for name in LAUNCHERS {
            assert!(rule.holds(&at(&format!("/home/reader/.local/bin/{name}"))), "{name}");
            assert!(!rule.holds(&at(&format!("/elsewhere/{name}"))), "{name}");
        }

        // And nothing runs on Linux, whatever the folders.
        assert!(list(Path::new(folder), Path::new("/home/reader/.local/bin")).is_empty());
        assert_eq!(
            stop(Path::new(folder), Path::new("/home/reader/.local/bin")),
            Stopped::default()
        );
    }

    #[test]
    fn a_folder_that_is_not_absolute_names_nothing() {
        // An empty or relative folder would otherwise make the rule a
        // question about any program whose path happens to share a tail.
        let rule = Engine::new(Path::new(""), Path::new("bin"), cfg!(windows));
        assert!(rule.folder.is_empty());
        assert!(rule.launchers.is_empty());
        let somewhere = std::env::temp_dir().join(ENGINE_TOOL).join("python.exe");
        assert!(!rule.holds(&somewhere));
        assert!(!rule.holds(Path::new("radialrig.exe")));
        assert!(!rule.holds(Path::new("bin/radialrig.exe")));
    }

    // --- real processes, started from copies in a scratch folder ---------
    //
    // Every process these tests list or stop is one they started
    // themselves, from a copy of a Windows program placed in a scratch
    // folder of their own, and the rule is pointed at that folder. The
    // real engine folders are never named here, and every stand-in is
    // ended and its folder taken away when the test ends, a failed test
    // included. A stand-in left behind by a test that was itself killed
    // ends on its own within a minute.

    #[cfg(windows)]
    struct Scratch {
        root: PathBuf,
        running: Vec<std::process::Child>,
    }

    #[cfg(windows)]
    impl Scratch {
        fn new(name: &str) -> Self {
            let root = std::env::temp_dir()
                .join(format!("radialrig-holders-{}-{name}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(&root).expect("a scratch folder");
            Scratch { root, running: Vec::new() }
        }

        /// The scratch root as the system spells it, so a short-name
        /// temp folder cannot make the rule miss.
        fn folder(&self, relative: &str) -> PathBuf {
            std::fs::canonicalize(&self.root).expect("the scratch root").join(relative)
        }

        /// A copy of the system's ping placed at `relative`, started so it
        /// waits about half a minute. Its own id comes back.
        fn start(&mut self, relative: &str) -> u32 {
            let system = std::env::var_os("SystemRoot").expect("the Windows folder");
            let program = PathBuf::from(system).join("System32").join("PING.EXE");
            let copy = self.root.join(relative);
            std::fs::create_dir_all(copy.parent().expect("a folder")).expect("a folder");
            std::fs::copy(&program, &copy).expect("a stand-in program");
            let child = std::process::Command::new(&copy)
                .args(["-n", "30", "127.0.0.1"])
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn()
                .expect("the stand-in starts");
            let pid = child.id();
            self.running.push(child);
            pid
        }

        /// Whether the stand-in with this id is still running.
        fn running(&mut self, pid: u32) -> bool {
            self.running
                .iter_mut()
                .find(|child| child.id() == pid)
                .map(|child| matches!(child.try_wait(), Ok(None)))
                .unwrap_or(false)
        }
    }

    #[cfg(windows)]
    impl Drop for Scratch {
        fn drop(&mut self) {
            for child in &mut self.running {
                let _ = child.kill();
                let _ = child.wait();
            }
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    #[cfg(windows)]
    fn ids(found: &[Holder]) -> Vec<u32> {
        let mut ids: Vec<u32> = found.iter().map(|holder| holder.pid).collect();
        ids.sort_unstable();
        ids
    }

    #[test]
    #[cfg(windows)]
    fn the_stop_takes_the_engine_and_its_launcher_and_leaves_every_bystander() {
        let mut scratch = Scratch::new("stop");
        let inside = scratch.start("tools\\radialrig\\Scripts\\python.exe");
        let launcher = scratch.start("bin\\radialrig-read.exe");
        // A sibling folder sharing the prefix, a program in the launcher
        // folder that is not a launcher, and a launcher's name in a folder
        // that is not the launcher folder.
        let sibling = scratch.start("tools\\radialrig-old\\Scripts\\python.exe");
        let neighbour = scratch.start("bin\\uv.exe");
        let namesake = scratch.start("elsewhere\\radialrig-read.exe");
        let tools = scratch.folder("tools");
        let bin = scratch.folder("bin");

        let mut wanted = vec![inside, launcher];
        wanted.sort_unstable();
        let found = list(&tools, &bin);
        assert_eq!(ids(&found), wanted, "{found:?}");
        // Each is named by the program it runs, the path a sentence uses.
        for holder in &found {
            let relative = if holder.pid == inside {
                "tools\\radialrig\\Scripts\\python.exe"
            } else {
                "bin\\radialrig-read.exe"
            };
            assert_eq!(
                components(&holder.image.to_string_lossy(), true),
                components(&scratch.folder(relative).to_string_lossy(), true),
            );
        }

        let done = stop(&tools, &bin);
        assert_eq!(ids(&done.stopped), wanted, "{done:?}");
        assert!(done.left.is_empty(), "{done:?}");
        assert!(!scratch.running(inside));
        assert!(!scratch.running(launcher));
        for bystander in [sibling, neighbour, namesake] {
            assert!(scratch.running(bystander), "a bystander was stopped");
        }
        assert!(list(&tools, &bin).is_empty());
    }

    #[test]
    #[cfg(windows)]
    fn the_app_itself_is_never_on_the_list() {
        // The test process stands in for the app (Invariant 3). A rule
        // whose engine folder is the one this process runs from does hold
        // its image, and the scan still leaves it off. Read only: nothing
        // is stopped under a rule this wide.
        let me = std::env::current_exe().expect("this program");
        let rule = Engine {
            windows: true,
            folder: components(&me.parent().expect("a folder").to_string_lossy(), true),
            launchers: vec![components(&me.to_string_lossy(), true)],
        };
        assert!(rule.holds(&me), "the rule must take this program to mean anything");
        let own = std::process::id();
        assert!(scan(&rule, LOOK).iter().all(|(holder, _)| holder.pid != own));
    }

    #[test]
    #[cfg(windows)]
    fn another_accounts_programs_are_never_on_the_list() {
        // These run as the system on every Windows, never as the person.
        // A rule over the folder they run from takes the person's own
        // programs there and none of these. Read only: nothing is stopped
        // under a rule this wide.
        let system = std::env::var_os("SystemRoot").expect("the Windows folder");
        let folder = PathBuf::from(system).join("System32");
        let rule = Engine {
            windows: true,
            folder: components(&folder.to_string_lossy(), true),
            launchers: Vec::new(),
        };
        let theirs = ["smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe"];
        for (holder, _) in scan(&rule, LOOK) {
            let name = holder
                .image
                .file_name()
                .map(|name| name.to_string_lossy().to_ascii_lowercase())
                .unwrap_or_default();
            assert!(!theirs.contains(&name.as_str()), "{holder:?}");
        }
    }
}
