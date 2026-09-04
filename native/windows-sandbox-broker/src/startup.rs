//! Pure Win32 process-startup encoders.
//!
//! The future AppContainer launcher will pass an explicit application path and
//! the command line/environment produced here. Keeping quoting and environment
//! construction independent of `CreateProcessW` makes the edge cases testable
//! on every development platform before a Windows-only launch path is enabled.

use std::collections::{BTreeMap, BTreeSet};

use crate::protocol::is_valid_environment_name;

const MAX_COMMAND_LINE_UTF16: usize = 32_767;

pub fn build_windows_command_line(
    executable: &str,
    arguments: &[String],
) -> Result<Vec<u16>, String> {
    let mut command_line = quote_windows_argument(executable)?;
    for argument in arguments {
        command_line.push(' ');
        command_line.push_str(&quote_windows_argument(argument)?);
    }
    let mut encoded = command_line.encode_utf16().collect::<Vec<_>>();
    if encoded.len() >= MAX_COMMAND_LINE_UTF16 {
        return Err("Broker command line exceeds the Windows UTF-16 limit".to_owned());
    }
    encoded.push(0);
    Ok(encoded)
}

/// Quote one argv element according to the CommandLineToArgvW / CRT backslash
/// rules. `CreateProcessW` receives this as a mutable UTF-16 buffer.
pub fn quote_windows_argument(value: &str) -> Result<String, String> {
    if value.is_empty() || value.contains('\0') {
        return Err("Broker command argument is empty or contains NUL".to_owned());
    }
    if !value
        .chars()
        .any(|character| character.is_whitespace() || character == '"')
    {
        return Ok(value.to_owned());
    }
    let mut quoted = String::with_capacity(value.len() + 2);
    quoted.push('"');
    let mut pending_backslashes = 0usize;
    for character in value.chars() {
        if character == '\\' {
            pending_backslashes += 1;
            continue;
        }
        if character == '"' {
            quoted.push_str(&"\\".repeat(pending_backslashes.saturating_mul(2).saturating_add(1)));
            quoted.push('"');
        } else {
            quoted.push_str(&"\\".repeat(pending_backslashes));
            quoted.push(character);
        }
        pending_backslashes = 0;
    }
    quoted.push_str(&"\\".repeat(pending_backslashes.saturating_mul(2)));
    quoted.push('"');
    Ok(quoted)
}

/// Build a deterministic, double-NUL-terminated Windows environment block.
///
/// Windows environment names are case-insensitive. The broker treats a case
/// collision as malformed rather than allowing the host's merge rule to choose
/// an unexpected value.
pub fn build_windows_environment_block(
    environment: &BTreeMap<String, String>,
) -> Result<Vec<u16>, String> {
    let mut normalized_names = BTreeSet::new();
    let mut entries = Vec::with_capacity(environment.len());
    for (name, value) in environment {
        if !is_valid_environment_name(name) || value.contains('\0') || value.len() > 32_768 {
            return Err("Broker environment contains malformed text".to_owned());
        }
        let normalized = name.to_ascii_uppercase();
        if !normalized_names.insert(normalized.clone()) {
            return Err("Broker environment contains case-colliding names".to_owned());
        }
        entries.push((normalized, format!("{name}={value}")));
    }
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    let mut block = Vec::new();
    for (_, entry) in entries {
        block.extend(entry.encode_utf16());
        block.push(0);
    }
    block.push(0);
    if environment.is_empty() {
        block.push(0);
    }
    if block.len() > MAX_COMMAND_LINE_UTF16 {
        return Err("Broker environment block exceeds the Windows UTF-16 limit".to_owned());
    }
    Ok(block)
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::{
        build_windows_command_line, build_windows_environment_block, quote_windows_argument,
    };

    #[test]
    fn quotes_spaces_quotes_and_trailing_backslashes() {
        assert_eq!(quote_windows_argument("plain").unwrap(), "plain");
        assert_eq!(
            quote_windows_argument("two words").unwrap(),
            "\"two words\""
        );
        assert_eq!(quote_windows_argument("a\"b").unwrap(), "\"a\\\"b\"");
        assert_eq!(
            quote_windows_argument("C:\\two words\\").unwrap(),
            "\"C:\\two words\\\\\""
        );
    }

    #[test]
    fn constructs_a_null_terminated_command_line() {
        let command_line = build_windows_command_line(
            "C:\\runtime\\python.exe",
            &[
                "C:\\work dir\\analysis.py".to_owned(),
                "--name".to_owned(),
                "A B".to_owned(),
            ],
        )
        .unwrap();
        let text = String::from_utf16(&command_line[..command_line.len() - 1]).unwrap();

        assert_eq!(
            text,
            "C:\\runtime\\python.exe \"C:\\work dir\\analysis.py\" --name \"A B\""
        );
        assert_eq!(command_line.last(), Some(&0));
    }

    #[test]
    fn rejects_case_colliding_environment_names() {
        let environment = BTreeMap::from([
            ("OUTPUT_DIR".to_owned(), "C:\\output".to_owned()),
            ("output_dir".to_owned(), "C:\\other".to_owned()),
        ]);

        assert!(build_windows_environment_block(&environment).is_err());
    }

    #[test]
    fn sorts_and_double_terminates_environment_block() {
        let environment = BTreeMap::from([
            ("Z_LAST".to_owned(), "z".to_owned()),
            ("A_FIRST".to_owned(), "a".to_owned()),
        ]);
        let block = build_windows_environment_block(&environment).unwrap();
        let text = String::from_utf16(&block).unwrap();

        assert_eq!(text, "A_FIRST=a\0Z_LAST=z\0\0");
    }

    #[test]
    fn empty_environment_is_still_double_terminated() {
        assert_eq!(
            build_windows_environment_block(&BTreeMap::new()).unwrap(),
            vec![0, 0]
        );
    }
}
