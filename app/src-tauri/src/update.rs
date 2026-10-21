//! PLAN-0021 phase 3 `update.rs`: the app asks whether a newer RadialRig
//! exists, and installs it when the person presses for it.
//!
//! Decided 8 settles when. The app asks once per launch, on Windows and
//! when it runs as the Linux AppImage, and only once its engine step has
//! finished: at the engine's page, or at a stop. The engine step runs uv
//! inside the app's job object, which ends with the app
//! (`attach.rs:890-931`), so an update that closed the app during that
//! step would cut an engine replacement short. A .deb never asks, because
//! the updater cannot replace a .deb install. A check that fails, or finds
//! the machine offline, says nothing.
//!
//! When a newer version exists, one dialog names both versions and says
//! what updating does, with the presses "Update now" and "Later". Later
//! does nothing and keeps nothing, so the next launch asks again. Update
//! now downloads the new version, and the plugin checks its signature
//! against the owner's public key in `tauri.conf.json` before a byte of it
//! is written anywhere; that check cannot be turned off (Verified 14).
//! The app then reads which file the signature was made over, and goes on
//! only when that name carries the version on offer, so an older signed
//! release cannot be offered under a newer number (the owner's decision of
//! 2026-10-03). What follows is the plugin's own, read in
//! tauri-plugin-updater 2.12.0:
//!
//! - On Windows, `install` writes the installer out, runs this module's
//!   hook, starts the installer in passive mode with the switch that opens
//!   the app again, and ends this process with `std::process::exit`
//!   (`src/updater.rs:841-883`). Nothing at the end of the program runs,
//!   so the hook closes the engine's page the way a window close does.
//! - On Linux, `install` replaces the AppImage file in place, and puts the
//!   old one back if the write fails (`src/updater.rs:1053-1124`). This
//!   module then closes the engine's page the same way and restarts the
//!   app on the new file.
//!
//! The app is never closed while an engine step runs. A press of Run This
//! Step Again can start a pass while the dialog is open or the download
//! runs, so before anything closes this module takes the flag a pass takes
//! when it starts (`Shell::hold`): it waits out a running pass, and while
//! it holds the flag no new pass starts. Nor does an update end the process
//! while a window close is giving the engine its five seconds: it takes
//! the same claim that close takes, and does not install when the close
//! has it.
//!
//! A failure says so in a dialog of its own: the update was not installed,
//! nothing changed, and why, in plain words. All of it runs in Rust. The
//! window's capability grants none of the updater's commands (Decided 9,
//! Invariant 6), and a plugin called from Rust passes no capability check:
//! `UpdaterExt` reads the plugin's managed state directly
//! (`src/lib.rs:78-130`), where a page reaches the plugin only through its
//! four commands, each behind a permission (`permissions/default.toml`).
//!
//! One side effect of the plugin's own: its first check installs rustls's
//! ring provider as this process's default when none is set
//! (`src/updater.rs:491-495`), and reqwest prefers a process default, so a
//! download the app makes later in the same launch uses ring where it
//! would have used aws-lc-rs. Both are rustls, checked by the same
//! platform verifier.

use std::ffi::OsStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::{Error, UpdaterExt};

use crate::bootstrap;
use crate::strings;

/// How long an update that found an engine step running waits before it
/// looks again.
const WAIT: Duration = Duration::from_millis(250);

/// How long the ask may take. A release page that does not answer in
/// this time is a check that failed, and a failed check says nothing.
const CHECK_WAIT: Duration = Duration::from_secs(30);

/// How long the download may take from start to finish. The plugin keeps
/// no wait of its own for it (`Update::timeout` comes back empty from the
/// check), and without one a link that went quiet would leave the person
/// who pressed Update now looking at nothing; with it, they are told the
/// new version did not download. Ten minutes leaves room for the AppImage,
/// the larger of the two files, on a slow line.
const DOWNLOAD_WAIT: Duration = Duration::from_secs(600);

/// The one ask this launch gets.
static ASKED: Once = Once::new();

// --- whether, and when, to ask -------------------------------------------

/// Whether a build asks at all (Decided 8): on Windows always, and on Linux
/// only when it runs as the AppImage. The AppImage's runtime says so by
/// setting `APPIMAGE` to the file's own path, which is the file the plugin
/// replaces. A .deb install runs with no such variable and never asks, and
/// an empty one names no file, so it reads as unset.
pub fn asks_here(windows: bool, appimage: Option<&OsStr>) -> bool {
    windows || appimage.is_some_and(|path| !path.is_empty())
}

/// Whether this moment may ask: the build asks, no engine step runs, and
/// this launch has not asked yet.
pub fn due(here: bool, running: bool, asked: bool) -> bool {
    here && !running && !asked
}

/// The one ask a launch gets, spent by the first moment `due` lets through.
pub struct Once(AtomicBool);

impl Once {
    pub const fn new() -> Self {
        Once(AtomicBool::new(false))
    }

    /// Whether this moment asks. A moment that may not ask spends nothing,
    /// and the spend is one exchange, so two moments arriving together
    /// cannot both ask.
    pub fn take(&self, here: bool, running: bool) -> bool {
        due(here, running, self.0.load(Ordering::SeqCst))
            && self
                .0
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
    }
}

/// This build's own answer, with the variable read where Tauri reads it for
/// the plugin (`Env::appimage`), so the build that asks is the build whose
/// file the plugin would replace.
#[cfg(windows)]
fn this_build(_app: &AppHandle) -> bool {
    asks_here(true, None)
}

#[cfg(target_os = "linux")]
fn this_build(app: &AppHandle) -> bool {
    asks_here(false, app.env().appimage.as_deref())
}

#[cfg(not(any(windows, target_os = "linux")))]
fn this_build(_app: &AppHandle) -> bool {
    false
}

/// The engine step has finished, at the engine's page or at a stop.
///
/// The two places in `bootstrap.rs` where that is known call this, and
/// nothing else does: the opening look's own stop, and the end of a pass
/// that did not go back to the gate. The ask runs on a thread of its own,
/// because it waits on the network and then on the person.
pub fn finished(app: &AppHandle) {
    let running = app.state::<bootstrap::Shell>().step_running();
    if !ASKED.take(this_build(app), running) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || ask(&app));
}

// --- the ask, the press, and the install ----------------------------------

/// Ask the release page, and on a newer version ask the person.
fn ask(app: &AppHandle) {
    // Whether the hook has run: it closes the engine's page when this
    // update holds the close, and it hides the window either way. Only
    // Windows runs the hook, inside `install`, just before it starts the
    // installer.
    let closed = Arc::new(AtomicBool::new(false));
    // Whether this update holds the claim on the engine's close, and so is
    // the one that closes the engine's page.
    let owns = Arc::new(AtomicBool::new(false));
    let hooked = app.clone();
    let marked = Arc::clone(&closed);
    let owned = Arc::clone(&owns);
    let updater = app
        .updater_builder()
        .timeout(CHECK_WAIT)
        // Assumed 1: the installer opens the app again. The plugin passes
        // it the switch that does so while this holds; 2.12.0 defaults it
        // on (src/updater.rs:197), and it is stated here so no default
        // decides it.
        .restart_after_install(true)
        // This hook replaces the plugin's own, which ran only the clean-up
        // at its end (src/lib.rs:121-123), so that clean-up is kept.
        .on_before_exit(move || {
            if owned.load(Ordering::SeqCst) {
                end_engine_page(&hooked);
            }
            marked.store(true, Ordering::SeqCst);
            hooked.cleanup_before_exit();
        })
        .build();
    let updater = match updater {
        Ok(updater) => updater,
        Err(_) => return,
    };
    // A check that fails, or finds the machine offline, or finds nothing
    // newer, says nothing (Decided 8).
    let mut update = match tauri::async_runtime::block_on(updater.check()) {
        Ok(Some(update)) => update,
        _ => return,
    };
    update.timeout = Some(DOWNLOAD_WAIT);

    let words = strings::words();
    let pressed = app
        .dialog()
        .message(ready_sentence(&update.version, &update.current_version))
        .title(words.window_title.clone())
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            words.update_now.clone(),
            words.update_later.clone(),
        ))
        .blocking_show();
    // Later, or the dialog closed: nothing happens and nothing is kept, so
    // the next launch asks again.
    if !pressed {
        return;
    }

    // The signature is checked here, over the bytes in memory, before any
    // of them is written (src/updater.rs:740-746).
    let bytes = match tauri::async_runtime::block_on(update.download(|_, _| {}, || {})) {
        Ok(bytes) => bytes,
        Err(why) => return failed(app, cause(Stage::Download, &why), false),
    };
    // The download came back, so the signature is the owner's, trusted
    // comment and all. The file it was made over must carry the version on
    // offer, or this is an older release under a newer number (the owner's
    // decision of 2026-10-03). Nothing has been written or closed yet.
    if !signed_for(&update.signature, &update.version) {
        return failed(app, Cause::Version, false);
    }

    hold(app);
    // A window close already under way is giving the engine its five
    // seconds and will end the app; an install now would end the process
    // inside that grace (PLAN-0021 phase 3 review). So the update takes the
    // claim the window close takes, and installs only when no window close
    // holds it. Losing it, the update does not install: the window close
    // ends the app, and the next launch asks again.
    let closing = claim_close(app);
    if !installs(closing) {
        app.state::<bootstrap::Shell>().let_go();
        return;
    }
    owns.store(closing == Closing::Ours, Ordering::SeqCst);
    match update.install(bytes) {
        // Linux alone comes back: on Windows a started installer ends this
        // process inside `install`.
        Ok(()) => {
            if closing == Closing::Ours {
                end_engine_page(app);
            }
            app.restart();
        }
        Err(why) => {
            let closed = closed.load(Ordering::SeqCst);
            if !closed {
                // Nothing was closed, so the claim and the flag go back and
                // the app runs on as it was.
                let shell = app.state::<bootstrap::Shell>();
                if closing == Closing::Ours {
                    shell.engine.release_closing();
                }
                shell.let_go();
            }
            failed(app, cause(Stage::Install, &why), closed);
        }
    }
}

/// Wait for a running engine step to finish, then hold the flag a pass
/// takes when it starts, so none starts until this process is gone.
fn hold(app: &AppHandle) {
    let shell = app.state::<bootstrap::Shell>();
    while !shell.hold() {
        std::thread::sleep(WAIT);
    }
}

/// Who closes the engine's page, if anyone (PLAN-0021 phase 3 review).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Closing {
    /// No engine is up, so there is no page to close and no grace to give.
    NoEngine,
    /// This update took the claim, so it closes the page itself, and a
    /// window close pressed meanwhile finds the claim taken and waits.
    Ours,
    /// A window close took it first and is giving the engine its grace.
    Taken,
}

/// Whether the update may install: not while a window close holds the
/// claim, since the install ends the process and would cut that close's
/// five seconds short.
pub fn installs(closing: Closing) -> bool {
    closing != Closing::Taken
}

/// Take the claim on the engine's close, the one exchange the window close
/// in `main.rs` takes, for a live engine only. The hold keeps a pass from
/// starting an engine after this reads none.
fn claim_close(app: &AppHandle) -> Closing {
    let shell = app.state::<bootstrap::Shell>();
    if !shell.engine.is_live() {
        return Closing::NoEngine;
    }
    if shell.engine.claim_closing() {
        Closing::Ours
    } else {
        Closing::Taken
    }
}

/// The engine's page, closed the way a window close closes it (`main.rs`,
/// CloseRequested), by the update that holds the claim: the close posted to
/// the engine and five seconds given for a write in flight to finish, then
/// the run's own job closed. On Linux that job is the one thing that ends
/// the engine's process group, and the process ends right after this with
/// no destructor run.
fn end_engine_page(app: &AppHandle) {
    let shell = app.state::<bootstrap::Shell>();
    let url = app
        .get_webview_window(bootstrap::MAIN_WINDOW)
        .and_then(|window| window.url().ok())
        .map(|url| url.to_string())
        .unwrap_or_default();
    shell.engine.quiesce(&url);
    shell.engine.end_job();
}

/// The failure, said in a dialog of its own. When the hook had already
/// closed the engine's page and hidden the window, the installer never
/// started, so this is still the version it was: the app opens again,
/// which brings its engine's page back, and nothing has changed.
fn failed(app: &AppHandle, cause: Cause, reopen: bool) {
    let words = strings::words();
    app.dialog()
        .message(failure_sentence(cause))
        .title(words.window_title.clone())
        .kind(MessageDialogKind::Warning)
        .blocking_show();
    if reopen {
        app.restart();
    }
}

// --- what the dialogs say -------------------------------------------------

/// Which half of an update a failure came from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    Download,
    Install,
}

/// Why an update was not installed, as the person is told it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cause {
    /// The new version did not arrive.
    Download,
    /// It arrived, and its signature is not the owner's.
    Signature,
    /// It arrived with its signature, and could not be put in place.
    Install,
    /// It arrived with the owner's signature, made over a file of another
    /// version than the one on offer.
    Version,
}

// --- the version a signature was made for ----------------------------------
//
// The owner's decision of 2026-10-03 (PLAN-0021 phase 3 review). The
// release manifest is not itself signed, and the signature covers only the
// file, so whoever could rewrite the published latest.json could offer an
// older signed installer under a newer version number. The plugin's own
// guard against that, `requireSignedVersion`, reads a version from the
// signature that tauri-cli 2.11.4 never writes, so it cannot be on. What
// the signer does write is the signed file's name, in the signature's
// trusted comment (`timestamp:<unix>\tfile:<name>`), and the release's names
// carry their version (`RadialRig_<version>_x64-setup.exe`,
// `RadialRig_<version>_amd64.AppImage`). So the app installs an update only
// when that name carries the version on offer. The comment is read only
// after the download returned: the plugin's check (`verify_signature`,
// src/updater.rs:1530-1554) verifies minisign's global signature, which
// covers the trusted comment, so by then the comment is the owner's.

/// The trusted comment's line, as minisign-verify reads it: the third line
/// of the signature, after this prefix.
const TRUSTED: &str = "trusted comment: ";

/// The trusted comment's field that names the signed file.
const SIGNED_FILE: &str = "file:";

/// The file name an update's signature was made over, read out of its
/// trusted comment. The signature is decoded with the base64 engine the
/// plugin decodes it with and split into lines the way minisign-verify
/// splits them, so the comment read is the one that was verified. A
/// signature that does not read, a comment with no file, an empty file, or
/// two of them, names none.
pub fn signed_file(signature: &str) -> Option<String> {
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(signature)
        .ok()?;
    let text = String::from_utf8(bytes).ok()?;
    let comment = text.lines().nth(2)?.strip_prefix(TRUSTED)?;
    let mut files = comment
        .split('\t')
        .filter_map(|field| field.strip_prefix(SIGNED_FILE));
    let file = files.next()?;
    if file.is_empty() || files.next().is_some() {
        return None;
    }
    Some(file.to_string())
}

/// Whether a file name carries a version as the release's names carry it:
/// between two underscores, so `0.1.1` is never read inside `0.1.18`.
pub fn names_version(file: &str, version: &str) -> bool {
    !version.is_empty() && file.contains(&format!("_{version}_"))
}

/// Whether an update's signature was made over a file of the version on
/// offer.
pub fn signed_for(signature: &str, version: &str) -> bool {
    signed_file(signature).is_some_and(|file| names_version(&file, version))
}

/// The cause a failure is told by. The plugin checks the signature inside
/// the download, so a signature error comes only from there: the errors
/// its check returns (`verify_signature`, `src/updater.rs:1530-1554`).
pub fn cause(stage: Stage, why: &Error) -> Cause {
    match stage {
        Stage::Install => Cause::Install,
        Stage::Download if signature(why) => Cause::Signature,
        Stage::Download => Cause::Download,
    }
}

fn signature(why: &Error) -> bool {
    matches!(
        why,
        Error::Minisign(_)
            | Error::Base64(_)
            | Error::SignatureUtf8(_)
            | Error::SignedVersionMismatch { .. }
            | Error::MissingSignedVersion
    )
}

/// The dialog's sentence: the version on offer, this one, and what
/// updating does.
pub fn ready_sentence(new: &str, current: &str) -> String {
    strings::fill_versions(&strings::words().update_ready, new, current)
}

/// The one sentence a failed update ends on, for its cause. None quotes the
/// machine's own error: a network library's message names hosts and parts
/// a reader does not know, and the cause is the why.
pub fn failure_sentence(cause: Cause) -> String {
    let words = strings::words();
    match cause {
        Cause::Download => words.update_failed_download.clone(),
        Cause::Signature => words.update_failed_signature.clone(),
        Cause::Install => words.update_failed_install.clone(),
        Cause::Version => words.update_failed_version.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bootstrap;
    use std::sync::atomic::Ordering;

    /// One file's code, its test module left out and its line endings
    /// read in one form, so a pin reads what runs and not what tests it.
    fn code_of(source: &str) -> String {
        let source = source.replace("\r\n", "\n");
        let end = source.find("#[cfg(test)]").expect("the test module");
        source[..end].to_string()
    }

    /// The text between two markers of one file's code.
    fn between<'a>(code: &'a str, from: &str, to: &str) -> &'a str {
        let start = code.find(from).unwrap_or_else(|| panic!("{from} is missing"));
        let rest = &code[start..];
        let end = rest[from.len()..]
            .find(to)
            .unwrap_or_else(|| panic!("{to} is missing after {from}"));
        &rest[..from.len() + end]
    }

    // Decided 8: Windows asks, the AppImage asks, and a .deb, which runs
    // with no APPIMAGE variable, never does. An empty variable names no
    // file the plugin could replace, so it reads as unset.
    #[test]
    fn the_check_runs_on_windows_and_in_the_appimage_and_never_from_a_deb() {
        assert!(asks_here(true, None));
        assert!(asks_here(false, Some(OsStr::new("/home/a/RadialRig.AppImage"))));
        assert!(!asks_here(false, None));
        assert!(!asks_here(false, Some(OsStr::new(""))));
    }

    // The moment: after the engine step, never while it runs, and once.
    #[test]
    fn the_check_runs_after_the_engine_step_and_never_while_it_runs() {
        assert!(due(true, false, false));
        assert!(!due(true, true, false));
        assert!(!due(true, false, true));
        assert!(!due(false, false, false));
    }

    // One launch asks once. A moment while a pass runs does not spend the
    // ask; the first finished pass does; a retry that ends in another
    // stop, or at the engine's page, finds it spent. A build that never
    // asks never spends it either.
    #[test]
    fn one_launch_asks_once_and_a_retry_that_stops_again_does_not_ask_again() {
        let once = Once::new();
        assert!(!once.take(true, true));
        assert!(once.take(true, false));
        assert!(!once.take(true, false));
        assert!(!once.take(true, true));

        let deb = Once::new();
        assert!(!deb.take(false, false));
        assert!(!deb.0.load(Ordering::SeqCst));
    }

    // The dialog names the version on offer and the one running, and says
    // what updating does, in the strings file's own words.
    #[test]
    fn the_dialog_names_the_new_version_and_this_one() {
        let said = ready_sentence("0.1.19", "0.1.18");
        assert!(
            said.starts_with("RadialRig 0.1.19 is ready. This is RadialRig 0.1.18.\n"),
            "{said}"
        );
        assert!(said.ends_with("closes RadialRig, installs the new version and opens it again."));
        assert!(!said.contains('{'), "{said}");
    }

    // Why an update was not installed: a download that did not arrive, a
    // signature that is not the owner's, or an install that could not be
    // put in place. A signature error can only come from the download,
    // which is where the plugin checks it.
    #[test]
    fn a_failed_update_is_said_by_its_cause() {
        let network = Error::Network("Download request failed with status: 404".into());
        assert_eq!(cause(Stage::Download, &network), Cause::Download);
        assert_eq!(cause(Stage::Download, &Error::MissingSignedVersion), Cause::Signature);
        let mismatch = Error::SignedVersionMismatch {
            signed: "0.1.17".into(),
            announced: "0.1.19".into(),
        };
        assert_eq!(cause(Stage::Download, &mismatch), Cause::Signature);
        assert_eq!(cause(Stage::Download, &Error::SignatureUtf8("x".into())), Cause::Signature);
        assert_eq!(cause(Stage::Install, &Error::InvalidUpdaterFormat), Cause::Install);
        assert_eq!(cause(Stage::Install, &network), Cause::Install);

        let said = [Cause::Download, Cause::Signature, Cause::Install].map(failure_sentence);
        for sentence in &said {
            assert!(
                sentence.starts_with("The update was not installed, and nothing changed.\n"),
                "{sentence}"
            );
        }
        assert!(said[0] != said[1] && said[1] != said[2] && said[0] != said[2]);
        assert!(said[1].contains("signature"));
    }

    // The update holds the flag a pass takes when it starts: it cannot
    // take it while a pass runs, and while it holds it no pass can start.
    // `launch` takes the same flag with one exchange, which is what makes
    // a press of Run This Step Again wait rather than run under a closing
    // app.
    #[test]
    fn the_update_waits_for_a_running_pass_and_no_pass_starts_while_it_holds() {
        let shell = bootstrap::Shell::new();
        assert!(!shell.step_running());
        assert!(shell.hold(), "a free flag is taken");
        assert!(shell.step_running());
        assert!(!shell.hold(), "a held flag is not taken twice");
        shell.let_go();
        assert!(!shell.step_running());

        let code = code_of(include_str!("bootstrap.rs"));
        let launch = between(&code, "fn launch(", "pub enum Outcome");
        assert!(launch.contains("if shell.busy.swap(true, Ordering::SeqCst) {\n            return;"));
        let hold = between(&code, "pub fn hold(&self)", "\n    }");
        assert!(hold.contains("self.busy"), "the hold takes the pass's own flag");
    }

    // The two places the engine step ends report it, and nothing else
    // does: the opening look's own stop, and the end of a pass once its
    // flag is down. A pass that went back to the gate has not run the
    // engine step, so it reports nothing.
    #[test]
    fn the_engine_step_reports_its_end_at_the_two_places_it_ends() {
        let code = code_of(include_str!("bootstrap.rs"));
        assert_eq!(code.matches("crate::update::finished(").count(), 2);

        let open = between(&code, "pub fn open(", "fn launch(");
        let stop = between(open, "Opening::Stop(stop) =>", "Opening::Swap =>");
        assert!(stop.contains("crate::update::finished(app);"));

        let launch = between(&code, "fn launch(", "pub enum Outcome");
        let gate = launch
            .find("let finished = !matches!(outcome, Ok(Outcome::Regated(_)));")
            .expect("a pass back at the gate is told apart");
        let down = launch.find("shell.busy.store(false, Ordering::SeqCst);").expect("the flag");
        let told = launch.find("if finished {\n            crate::update::finished(&app);").expect("the report");
        assert!(gate < down && down < told);

        let work = between(&code, "fn work(", "pub fn bootstrap_proceed");
        assert!(!work.contains("update::"), "no step inside the pass reports");
    }

    // The installer is told to open the app again, and the hook that runs
    // before it closes the engine's page, when the update holds the close,
    // and keeps the plugin's own clean-up, which a hook of this module's
    // replaces (lib.rs:121-123 in 2.12.0). Closing the page is the window
    // close's own order: post the close and wait, then end the run's job.
    // main.rs registers this module and the plugin.
    #[test]
    fn the_installer_opens_the_app_again_and_the_page_closes_before_it_starts() {
        let code = code_of(include_str!("update.rs"));
        assert!(code.contains(".restart_after_install(true)"));
        let hook = between(&code, ".on_before_exit(", ".build()");
        let ours = hook.find("if owned.load(Ordering::SeqCst) {").expect("the claim decides");
        let closes = hook.find("end_engine_page(").expect("the page closes");
        let cleans = hook.find("cleanup_before_exit()").expect("the clean-up stays");
        assert!(ours < closes && closes < cleans);

        let close = between(&code, "fn end_engine_page(", "\n}");
        let quiet = close.find(".quiesce(").expect("the close is posted");
        let ended = close.find(".end_job()").expect("the run's job is closed");
        assert!(quiet < ended);

        let main = code_of(include_str!("main.rs"));
        assert!(main.contains("mod update;"));
        assert!(main.contains(".plugin(tauri_plugin_updater::Builder::new().build())"));
    }

    // A window close already under way (PLAN-0021 phase 3 review). The
    // close in main.rs claims the engine's close and gives the engine up to
    // five seconds before it ends the app. An update that went ahead then
    // would end the process inside that grace, so it does not install: the
    // window close ends the app, and the update waits for the next launch.
    // An update that wins the claim gives the engine the same grace itself,
    // and a window close pressed meanwhile finds the claim taken and waits
    // for the update to end the app. A claim the update took and did not
    // spend goes back, so a later window close still works.
    #[test]
    fn an_update_never_installs_while_a_window_close_gives_the_engine_its_grace() {
        assert!(installs(Closing::NoEngine));
        assert!(installs(Closing::Ours));
        assert!(!installs(Closing::Taken));

        let code = code_of(include_str!("update.rs"));
        let claim = between(&code, "fn claim_close(", "\n}");
        let live = claim.find(".is_live()").expect("only a live engine is claimed");
        let claimed = claim.find(".claim_closing()").expect("the close's own claim");
        assert!(live < claimed);

        let ask = between(&code, "fn ask(", "\n}");
        let held = ask.find("hold(app);").expect("the hold");
        let asked = ask.find("let closing = claim_close(app);").expect("the claim");
        let refused = ask.find("if !installs(closing) {").expect("the refusal");
        let installed = ask.find("update.install(").expect("the install");
        assert!(held < asked && asked < refused && refused < installed);
        let refusal = between(ask, "if !installs(closing) {", "\n    }");
        assert!(refusal.contains(".let_go();") && refusal.contains("return;"));

        let failure = between(ask, "Err(why) => {", "\n        }");
        let given = failure.find(".release_closing();").expect("an unspent claim goes back");
        let told = failure.find("failed(").expect("the failure is said");
        assert!(given < told);
    }

    // The hold stands before the install, once (PLAN-0021 phase 3 review).
    // Without it, a pass that Run This Step Again started during the dialog
    // or the download could still be running when the install ends the
    // app, which would cut an engine replacement short. The hold's own test
    // proves it works; this one proves the ask takes it in time.
    #[test]
    fn the_install_waits_for_the_hold() {
        let code = code_of(include_str!("update.rs"));
        let ask = between(&code, "fn ask(", "\n}");
        assert_eq!(ask.matches("hold(app);").count(), 1, "one hold in the ask");
        let held = ask.find("hold(app);").expect("the hold");
        let installed = ask.find("update.install(").expect("the install");
        assert!(held < installed, "the hold comes before the install");
    }

    // --- the version the signature was made for (owner, 2026-10-03) -----

    /// A signature as the signer writes it: four lines, base64 as a whole,
    /// with this trusted comment as the third. The two signature lines are
    /// the shape only; the plugin has already checked the real ones by the
    /// time the app reads the comment.
    fn signature_with(comment: &str) -> String {
        use base64::Engine;
        let text = format!(
            "untrusted comment: signature from tauri secret key\nRUS2\n\
             trusted comment: {comment}\ni61M\n"
        );
        base64::engine::general_purpose::STANDARD.encode(text)
    }

    // The signer names the file it signed in the trusted comment. The
    // committed fixture is a real signer's output over a file named
    // `signed.txt`, so the reading is held to the real shape.
    #[test]
    fn the_signed_file_is_read_from_the_trusted_comment() {
        let real = include_str!("../../../src/radialrig/tests/fixtures/updater/signed.txt.sig");
        assert_eq!(signed_file(real.trim()).as_deref(), Some("signed.txt"));

        let said = signature_with("timestamp:1791057895\tfile:RadialRig_0.1.18_x64-setup.exe");
        assert_eq!(signed_file(&said).as_deref(), Some("RadialRig_0.1.18_x64-setup.exe"));
    }

    // An update installs only when the file its signature names carries the
    // version on offer, as the release names carry it: `_<version>_`. An
    // older signed installer offered under a newer number is refused.
    #[test]
    fn an_update_installs_only_when_its_signature_names_the_version_on_offer() {
        let names = [
            "RadialRig_0.1.18_x64-setup.exe",
            "RadialRig_0.1.18_amd64.AppImage",
            "RadialRig Proof_0.1.18_x64-setup.exe",
        ];
        for name in names {
            assert!(names_version(name, "0.1.18"), "{name}");
            let said = signature_with(&format!("timestamp:1\tfile:{name}"));
            assert!(signed_for(&said, "0.1.18"), "{name}");
        }

        let older = signature_with("timestamp:1\tfile:RadialRig_0.1.17_x64-setup.exe");
        assert!(!signed_for(&older, "0.1.18"), "an older file under a newer number");
        assert!(signed_for(&older, "0.1.17"));
    }

    // The boundary: one version is never read inside another.
    #[test]
    fn a_version_is_never_read_inside_a_longer_one() {
        assert!(!names_version("RadialRig_0.1.18_x64-setup.exe", "0.1.1"));
        assert!(!names_version("RadialRig_0.1.1_x64-setup.exe", "0.1.18"));
        assert!(!names_version("RadialRig_10.1.18_x64-setup.exe", "0.1.18"));
        assert!(!names_version("RadialRig_0.1.18x_x64-setup.exe", "0.1.18"));
        assert!(!names_version("RadialRig-0.1.18-x64-setup.exe", "0.1.18"));
        assert!(!names_version("RadialRig__x64-setup.exe", ""));
    }

    // A comment with no file, an empty one, two of them, or a signature
    // that does not read at all names no version, so nothing installs.
    #[test]
    fn a_missing_or_garbled_file_name_installs_nothing() {
        for comment in [
            "timestamp:1",
            "timestamp:1\tfile:",
            "timestamp:1\tfile:RadialRig_0.1.18_x64-setup.exe\tfile:RadialRig_0.1.18_amd64.AppImage",
            "timestamp:1 file:RadialRig_0.1.18_x64-setup.exe",
        ] {
            let said = signature_with(comment);
            assert_eq!(signed_file(&said), None, "{comment}");
            assert!(!signed_for(&said, "0.1.18"), "{comment}");
        }

        use base64::Engine;
        let short = base64::engine::general_purpose::STANDARD
            .encode("untrusted comment: x\nRUS2\n");
        let unmarked = base64::engine::general_purpose::STANDARD
            .encode("untrusted comment: x\nRUS2\nfile:RadialRig_0.1.18_x64-setup.exe\ni61M\n");
        for said in ["", "not base64 at all!", short.as_str(), unmarked.as_str()] {
            assert_eq!(signed_file(said), None, "{said}");
            assert!(!signed_for(said, "0.1.18"), "{said}");
        }
    }

    // The check stands after the download, which is where the plugin
    // proves the comment authentic, and before the hold and the install. A
    // refusal says why and changes nothing.
    #[test]
    fn the_version_is_checked_after_the_download_and_before_anything_closes() {
        let code = code_of(include_str!("update.rs"));
        let ask = between(&code, "fn ask(", "\n}");
        let downloaded = ask.find("update.download(").expect("the download");
        let checked = ask
            .find("if !signed_for(&update.signature, &update.version) {")
            .expect("the version check");
        let held = ask.find("hold(app);").expect("the hold");
        assert!(downloaded < checked && checked < held);
        let refusal = between(ask, "if !signed_for(", "\n    }");
        assert!(refusal.contains("return failed(app, Cause::Version, false);"));

        let said = failure_sentence(Cause::Version);
        assert!(said.starts_with("The update was not installed, and nothing changed.\n"));
        assert!(said.contains("signed for"), "{said}");
    }
}
