//! D13 `config.rs`: the pinned bootstrap values and their one override.
//!
//! The pins live in `app/src/bootstrap.json` and are compiled in with
//! `include_str!`: the uv version, one archive row per operating system
//! this shell ships to — its two URLs and its checksum — the package
//! source with the digest its wheel must answer to, the Python pin, and
//! the oldest engine this shell drives. The
//! build reads its own platform's row and validation proves every row,
//! so a bad pin for either platform stops both builds.
//!
//! Validation runs before the window is drawn and stops hard on failure.
//! It cannot fire in a shipped build — the pytest hold checks the same
//! file by the same rules — so the stop is a stop, not a screen: a
//! sentence about it would be a sentence no reader can ever reach.

use serde::Deserialize;
use std::path::Path;
use std::sync::OnceLock;

/// The file itself, compiled in.
const SOURCE: &str = include_str!("../../src/bootstrap.json");

/// The stop when the compiled-in pins are not the shape the shell needs.
const UNPINNED: &str = "the bootstrap pins did not hold";

/// What the two URL templates carry in place of the version.
const VERSION_SLOT: &str = "<ver>";

/// The scheme, and the only one. A plain-http hop would put the bytes
/// this app runs on the wire for anyone to answer.
const HTTPS: &str = "https://";

/// The PEP 508 joiner, which is the one form a package source may take
/// besides a bare address.
const NAMED: &str = " @ ";

/// The one override, for the in-repo proof and nothing else.
///
/// It is compiled into debug builds alone, so a release binary carries no
/// such switch. When it is set, the value passes the same validator the
/// file value passes, a bad value is a hard stop, and the bootstrap
/// screen says on its face that the override is in force.
#[cfg(debug_assertions)]
pub const PACKAGE_SOURCE_OVERRIDE_ENV: &str = "RADIALRIG_PACKAGE_SOURCE";

/// The archive file each platform's templates must name. The name is the
/// whole of what varies between the rows besides the digest, and pinning
/// it is what stops a Linux build reading the Windows archive or the
/// other way around.
pub const WINDOWS_ARCHIVE: &str = "uv-x86_64-pc-windows-msvc.zip";
pub const LINUX_ARCHIVE: &str = "uv-x86_64-unknown-linux-gnu.tar.gz";

/// One operating system's archive row: where the pinned archive is
/// fetched from, in the order the two hosts are tried, and the digest
/// its bytes must answer to.
#[derive(Deserialize, Clone, Debug)]
pub struct Archive {
    pub url_primary: String,
    pub url_fallback: String,
    pub sha256: String,
}

/// The archive rows, one per operating system this shell ships to. Named
/// fields rather than a map: a build for a platform with no row is a
/// build that must not compile, and a field does that where a map lookup
/// would defer it to a runtime stop.
#[derive(Deserialize, Clone, Debug)]
pub struct Archives {
    pub windows: Archive,
    pub linux: Archive,
}

/// The pins, as the file writes them.
#[derive(Deserialize, Clone, Debug)]
pub struct Pins {
    pub uv_version: String,
    pub uv_archives: Archives,
    pub package_source: String,
    pub package_sha256: String,
    pub python_pin: String,
    pub engine_min_version: String,
}

impl Pins {
    /// This build's own archive row, picked at compile time.
    pub fn archive(&self) -> &Archive {
        #[cfg(windows)]
        {
            &self.uv_archives.windows
        }
        #[cfg(not(windows))]
        {
            &self.uv_archives.linux
        }
    }

    /// The digest this build's archive bytes must answer to.
    pub fn archive_sha256(&self) -> &str {
        &self.archive().sha256
    }

    /// The two archive addresses, in the order they are tried, with the
    /// pinned version put where each template carries its slot.
    ///
    /// The download that reads them is the next unit's; the templates are
    /// pinned and proved here so that unit finds them already answerable.
    #[allow(dead_code)]
    pub fn archive_urls(&self) -> [String; 2] {
        let row = self.archive();
        [
            row.url_primary.replace(VERSION_SLOT, &self.uv_version),
            row.url_fallback.replace(VERSION_SLOT, &self.uv_version),
        ]
    }
}

/// Where the package address the shell installs from came from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Source {
    /// The value handed to `uv tool install`.
    pub value: String,
    /// Whether the debug switch put it there rather than the file.
    pub overridden: bool,
}

static PINS: OnceLock<Pins> = OnceLock::new();

/// The pins, parsed and validated once. A hard stop on failure.
pub fn pins() -> &'static Pins {
    PINS.get_or_init(|| {
        let pins: Pins = serde_json::from_str(SOURCE).expect(UNPINNED);
        validate(&pins).expect(UNPINNED);
        pins
    })
}

/// The package address this run installs from, and whether the debug
/// switch chose it. A switch that is set and does not hold is a stop.
pub fn source(pins: &Pins) -> Source {
    match overridden() {
        Some(value) => {
            assert!(override_holds(&value), "{}", UNPINNED);
            Source { value, overridden: true }
        }
        None => Source { value: pins.package_source.clone(), overridden: false },
    }
}

#[cfg(debug_assertions)]
fn overridden() -> Option<String> {
    match std::env::var(PACKAGE_SOURCE_OVERRIDE_ENV) {
        Ok(value) if !value.trim().is_empty() => Some(value.trim().to_string()),
        _ => None,
    }
}

#[cfg(not(debug_assertions))]
fn overridden() -> Option<String> {
    None
}

// --- the rules, each one its own answerable question -------------------

/// Three numbers with dots between them, and nothing else.
pub fn triple(said: &str) -> bool {
    numbers(said, 3)
}

/// Two numbers with a dot between them, and nothing else.
pub fn pair(said: &str) -> bool {
    numbers(said, 2)
}

fn numbers(said: &str, wanted: usize) -> bool {
    let parts: Vec<&str> = said.split('.').collect();
    parts.len() == wanted
        && parts
            .iter()
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
}

/// Sixty-four lowercase hex digits.
pub fn hex64(said: &str) -> bool {
    said.len() == 64
        && said
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// An https address, with no second scheme hiding inside it.
pub fn https_address(said: &str) -> bool {
    match said.strip_prefix(HTTPS) {
        Some(rest) => !rest.is_empty() && !rest.contains("://"),
        None => false,
    }
}

/// An https template that carries the version slot and names the one
/// archive its row is for.
pub fn archive_template(said: &str, names: &str) -> bool {
    https_address(said) && said.contains(VERSION_SLOT) && said.ends_with(names)
}

/// A package source: an address, or the named form that carries one.
/// A bare name is not a source — it would reach a public index.
pub fn package_source(said: &str) -> bool {
    if said.trim() != said || said.is_empty() {
        return false;
    }
    let address = match said.split_once(NAMED) {
        Some((name, rest)) => {
            if name.is_empty() || name.contains(' ') {
                return false;
            }
            rest
        }
        None => said,
    };
    https_address(address)
}

/// The address inside a package source, which is the whole of it when
/// no name stands in front. The download that fetches the wheel asks
/// for this, because the PEP 508 name is for uv and not for a GET.
pub fn address_of(said: &str) -> &str {
    match said.split_once(NAMED) {
        Some((_, rest)) => rest,
        None => said,
    }
}

/// The one extra shape the debug switch may take: an absolute path to a
/// wheel that is on this machine.
///
/// Ruled, and recorded: without a local form the switch cannot serve the
/// in-repo proof it exists for. No https address carries an unreleased
/// wheel, so an https-only switch would have nothing to point at.
pub fn local_wheel(said: &str) -> bool {
    let path = Path::new(said);
    path.is_absolute()
        && said.to_ascii_lowercase().ends_with(".whl")
        && path.is_file()
}

/// The debug switch's value, by the same rules as the file value plus the
/// one local form.
pub fn override_holds(said: &str) -> bool {
    package_source(said) || local_wheel(said)
}

/// One archive row's three rules, against the archive its row names.
fn validate_archive(row: &Archive, names: &str) -> bool {
    archive_template(&row.url_primary, names)
        && archive_template(&row.url_fallback, names)
        && hex64(&row.sha256)
}

/// Every rule, over one set of pins. Both rows are proved on both
/// platforms, so a bad Linux pin stops the Windows build too rather than
/// waiting for the one build that would read it.
pub fn validate(pins: &Pins) -> Result<(), &'static str> {
    if !triple(&pins.uv_version) {
        return Err("uv_version");
    }
    if !validate_archive(&pins.uv_archives.windows, WINDOWS_ARCHIVE) {
        return Err("uv_archives.windows");
    }
    if !validate_archive(&pins.uv_archives.linux, LINUX_ARCHIVE) {
        return Err("uv_archives.linux");
    }
    if !package_source(&pins.package_source) {
        return Err("package_source");
    }
    if !hex64(&pins.package_sha256) {
        return Err("package_sha256");
    }
    if !pair(&pins.python_pin) {
        return Err("python_pin");
    }
    if !triple(&pins.engine_min_version) {
        return Err("engine_min_version");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shipped() -> Pins {
        serde_json::from_str(SOURCE).expect("the shipped pins parse")
    }

    #[test]
    fn the_shipped_pins_hold() {
        let pins = shipped();
        assert_eq!(validate(&pins), Ok(()));
        let urls = pins.archive_urls();
        for url in &urls {
            assert!(!url.contains(VERSION_SLOT));
            assert!(url.contains(&pins.uv_version));
            assert!(https_address(url));
        }
        assert_ne!(urls[0], urls[1]);

        // This build's row names this build's archive, and the two rows
        // carry two different archives with two different digests.
        let names = if cfg!(windows) { WINDOWS_ARCHIVE } else { LINUX_ARCHIVE };
        for url in &urls {
            assert!(url.ends_with(names));
        }
        assert_ne!(
            pins.uv_archives.windows.sha256,
            pins.uv_archives.linux.sha256
        );
        assert_eq!(pins.archive_sha256(), &pins.archive().sha256);
    }

    #[test]
    fn a_version_is_three_numbers_and_a_pin_is_two() {
        assert!(triple("0.12.5"));
        assert!(!triple("0.12"));
        assert!(!triple("0.12.5.1"));
        assert!(!triple("0.12.x"));
        assert!(!triple("v0.12.5"));
        assert!(!triple(""));
        assert!(!triple("0..5"));
        assert!(pair("3.13"));
        assert!(!pair("3"));
        assert!(!pair("3.13.1"));
    }

    #[test]
    fn a_checksum_is_sixty_four_lowercase_hex_digits() {
        let good = "4c4d49d8738847d9b71ba319e49a5688c93eac0fe6204b1df24e98528dddf39a";
        assert!(hex64(good));
        assert!(!hex64(&good.to_ascii_uppercase()));
        assert!(!hex64(&good[..63]));
        assert!(!hex64(&format!("{good}0")));
        assert!(!hex64(&good.replace('4', "g")));
    }

    #[test]
    fn only_an_https_address_with_no_second_scheme_passes() {
        assert!(https_address("https://example.test/a.whl"));
        assert!(!https_address("http://example.test/a.whl"));
        assert!(!https_address("HTTPS://example.test/a.whl"));
        assert!(!https_address("https://"));
        assert!(!https_address("https://example.test/?u=http://elsewhere.test"));
        assert!(!https_address("file:///c:/a.whl"));
    }

    #[test]
    fn an_archive_template_names_its_own_rows_archive_and_carries_the_slot() {
        let pins = shipped();
        let windows = &pins.uv_archives.windows;
        let linux = &pins.uv_archives.linux;
        assert!(archive_template(&windows.url_primary, WINDOWS_ARCHIVE));
        assert!(archive_template(&linux.url_primary, LINUX_ARCHIVE));
        // The rows do not answer for each other's archive.
        assert!(!archive_template(&windows.url_primary, LINUX_ARCHIVE));
        assert!(!archive_template(&linux.url_primary, WINDOWS_ARCHIVE));
        assert!(!archive_template(
            &windows.url_primary.replace(VERSION_SLOT, "0.1.0"),
            WINDOWS_ARCHIVE
        ));
        assert!(!archive_template("https://example.test/<ver>/other.zip", WINDOWS_ARCHIVE));
    }

    #[test]
    fn a_bare_name_is_never_a_package_source() {
        assert!(!package_source("radialrig"));
        assert!(!package_source("radialrig==0.1.0"));
        assert!(!package_source(""));
        assert!(!package_source(" https://example.test/a.whl"));
        assert!(package_source("https://example.test/a.whl"));
        assert!(package_source("radialrig @ https://example.test/a.whl"));
        assert!(!package_source("radialrig @ http://example.test/a.whl"));
        assert!(!package_source("radialrig @ a.whl"));
        assert!(!package_source("two names @ https://example.test/a.whl"));
    }

    #[test]
    fn the_switch_takes_an_address_or_a_wheel_that_is_on_this_machine() {
        assert!(override_holds("radialrig @ https://example.test/a.whl"));
        assert!(!override_holds("radialrig"));
        // A relative path is not a location this shell can resolve, and a
        // path to nothing is not a wheel however it is spelled.
        assert!(!local_wheel("dist/radialrig-0.1.0-py3-none-any.whl"));
        assert!(!local_wheel("C:\\nowhere\\radialrig-0.1.0-py3-none-any.whl"));

        let mut made = std::env::temp_dir();
        made.push(format!("radialrig-{}-py3-none-any.whl", std::process::id()));
        std::fs::write(&made, b"").expect("a wheel to point at");
        let said = made.to_string_lossy().to_string();
        assert!(local_wheel(&said));
        assert!(override_holds(&said));
        assert!(!package_source(&said));
        std::fs::remove_file(&made).ok();
        assert!(!local_wheel(&said));
    }

    #[test]
    fn every_rule_names_the_pin_it_refused() {
        let mut pins = shipped();
        pins.uv_archives.windows.sha256 = "0".to_string();
        assert_eq!(validate(&pins), Err("uv_archives.windows"));

        let mut pins = shipped();
        pins.uv_archives.linux.sha256 = "0".to_string();
        assert_eq!(validate(&pins), Err("uv_archives.linux"));

        let mut pins = shipped();
        pins.package_source = "radialrig".to_string();
        assert_eq!(validate(&pins), Err("package_source"));

        let mut pins = shipped();
        pins.uv_archives.windows.url_primary =
            pins.uv_archives.windows.url_primary.replace(HTTPS, "http://");
        assert_eq!(validate(&pins), Err("uv_archives.windows"));

        let mut pins = shipped();
        pins.uv_archives.linux.url_primary =
            pins.uv_archives.linux.url_primary.replace(HTTPS, "http://");
        assert_eq!(validate(&pins), Err("uv_archives.linux"));

        let mut pins = shipped();
        pins.python_pin = "3".to_string();
        assert_eq!(validate(&pins), Err("python_pin"));

        let mut pins = shipped();
        pins.package_sha256 = "0".to_string();
        assert_eq!(validate(&pins), Err("package_sha256"));
    }

    #[test]
    fn the_wheels_digest_is_pinned_by_the_same_rule_the_archive_digest_is() {
        let pins = shipped();
        assert!(hex64(&pins.package_sha256));
        assert_eq!(pins.package_sha256, pins.package_sha256.to_ascii_lowercase());
        // The wheel and the archives are three different files, so no
        // two of the three pins may be the same digest.
        assert_ne!(pins.package_sha256, pins.uv_archives.windows.sha256);
        assert_ne!(pins.package_sha256, pins.uv_archives.linux.sha256);

        // Every shape the archive digest refuses, the wheel digest
        // refuses too: it goes through the one hex64 rule.
        let shouted = pins.package_sha256.to_ascii_uppercase();
        let prefixed = format!("sha256:{}", pins.package_sha256);
        let longer = format!("{}0", pins.package_sha256);
        for said in [
            "",
            "0",
            shouted.as_str(),
            &pins.package_sha256[..63],
            prefixed.as_str(),
            longer.as_str(),
        ] {
            let mut bad = shipped();
            bad.package_sha256 = said.to_string();
            assert_eq!(validate(&bad), Err("package_sha256"), "{said}");
        }
    }

    #[test]
    fn the_address_is_what_is_left_when_the_name_is_taken_off() {
        let pins = shipped();
        let address = address_of(&pins.package_source);
        assert!(https_address(address));
        assert!(!address.contains(NAMED));
        assert!(pins.package_source.ends_with(address));

        assert_eq!(
            address_of("radialrig @ https://example.test/a.whl"),
            "https://example.test/a.whl"
        );
        // A bare address is already the address.
        assert_eq!(
            address_of("https://example.test/a.whl"),
            "https://example.test/a.whl"
        );
    }

    #[test]
    fn the_file_value_stands_when_no_switch_is_set() {
        let pins = shipped();
        let chosen = source(&pins);
        // A test process carrying the switch would say so; the check is
        // that the two answers cannot disagree about which one it is.
        if chosen.overridden {
            assert_ne!(chosen.value, pins.package_source);
        } else {
            assert_eq!(chosen.value, pins.package_source);
        }
        assert!(override_holds(&chosen.value));
    }
}
