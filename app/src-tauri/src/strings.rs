//! D13 `strings.rs`: the shell's sentences, read from `app/src/strings.json`.
//!
//! Every sentence this window says before the engine serves a page comes
//! from that one file. Nothing in the Rust composes prose beyond dropping
//! a value into a string's single slot.
//!
//! Two readers share the file. The bootstrap page fetches it and fills
//! its own slots, so most sentences never pass through Rust at all: what
//! the shell sends the page is a *key*, and the page says the sentence.
//! Rust reads the file for the places that have no page to render them —
//! the window's own name, the picker's menu label, the notice the picker
//! shows in the shell's own chrome once the window has left the bootstrap
//! screen, the removal flow's lines, and the update's dialogs.
//!
//! What Rust does own is the promise that every key it can name exists.
//! `EMITTED` lists them, the loader proves each one is in the file, and a
//! key that went missing is a startup stop rather than a blank line on
//! screen much later.

use serde::Deserialize;
use std::sync::OnceLock;

/// The file itself, compiled in. One copy ships; nothing reads a second.
const SOURCE: &str = include_str!("../../src/strings.json");

/// The stop when the compiled-in file cannot be read as the shell needs
/// it. It cannot fire in a shipped build: the pytest hold pins the key
/// set of the same file this constant is compiled from.
const UNREADABLE: &str = "the shell text did not parse";

// --- the keys the shell can name --------------------------------------

pub const PROGRESS_PROBE: &str = "progress_probe";
pub const PROGRESS_DOWNLOAD: &str = "progress_download";
pub const PROGRESS_VERIFY: &str = "progress_verify";
pub const PROGRESS_EXTRACT: &str = "progress_extract";
pub const PROGRESS_PATH: &str = "progress_path";
pub const PROGRESS_INSTALL: &str = "progress_install";
pub const PROGRESS_RESOLVE: &str = "progress_resolve";
pub const PROGRESS_VERSION: &str = "progress_version";
pub const PROGRESS_START: &str = "progress_start";

pub const FAIL_UV_PROBE: &str = "fail_uv_probe";
pub const FAIL_DOWNLOAD: &str = "fail_download";
pub const FAIL_CHECKSUM: &str = "fail_checksum";
pub const FAIL_EXTRACT: &str = "fail_extract";
pub const FAIL_PATH_WRITE: &str = "fail_path_write";
pub const FAIL_PACKAGE_DOWNLOAD: &str = "fail_package_download";
pub const FAIL_PACKAGE_CHECKSUM: &str = "fail_package_checksum";
pub const FAIL_ENGINE_INSTALL: &str = "fail_engine_install";
pub const FAIL_RECOVERY: &str = "fail_recovery";
pub const FAIL_TOOL_DIR: &str = "fail_tool_dir";
pub const FAIL_FOREIGN_EXE: &str = "fail_foreign_exe";
pub const FAIL_ENGINE_OLD: &str = "fail_engine_old";
/// The same stop for an engine that answered no version at all, so the
/// sentence never names a blank where the installed version would go.
pub const FAIL_ENGINE_UNREAD: &str = "fail_engine_unread";
pub const FAIL_SPAWN: &str = "fail_spawn";
pub const FAIL_LINE_TIMEOUT: &str = "fail_line_timeout";
pub const FAIL_LINE_UNPARSED: &str = "fail_line_unparsed";
pub const FAIL_ENGINE_STOPPED: &str = "fail_engine_stopped";
pub const FAIL_PICKER_NO_TARGET: &str = "fail_picker_no_target";
pub const FAIL_DEBUG_OVERRIDE: &str = "fail_debug_override";

/// The PATH report's two sentences, for the platform where that step
/// reads and writes nothing (PLAN-0009 phase 3 prescription 3). The
/// slot carries the directory that was looked for.
pub const PATH_REPORT_ON: &str = "path_report_on";
pub const PATH_REPORT_OFF: &str = "path_report_off";

/// The engine swap's own sentences (PLAN-0021 phase 2, prescription 4):
/// the progress line while it replaces the engine, which is not one of
/// the nine steps and so draws no count, and the one sentence a swap that
/// gave up adds under its stop. The first of those two names, in its
/// slot, the programs that still hold the engine; the second says none
/// was found running from the engine's folders.
pub const SWAP_RUNNING: &str = "swap_running";
pub const SWAP_HELD: &str = "swap_held";
pub const SWAP_HELD_NONE: &str = "swap_held_none";

/// The update's own sentences (PLAN-0021 phase 3, prescription 3), said in
/// native dialogs with no page behind them. The first names the version on
/// offer in `{detail}` and this build's own in `{current}`; the next two
/// label the dialog's presses; the last four are the one sentence a failed
/// update ends on, one per cause. The version one is the review's: a
/// signature made for another version than the one offered (the owner's
/// decision of 2026-10-03).
pub const UPDATE_READY: &str = "update_ready";
pub const UPDATE_NOW: &str = "update_now";
pub const UPDATE_LATER: &str = "update_later";
pub const UPDATE_FAILED_DOWNLOAD: &str = "update_failed_download";
pub const UPDATE_FAILED_SIGNATURE: &str = "update_failed_signature";
pub const UPDATE_FAILED_VERSION: &str = "update_failed_version";
pub const UPDATE_FAILED_INSTALL: &str = "update_failed_install";

/// The removal flow's own sentences. These six outcome lines and the one
/// notice under them are the only sentences Rust says with no page to
/// render them: the uninstaller runs them after the window is gone. The
/// two error lines are two different failures: the read of the Path
/// value, and the one write that takes the entry out.
pub const UNINSTALL_NOTICE: &str = "uninstall_notice";
pub const UNINSTALL_PATH_REMOVED: &str = "uninstall_path_removed";
pub const UNINSTALL_PATH_KEPT: &str = "uninstall_path_kept";
pub const UNINSTALL_PATH_GONE: &str = "uninstall_path_gone";
pub const UNINSTALL_PATH_ABSENT: &str = "uninstall_path_absent";
pub const UNINSTALL_PATH_ERROR: &str = "uninstall_path_error";
pub const UNINSTALL_PATH_WRITE_ERROR: &str = "uninstall_path_write_error";

/// The steps consent is given to, by name.
///
/// These are identifiers, not keys into the strings file. The gate says
/// in plain words what the first run sets up and where it lands; it does
/// not enumerate the steps, so there is no block of prose behind each of
/// these names any more. What they still do is carry R6: the set the gate
/// drew is the set the run is allowed to execute.
pub const GATE_UV: &str = "gate_uv";
pub const GATE_PYTHON: &str = "gate_python";
pub const GATE_ENGINE: &str = "gate_engine";
pub const GATE_PATH: &str = "gate_path";

/// Every key the shell can put in front of a reader by naming it.
pub const EMITTED: &[&str] = &[
    PROGRESS_PROBE,
    PROGRESS_DOWNLOAD,
    PROGRESS_VERIFY,
    PROGRESS_EXTRACT,
    PROGRESS_PATH,
    PROGRESS_INSTALL,
    PROGRESS_RESOLVE,
    PROGRESS_VERSION,
    PROGRESS_START,
    FAIL_UV_PROBE,
    FAIL_DOWNLOAD,
    FAIL_CHECKSUM,
    FAIL_EXTRACT,
    FAIL_PATH_WRITE,
    FAIL_PACKAGE_DOWNLOAD,
    FAIL_PACKAGE_CHECKSUM,
    FAIL_ENGINE_INSTALL,
    FAIL_RECOVERY,
    FAIL_TOOL_DIR,
    FAIL_FOREIGN_EXE,
    FAIL_ENGINE_OLD,
    FAIL_ENGINE_UNREAD,
    FAIL_SPAWN,
    FAIL_LINE_TIMEOUT,
    FAIL_LINE_UNPARSED,
    FAIL_ENGINE_STOPPED,
    FAIL_PICKER_NO_TARGET,
    FAIL_DEBUG_OVERRIDE,
    PATH_REPORT_ON,
    PATH_REPORT_OFF,
    SWAP_RUNNING,
    SWAP_HELD,
    SWAP_HELD_NONE,
    UPDATE_READY,
    UPDATE_NOW,
    UPDATE_LATER,
    UPDATE_FAILED_DOWNLOAD,
    UPDATE_FAILED_SIGNATURE,
    UPDATE_FAILED_VERSION,
    UPDATE_FAILED_INSTALL,
    UNINSTALL_NOTICE,
    UNINSTALL_PATH_REMOVED,
    UNINSTALL_PATH_KEPT,
    UNINSTALL_PATH_GONE,
    UNINSTALL_PATH_ABSENT,
    UNINSTALL_PATH_ERROR,
    UNINSTALL_PATH_WRITE_ERROR,
];

/// The stops a second press cannot move. Every other failure pairs with
/// the one control; these say what does move them instead. Two of them
/// are a digest that will not change under a second ask, the uv
/// archive's and the engine package's. One is a file this app did not
/// write.
pub const DETERMINISTIC: &[&str] =
    &[FAIL_CHECKSUM, FAIL_PACKAGE_CHECKSUM, FAIL_FOREIGN_EXE];

/// Whether a failure key pairs with the retry control.
pub fn retryable(key: &str) -> bool {
    !DETERMINISTIC.contains(&key)
}

// --- the typed part ----------------------------------------------------

/// The sentences Rust itself renders, with no page to render them.
///
/// Unknown keys are ignored by design: the page owns the rest of the
/// file, and a struct restating all of it would be a second key set to
/// keep level with the first.
#[derive(Deserialize)]
pub struct Words {
    /// The window's own name, for the chrome the picker raises. The
    /// window's title is set from the config copies before it is drawn.
    pub window_title: String,
    /// D11's one menu item.
    pub picker_menu_label: String,
    /// D11's stop, shown in the shell's own chrome because the window is
    /// on the engine's page by then and this screen is not.
    pub fail_picker_no_target: String,
    /// The removal flow's seven. There is no page at all by the time the
    /// uninstaller runs them, so Rust reads them here rather than
    /// sending a key to something that would render it.
    pub uninstall_notice: String,
    pub uninstall_path_removed: String,
    pub uninstall_path_kept: String,
    pub uninstall_path_gone: String,
    pub uninstall_path_absent: String,
    pub uninstall_path_error: String,
    pub uninstall_path_write_error: String,
    /// The update's seven. The dialogs open over whatever the window shows,
    /// the engine's page included, so Rust says them itself.
    pub update_ready: String,
    pub update_now: String,
    pub update_later: String,
    pub update_failed_download: String,
    pub update_failed_signature: String,
    pub update_failed_version: String,
    pub update_failed_install: String,
}

static WORDS: OnceLock<Words> = OnceLock::new();

/// The file, parsed once, with every key the shell can name proved to be
/// in it. A failure here is a hard stop before the window is drawn.
pub fn words() -> &'static Words {
    WORDS.get_or_init(parse)
}

/// The two platform keys a per-platform sentence carries, exactly.
/// Where one sentence cannot be true on both operating systems, the
/// file holds one per platform and the page picks by the view's own
/// platform field. Every emitted key is either one whole sentence or
/// one such pair — never a pair with a platform missing, because that
/// would be a sentence one build renders as nothing.
const PLATFORM_KEYS: [&str; 2] = ["windows", "linux"];

/// Whether one emitted value is sayable: a non-empty sentence, or a
/// per-platform pair of them.
fn sayable(value: &serde_json::Value) -> bool {
    if let Some(text) = value.as_str() {
        return !text.trim().is_empty();
    }
    if let Some(pair) = value.as_object() {
        return pair.len() == PLATFORM_KEYS.len()
            && PLATFORM_KEYS.iter().all(|key| {
                pair.get(*key)
                    .and_then(|inner| inner.as_str())
                    .is_some_and(|text| !text.trim().is_empty())
            });
    }
    false
}

fn parse() -> Words {
    let raw: serde_json::Map<String, serde_json::Value> =
        serde_json::from_str(SOURCE).expect(UNREADABLE);
    for key in EMITTED {
        assert!(raw.get(*key).is_some_and(sayable), "{}", key);
    }
    serde_json::from_value(serde_json::Value::Object(raw)).expect(UNREADABLE)
}

/// One sentence with its single slot filled, or with the slot taken out
/// when there is nothing to put in it.
pub fn fill(said: &str, detail: &str) -> String {
    said.replace("{detail}", detail).trim().to_string()
}

/// The update dialog's sentence with both its versions in place: the one
/// on offer in `{detail}`, and this build's own in `{current}`, which
/// belongs to the build as the version an old-engine stop needs does
/// (PLAN-0021 phase 3, prescription 3).
pub fn fill_versions(said: &str, detail: &str, current: &str) -> String {
    fill(&said.replace("{current}", current), detail)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_compiled_file_carries_every_key_the_shell_can_name() {
        let words = words();
        assert!(!words.window_title.trim().is_empty());
        assert!(!words.picker_menu_label.trim().is_empty());
        assert!(words.fail_picker_no_target.contains("{detail}"));
    }

    #[test]
    fn the_deterministic_stops_take_no_retry() {
        assert!(!retryable(FAIL_CHECKSUM));
        assert!(!retryable(FAIL_PACKAGE_CHECKSUM));
        assert!(!retryable(FAIL_FOREIGN_EXE));
        assert!(retryable(FAIL_DOWNLOAD));
        assert!(retryable(FAIL_PACKAGE_DOWNLOAD));
        assert!(retryable(FAIL_ENGINE_STOPPED));
    }

    #[test]
    fn a_slot_takes_the_value_and_an_empty_one_leaves_no_gap() {
        assert_eq!(fill("a {detail} b", "x"), "a x b");
        assert_eq!(fill("a {detail}", ""), "a");
        assert_eq!(fill("no slot", "x"), "no slot");
    }

    #[test]
    fn the_two_versions_land_in_their_own_slots() {
        assert_eq!(fill_versions("{detail} over {current}", "2", "1"), "2 over 1");
        assert_eq!(fill_versions("only {detail}", "2", "1"), "only 2");
    }
}
