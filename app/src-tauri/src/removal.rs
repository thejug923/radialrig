//! U5 `removal.rs`: taking back the one Path entry this app ever added.
//!
//! The uninstaller runs this before it deletes anything, by starting the
//! app's own binary with one hidden flag. There is no window and no
//! engine on that run: the process reads its own record, reads the user
//! Path value, decides, writes at most one registry value, says one
//! sentence and ends.
//!
//! Two promises shape the whole module. The entry that comes out is the
//! entry this app recorded putting in, never one inferred from a
//! directory that looks familiar — a record that is not there means
//! nothing is removed. And the value that goes back is the current value
//! with one span cut out of it, byte for byte: no normalising, no
//! re-joining, no trimming, and no dropping of an empty segment. If the
//! two remainders — the one cut from the value on this machine and the
//! one cut from the value this app wrote — differ by a single byte, then
//! something else edited the Path after this app wrote it, and cutting a
//! span out of a value we no longer recognise is exactly what constraint
//! 5 forbids. That case refuses and says so.

use std::path::{Path, PathBuf};

use crate::bootstrap::{self, PathRecord};
use crate::strings;

/// The flag the installer hook passes. It is written once, here, and the
/// hook file is the only other place it appears.
pub const FLAG: &str = "--uninstall-path-entry";

/// The second flag, appended by the hook when the uninstaller is silent
/// or passive. It suppresses the message box and changes nothing else.
pub const QUIET: &str = "--quiet";

/// Removed, or nothing to remove.
const DONE: i32 = 0;

/// A refusal, or a registry fault. NSIS ignores it by design: a refusal
/// must never stop the app's own uninstall, and the reader learns the
/// outcome from the box rather than from a code.
const STOPPED: i32 = 1;

/// What one read of the record and the Path value asks the caller to do.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Removal {
    /// No record file, or a file that is not a record. Nothing is read
    /// from the Path and nothing is written. This is the case that must
    /// never guess a segment.
    NoRecord,
    /// The recorded segment is not on the value now, so there is nothing
    /// to take out.
    Gone,
    /// Write this value back under this type, empty remainder included.
    Take { kind: u32, value: String },
    /// The value no longer matches what this app wrote. It stays as it is.
    Refuse,
}

/// The whole decision, with no I/O in it.
///
/// The order of the tests is the order the outcomes are ruled in: a type
/// that changed is a value someone else re-created, which is refused
/// before the segment is even looked for.
pub fn decide(record: Option<&PathRecord>, kind: u32, value: &str) -> Removal {
    let record = match record {
        Some(record) => record,
        None => return Removal::NoRecord,
    };
    if record.kind != kind {
        return Removal::Refuse;
    }
    let here = match cut(value, &record.segment) {
        Some(here) => here,
        None => return Removal::Gone,
    };
    // The value this app wrote carried the segment too, so a cut that
    // finds nothing there is a record that never matched its own value.
    let written = match cut(&record.value, &record.segment) {
        Some(written) => written,
        None => return Removal::Refuse,
    };
    if here.as_bytes() != written.as_bytes() {
        return Removal::Refuse;
    }
    Removal::Take { kind, value: here }
}

/// One value with the recorded segment's span cut out of it, or nothing
/// when no span of it names that directory.
///
/// The span is the piece between separators, exactly as it stands, plus
/// one adjoining separator — the one after it when there is one, and the
/// one before it when there is not. What is left is the two sides of the
/// original string put back together untouched, which is why an empty
/// segment stays empty and a padded one stays padded.
fn cut(value: &str, dir: &str) -> Option<String> {
    let (from, to) = locate(value, dir)?;
    let (from, to) = if to < value.len() {
        (from, to + 1)
    } else if from > 0 {
        (from - 1, to)
    } else {
        (from, to)
    };
    let mut left = String::with_capacity(value.len());
    left.push_str(&value[..from]);
    left.push_str(&value[to..]);
    Some(left)
}

/// The byte span of the first segment of a value that names the same
/// directory as `dir`.
///
/// The oracle is `segment_present`, the writer's own, applied to one
/// piece at a time. Reading it that way rather than writing a second fold
/// is the point: `C:\a`, `c:\a\` and a quoted form are one directory at
/// both ends of the flow, and there is only one place that is decided.
/// The separator is ASCII, so the byte arithmetic below lands on
/// character boundaries whatever the value holds.
fn locate(value: &str, dir: &str) -> Option<(usize, usize)> {
    let mut at = 0usize;
    for piece in value.split(';') {
        let from = at;
        let to = at + piece.len();
        at = to + 1;
        if bootstrap::segment_present(piece, dir) {
            return Some((from, to));
        }
    }
    None
}

/// U5's table: one outcome, one key.
fn key_of(outcome: &Removal) -> &'static str {
    match outcome {
        Removal::NoRecord => strings::UNINSTALL_PATH_ABSENT,
        Removal::Gone => strings::UNINSTALL_PATH_GONE,
        Removal::Take { .. } => strings::UNINSTALL_PATH_REMOVED,
        Removal::Refuse => strings::UNINSTALL_PATH_KEPT,
    }
}

/// One outcome key, resolved to the sentence the strings file carries.
///
/// All six keys are named, the two error ones included, so the table is
/// read here rather than inferred from which arm was left out. The
/// fallback stays for a key no table names, and says the same thing a
/// failed read says, because a sentence this function cannot find is a
/// read that did not work.
fn said<'a>(words: &'a strings::Words, key: &str) -> &'a str {
    match key {
        strings::UNINSTALL_PATH_ABSENT => &words.uninstall_path_absent,
        strings::UNINSTALL_PATH_GONE => &words.uninstall_path_gone,
        strings::UNINSTALL_PATH_REMOVED => &words.uninstall_path_removed,
        strings::UNINSTALL_PATH_KEPT => &words.uninstall_path_kept,
        strings::UNINSTALL_PATH_ERROR => &words.uninstall_path_error,
        strings::UNINSTALL_PATH_WRITE_ERROR => &words.uninstall_path_write_error,
        _ => &words.uninstall_path_error,
    }
}

/// Where the record is, resolved without an app handle.
///
/// The identifier is the one compiled into this build, handed over by
/// `main` out of the same context the window runs on, so it is the
/// directory name `app_data_dir()` resolves under `%APPDATA%`. A build
/// made under another identifier with `--config` reads its own record
/// and never this app's (PLAN-0021 phase 2, prescription 1).
fn record_at(identifier: &str) -> Option<PathBuf> {
    std::env::var("APPDATA")
        .ok()
        .map(|data| record_under(Path::new(&data), identifier))
}

/// The record's path under one data directory, for one identifier.
fn record_under(data: &Path, identifier: &str) -> PathBuf {
    data.join(identifier).join(bootstrap::PATH_RECORD)
}

/// The engine's own command as an absolute path, quoted, for the notice's
/// one slot. The recorded segment is the directory when there is a
/// record; the documented location is it when there is not. Absolute
/// because this same run may have just taken the Path entry out, so a
/// bare name would not resolve in a fresh terminal.
fn engine_command(record: Option<&PathRecord>) -> String {
    let dir = match record {
        Some(record) => Some(PathBuf::from(bootstrap::expanded(&record.segment))),
        None => bootstrap::documented_dir(),
    };
    match dir {
        Some(dir) => format!("\"{}\"", dir.join(bootstrap::ENGINE_EXE).display()),
        None => String::new(),
    }
}

/// The flag run, start to finish, for the build whose identifier is
/// given. The exit code comes back for `main` to end on; NSIS ignores it.
pub fn run(identifier: &str, quiet: bool) -> i32 {
    let at = record_at(identifier);
    let record = at
        .as_ref()
        .and_then(|at| std::fs::read_to_string(at).ok())
        .and_then(|text| bootstrap::parse_record(&text));

    // The record is resolved first, and a run without one ends here. U5:
    // with no record, nothing is read from the Path. Reading the value
    // anyway would put this flow one edit away from acting on a segment
    // it never wrote, and there is nothing it could do with what it read.
    let record = match record {
        Some(record) => record,
        None => {
            if !quiet {
                show(key_of(&Removal::NoRecord), "", &engine_command(None));
            }
            return DONE;
        }
    };

    // The read's own failure is the one sentence that says the read did
    // not work; a write that refuses later says its own (app-shell-02).
    let (key, detail, code) = match bootstrap::read_user_path() {
        Err(_) => (strings::UNINSTALL_PATH_ERROR, String::new(), STOPPED),
        Ok((kind, value)) => {
            let outcome = decide(Some(&record), kind, &value);
            match &outcome {
                Removal::Take { kind, value } => {
                    let wrote = take(*kind, value);
                    if wrote.is_ok() {
                        // The record goes last, so a run that stopped
                        // before the write leaves the record standing and
                        // the next run decides again from it.
                        if let Some(at) = &at {
                            let _ = std::fs::remove_file(at);
                        }
                    }
                    after_take(&record, wrote)
                }
                Removal::Refuse => (key_of(&outcome), record.segment.clone(), STOPPED),
                _ => (key_of(&outcome), String::new(), DONE),
            }
        }
    };

    if !quiet {
        show(key, &detail, &engine_command(Some(&record)));
    }
    code
}

/// What the one write's result says: the outcome key, the sentence's
/// slot, and the exit code.
///
/// A write that refused is not a read that failed: the value was read,
/// the decision was to take the entry out, and the entry is still there.
/// The uninstaller deletes the record moments after this run ends, so
/// this sentence is the last thing on the machine that names the
/// segment, and it names it the way the refusal's sentence does.
fn after_take(record: &PathRecord, wrote: Result<(), ()>) -> (&'static str, String, i32) {
    match wrote {
        Ok(()) => (strings::UNINSTALL_PATH_REMOVED, String::new(), DONE),
        Err(()) => (strings::UNINSTALL_PATH_WRITE_ERROR, record.segment.clone(), STOPPED),
    }
}

/// The one write this flow ever makes, and the only one.
///
/// An empty remainder is written back as an empty value under the type
/// the read returned. Deleting the value entity is refused: the reader
/// this flow uses cannot tell a missing value from an existing empty one,
/// so a record of a segment is never proof that this app created the
/// value it sat in, and removing an entity on that guess is the shape
/// constraint 5 forbids.
fn take(kind: u32, value: &str) -> Result<(), ()> {
    bootstrap::write_user_path(kind, value).map_err(|_| ())?;
    bootstrap::announce();
    Ok(())
}

/// The one box this flow shows: the outcome line, a blank line, then the
/// notice that says what this removal did not take. Two whole sentences
/// out of the strings file, joined; nothing is composed here.
#[cfg(windows)]
fn show(key: &str, detail: &str, engine: &str) {
    use windows::core::PCWSTR;
    use windows::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, MB_ICONINFORMATION, MB_OK, MB_SETFOREGROUND, MB_TOPMOST,
    };

    let words = strings::words();
    let body = format!(
        "{}\n\n{}",
        strings::fill(said(words, key), detail),
        strings::fill(&words.uninstall_notice, engine)
    );
    let body = wide(&body);
    let title = wide(&words.window_title);
    unsafe {
        MessageBoxW(
            None,
            PCWSTR(body.as_ptr()),
            PCWSTR(title.as_ptr()),
            MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST,
        );
    }
}

#[cfg(windows)]
fn wide(said: &str) -> Vec<u16> {
    let mut units: Vec<u16> = said.encode_utf16().collect();
    units.push(0);
    units
}

#[cfg(not(windows))]
fn show(_key: &str, _detail: &str, _engine: &str) {}

#[cfg(test)]
mod tests {
    use super::*;

    const EXPANDABLE: u32 = 2;
    const PLAIN: u32 = 1;
    const OURS: &str = "C:\\Users\\reader\\.local\\bin";

    fn record(segment: &str, value: &str, kind: u32) -> PathRecord {
        PathRecord { kind, segment: segment.to_string(), value: value.to_string() }
    }

    #[test]
    fn the_record_is_read_under_the_identifier_it_is_given() {
        // PLAN-0021 phase 2, prescription 1. A proof build's identifier
        // names its own folder, and nothing of the shipped one is left in
        // the path it reads.
        let data = Path::new("scratch-profile").join("Roaming");
        let proof = "com.example.proof";
        let at = record_under(&data, proof);
        assert_eq!(at, data.join(proof).join(bootstrap::PATH_RECORD));
        assert_eq!(at.parent(), Some(data.join(proof).as_path()));

        let other = record_under(&data, "com.example.other");
        assert_ne!(at, other);
        assert!(other.starts_with(data.join("com.example.other")));
    }

    #[test]
    fn a_record_written_and_read_back_is_the_same_record() {
        let held = record(OURS, &format!("{OURS};C:\\Windows"), EXPANDABLE);
        let text = bootstrap::record_text(&held);
        assert_eq!(bootstrap::parse_record(&text), Some(held));
        assert_eq!(bootstrap::parse_record(&text[..text.len() / 2]), None);
        assert_eq!(bootstrap::parse_record("{\"other\": 1}"), None);
    }

    #[test]
    fn only_the_recorded_segment_comes_out() {
        let rest = "%USERPROFILE%\\bin;C:\\Windows;C:\\Windows\\System32";
        let value = format!("{OURS};{rest}");
        let held = record(OURS, &value, EXPANDABLE);
        match decide(Some(&held), EXPANDABLE, &value) {
            Removal::Take { kind, value } => {
                assert_eq!(kind, EXPANDABLE);
                assert_eq!(value.as_bytes(), rest.as_bytes());
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_value_edited_after_the_write_is_refused() {
        let rest = "C:\\Windows";
        let value = format!("{OURS};{rest}");
        let held = record(OURS, &value, EXPANDABLE);

        let appended = format!("{value};D:\\later");
        assert_eq!(decide(Some(&held), EXPANDABLE, &appended), Removal::Refuse);

        let edited = format!("{OURS};C:\\Windows\\System32");
        assert_eq!(decide(Some(&held), EXPANDABLE, &edited), Removal::Refuse);

        assert_eq!(decide(Some(&held), PLAIN, &value), Removal::Refuse);

        for reading in [&appended, &edited] {
            assert!(!matches!(
                decide(Some(&held), EXPANDABLE, reading),
                Removal::Take { .. }
            ));
        }
    }

    #[test]
    fn no_record_takes_nothing_out_of_a_value_that_looks_familiar() {
        let value = format!("{OURS};C:\\Windows");
        assert_eq!(decide(None, EXPANDABLE, &value), Removal::NoRecord);
    }

    #[test]
    fn a_segment_already_off_the_value_is_a_write_that_does_not_happen() {
        let held = record(OURS, &format!("{OURS};C:\\Windows"), EXPANDABLE);
        assert_eq!(decide(Some(&held), EXPANDABLE, "C:\\Windows"), Removal::Gone);
    }

    #[test]
    fn one_directory_written_three_ways_is_one_directory() {
        let held = record("C:\\a", "C:\\a;D:\\b", EXPANDABLE);
        for reading in ["C:\\a;D:\\b", "c:\\a\\;D:\\b", "\"C:\\A\";D:\\b"] {
            match decide(Some(&held), EXPANDABLE, reading) {
                Removal::Take { value, .. } => assert_eq!(value, "D:\\b"),
                other => panic!("{reading}: {other:?}"),
            }
        }
    }

    #[test]
    fn every_other_byte_of_the_value_survives_the_cut() {
        let rest = ";  D:\\padded  ;E:\\x";
        let value = format!("{OURS};{rest}");
        let held = record(OURS, &value, EXPANDABLE);
        match decide(Some(&held), EXPANDABLE, &value) {
            Removal::Take { value, .. } => assert_eq!(value.as_bytes(), rest.as_bytes()),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_segment_the_reader_added_back_by_themselves_is_refused() {
        let held = record("C:\\a", "C:\\a;D:\\b", EXPANDABLE);
        assert_eq!(
            decide(Some(&held), EXPANDABLE, "C:\\a;D:\\b;C:\\a"),
            Removal::Refuse
        );
    }

    #[test]
    fn a_segment_that_ends_the_value_takes_the_separator_before_it() {
        // The writer prepends, so the record's own value always carries
        // the segment in front and its cut takes the separator after it.
        // A reader who reordered their Path themselves leaves the segment
        // last, where there is no separator after it to take, and the cut
        // takes the one before it instead. Both remainders are the same
        // bytes, so a reorder is not an edit and the removal goes ahead.
        let held = record("C:\\a", "C:\\a;X", EXPANDABLE);
        match decide(Some(&held), EXPANDABLE, "X;C:\\a") {
            Removal::Take { value, .. } => assert_eq!(value.as_bytes(), b"X"),
            other => panic!("{other:?}"),
        }

        // And the same arm with something padded on the other side of it,
        // so the bytes that are kept are visibly not the bytes that are
        // cut. The separator that goes is the one immediately before the
        // segment, never the one before that.
        let padded = record("C:\\a", "C:\\a;  D:\\b  ;", EXPANDABLE);
        match decide(Some(&padded), EXPANDABLE, "  D:\\b  ;;C:\\a") {
            Removal::Take { value, .. } => assert_eq!(value.as_bytes(), b"  D:\\b  ;"),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_value_that_is_only_our_segment_comes_back_empty() {
        // Empty, and written back as empty. Nothing in this flow deletes
        // the value: the reader cannot tell a value that is not there
        // from one that is there and empty, so a record of a segment is
        // never proof that this app created the value it sat in.
        let held = record(OURS, OURS, EXPANDABLE);
        match decide(Some(&held), EXPANDABLE, OURS) {
            Removal::Take { kind, value } => {
                assert_eq!(kind, EXPANDABLE);
                assert_eq!(value.as_bytes(), b"");
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_write_that_did_not_take_names_the_entry_it_left() {
        // app-shell-02. The read worked and the decision was to take the
        // entry out; the one write refused. That is not a read failure,
        // and the entry is still on the Path with nothing else on the
        // machine naming it once the uninstaller deletes the record, so
        // the sentence names the segment the way the refusal's does.
        let held = record(OURS, &format!("{OURS};C:\\Windows"), EXPANDABLE);
        let (key, detail, code) = after_take(&held, Err(()));
        assert_eq!(key, strings::UNINSTALL_PATH_WRITE_ERROR);
        assert_ne!(key, strings::UNINSTALL_PATH_ERROR);
        assert_eq!(detail, OURS);
        assert_eq!(code, STOPPED);
        let words = strings::words();
        assert!(said(words, key).contains("{detail}"));
        assert_ne!(said(words, key), said(words, strings::UNINSTALL_PATH_ERROR));
        assert!(strings::fill(said(words, key), &detail).contains(OURS));

        // A write that took is the removed sentence, with nothing to name.
        assert_eq!(
            after_take(&held, Ok(())),
            (strings::UNINSTALL_PATH_REMOVED, String::new(), DONE)
        );
    }

    #[test]
    fn every_outcome_names_a_sentence_the_strings_file_carries() {
        let words = strings::words();
        let keys = [
            key_of(&Removal::NoRecord),
            key_of(&Removal::Gone),
            key_of(&Removal::Take { kind: EXPANDABLE, value: String::new() }),
            key_of(&Removal::Refuse),
            strings::UNINSTALL_PATH_ERROR,
            strings::UNINSTALL_PATH_WRITE_ERROR,
        ];
        // Six keys, six sentences: each one resolves to something the
        // file really carries, and no two of them resolve to the same
        // words. An arm left out of `said` falls through to the read
        // error's sentence, which is exactly what a duplicate would look
        // like.
        for (index, key) in keys.iter().enumerate() {
            assert!(!said(words, key).trim().is_empty(), "{key}");
            assert!(!keys[index + 1..].contains(key), "{key}");
            for later in &keys[index + 1..] {
                assert_ne!(said(words, key), said(words, later), "{key} / {later}");
            }
        }
        assert!(words.uninstall_path_kept.contains("{detail}"));
        assert!(words.uninstall_path_write_error.contains("{detail}"));
        assert!(words.uninstall_notice.contains("{detail}"));
    }
}
