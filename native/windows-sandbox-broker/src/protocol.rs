use std::collections::BTreeMap;

pub fn validate_path_contract(
    executable: &str,
    cwd: &str,
    output_root: &str,
    temporary_root: &str,
    read_only: &[String],
    runtime_read: &[String],
    writable: &[String],
) -> Result<(), String> {
    let all_roots = read_only
        .iter()
        .chain(runtime_read.iter())
        .chain(writable.iter());
    if all_roots.clone().any(|path| !is_safe_windows_path(path))
        || !is_safe_windows_path(executable)
        || !is_safe_windows_path(cwd)
        || !is_safe_windows_path(output_root)
        || !is_safe_windows_path(temporary_root)
    {
        return Err("Broker request contains a non-canonical or device path".to_owned());
    }
    if !is_within_any(executable, runtime_read) {
        return Err("Broker executable must be below a declared runtime root".to_owned());
    }
    let readable = read_only
        .iter()
        .chain(runtime_read.iter())
        .chain(writable.iter())
        .cloned()
        .collect::<Vec<_>>();
    if !is_within_any(cwd, &readable)
        || !is_within_any(output_root, writable)
        || !is_within_any(temporary_root, writable)
    {
        return Err("Broker cwd, output, or temporary root escapes its declared roots".to_owned());
    }
    Ok(())
}

pub fn validate_environment_keys(environment: &BTreeMap<String, String>) -> bool {
    let mut names = std::collections::BTreeSet::new();
    environment.iter().all(|(key, value)| {
        let normalized = key.to_ascii_uppercase();
        is_valid_environment_name(key)
            && is_bounded_text(value)
            && names.insert(normalized.clone())
            && !matches!(
                normalized.as_str(),
                "PATH"
                    | "PATHEXT"
                    | "COMSPEC"
                    | "HOME"
                    | "USERPROFILE"
                    | "APPDATA"
                    | "LOCALAPPDATA"
            )
    })
}

pub(crate) fn is_valid_environment_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.is_ascii()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

fn is_bounded_text(value: &str) -> bool {
    !value.is_empty() && value.len() <= 32_768 && !value.contains('\0')
}

fn is_safe_windows_path(value: &str) -> bool {
    let bytes = value.as_bytes();
    if value.len() > 32_768
        || value.contains('\0')
        || value.starts_with("\\\\?\\")
        || value.starts_with("\\\\.\\")
        || value.starts_with("\\\\")
        || bytes.len() < 3
        || !bytes[0].is_ascii_alphabetic()
        || bytes[1] != b':'
        || (bytes[2] != b'\\' && bytes[2] != b'/')
    {
        return false;
    }
    let normalized = value.replace('/', "\\");
    normalized[3..].split('\\').all(is_safe_component)
}

fn is_safe_component(component: &str) -> bool {
    if component.is_empty() {
        return true;
    }
    if component == "." || component == ".." || component.contains(':') {
        return false;
    }
    let trimmed = component.trim_end_matches(['.', ' ']).to_ascii_uppercase();
    !matches!(
        trimmed.as_str(),
        "CON"
            | "PRN"
            | "AUX"
            | "NUL"
            | "COM1"
            | "COM2"
            | "COM3"
            | "COM4"
            | "COM5"
            | "COM6"
            | "COM7"
            | "COM8"
            | "COM9"
            | "LPT1"
            | "LPT2"
            | "LPT3"
            | "LPT4"
            | "LPT5"
            | "LPT6"
            | "LPT7"
            | "LPT8"
            | "LPT9"
    )
}

fn is_within_any(path: &str, roots: &[String]) -> bool {
    roots.iter().any(|root| is_within(path, root))
}

fn is_within(path: &str, root: &str) -> bool {
    let path = normalized_path(path);
    let root = normalized_path(root);
    path == root
        || path
            .strip_prefix(&root)
            .is_some_and(|suffix| suffix.starts_with('\\'))
}

fn normalized_path(value: &str) -> String {
    value
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::{validate_environment_keys, validate_path_contract};

    fn roots() -> (Vec<String>, Vec<String>, Vec<String>) {
        (
            vec!["C:\\work".to_owned()],
            vec!["C:\\runtime".to_owned()],
            vec!["C:\\output".to_owned(), "C:\\temporary".to_owned()],
        )
    }

    #[test]
    fn accepts_a_policy_owned_windows_path_contract() {
        let (read_only, runtime, writable) = roots();
        assert!(validate_path_contract(
            "C:\\runtime\\python.exe",
            "C:\\work",
            "C:\\output",
            "C:\\temporary",
            &read_only,
            &runtime,
            &writable,
        )
        .is_ok());
    }

    #[test]
    fn rejects_unc_device_ads_traversal_and_reserved_names() {
        let (read_only, runtime, writable) = roots();
        for escaped in [
            "\\\\server\\share\\input.nc",
            "\\\\?\\C:\\work\\input.nc",
            "C:\\work\\..\\private",
            "C:\\work\\result.nc:metadata",
            "C:\\work\\NUL",
        ] {
            assert!(validate_path_contract(
                "C:\\runtime\\python.exe",
                escaped,
                "C:\\output",
                "C:\\temporary",
                &read_only,
                &runtime,
                &writable,
            )
            .is_err());
        }
    }

    #[test]
    fn rejects_paths_that_escape_declared_root_kinds() {
        let (read_only, runtime, writable) = roots();
        assert!(validate_path_contract(
            "C:\\private\\python.exe",
            "C:\\work",
            "C:\\output",
            "C:\\temporary",
            &read_only,
            &runtime,
            &writable,
        )
        .is_err());
        assert!(validate_path_contract(
            "C:\\runtime\\python.exe",
            "C:\\work",
            "C:\\private\\output",
            "C:\\temporary",
            &read_only,
            &runtime,
            &writable,
        )
        .is_err());
    }

    #[test]
    fn rejects_parent_environment_escape_keys() {
        let mut environment = BTreeMap::from([("OUTPUT_DIR".to_owned(), "C:\\output".to_owned())]);
        assert!(validate_environment_keys(&environment));
        environment.insert("PATH".to_owned(), "C:\\private".to_owned());
        assert!(!validate_environment_keys(&environment));
    }

    #[test]
    fn rejects_case_colliding_environment_keys() {
        let environment = BTreeMap::from([
            ("OUTPUT_DIR".to_owned(), "C:\\output".to_owned()),
            ("output_dir".to_owned(), "C:\\other".to_owned()),
        ]);

        assert!(!validate_environment_keys(&environment));
    }
}
