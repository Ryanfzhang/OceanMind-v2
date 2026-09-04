//! Handle-backed validation for Windows paths accepted by the sandbox broker.
//!
//! The JSON protocol first rejects lexical escapes.  This module adds the
//! Windows-specific half: every existing path component is opened with
//! `FILE_FLAG_OPEN_REPARSE_POINT`, reparse points are rejected, and the final
//! handle identity must remain below the policy root's final handle identity.
//! The opened handles deliberately stay live until the future launch path has
//! consumed them, denying new write/delete sharing while that handoff occurs.
//! They are an additional race-resistance layer, not a substitute for the
//! immutable ACL and process-launch proof that remains unfinished.

#[cfg(windows)]
use std::collections::BTreeMap;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, GetFileInformationByHandle, GetFinalPathNameByHandleW, BY_HANDLE_FILE_INFORMATION,
    FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_NAME_NORMALIZED, FILE_READ_ATTRIBUTES, FILE_SHARE_READ,
    OPEN_EXISTING, VOLUME_NAME_DOS,
};

const MAX_COMPONENTS_PER_PATH: usize = 128;
#[cfg(windows)]
const MAX_GUARD_HANDLES: usize = 1024;

/// The canonical paths and live handles required by a future constrained launch.
///
/// `handles` remains private so a caller cannot accidentally close a guard
/// before the process is assigned to its Job Object.  The current broker is
/// fail closed and drops this guard before returning its typed rejection.
#[cfg(windows)]
pub struct NativePathGuard {
    handles: Vec<HANDLE>,
    pub executable: String,
    pub cwd: String,
    pub output_root: String,
    pub temporary_root: String,
    pub read_only_roots: Vec<String>,
    pub runtime_read_roots: Vec<String>,
    pub writable_roots: Vec<String>,
}

#[cfg(windows)]
impl Drop for NativePathGuard {
    fn drop(&mut self) {
        for handle in self.handles.drain(..) {
            // Each handle was returned by CreateFileW and is owned by this guard.
            unsafe { CloseHandle(handle) };
        }
    }
}

/// Open every request path and prove its final identity remains policy-owned.
///
/// This must run only after the protocol module has rejected device/UNC/ADS
/// syntax and verified the lexical root relation.  It intentionally accepts no
/// non-existent paths: the broker does not create parent directories from an
/// untrusted request.
#[cfg(windows)]
pub fn validate_native_path_contract(
    executable: &str,
    cwd: &str,
    output_root: &str,
    temporary_root: &str,
    read_only: &[String],
    runtime_read: &[String],
    writable: &[String],
) -> Result<NativePathGuard, String> {
    let mut opened = OpenedPathSet::default();
    let read_only_roots = canonical_roots(&mut opened, read_only)?;
    let runtime_read_roots = canonical_roots(&mut opened, runtime_read)?;
    let writable_roots = canonical_roots(&mut opened, writable)?;

    let executable = opened.open_chain(executable, false)?;
    let cwd = opened.open_chain(cwd, true)?;
    let output_root = opened.open_chain(output_root, true)?;
    let temporary_root = opened.open_chain(temporary_root, true)?;

    if !is_within_any(&executable, &runtime_read_roots) {
        return Err("Broker executable escapes the canonical runtime roots".to_owned());
    }
    let readable = read_only_roots
        .iter()
        .chain(runtime_read_roots.iter())
        .chain(writable_roots.iter())
        .cloned()
        .collect::<Vec<_>>();
    if !is_within_any(&cwd, &readable) {
        return Err("Broker cwd escapes the canonical readable roots".to_owned());
    }
    if !is_within_any(&output_root, &writable_roots)
        || !is_within_any(&temporary_root, &writable_roots)
    {
        return Err("Broker writable path escapes the canonical writable roots".to_owned());
    }

    Ok(NativePathGuard {
        handles: opened.take_handles(),
        executable,
        cwd,
        output_root,
        temporary_root,
        read_only_roots,
        runtime_read_roots,
        writable_roots,
    })
}

#[cfg(windows)]
fn canonical_roots(opened: &mut OpenedPathSet, roots: &[String]) -> Result<Vec<String>, String> {
    roots
        .iter()
        .map(|root| opened.open_chain(root, true))
        .collect()
}

#[cfg(windows)]
#[derive(Default)]
struct OpenedPathSet {
    handles: Vec<HANDLE>,
    components_by_requested_prefix: BTreeMap<String, OpenedComponentMetadata>,
}

#[cfg(windows)]
impl OpenedPathSet {
    fn take_handles(&mut self) -> Vec<HANDLE> {
        std::mem::take(&mut self.handles)
    }

    fn open_chain(&mut self, path: &str, require_directory: bool) -> Result<String, String> {
        let prefixes = windows_path_prefixes(path)?;
        let final_prefix = prefixes
            .last()
            .ok_or_else(|| "Broker path has no canonical components".to_owned())?;
        for prefix in &prefixes {
            let key = requested_path_key(prefix);
            if let std::collections::btree_map::Entry::Vacant(entry) =
                self.components_by_requested_prefix.entry(key)
            {
                if self.handles.len() >= MAX_GUARD_HANDLES {
                    return Err("Broker request exceeds the handle-validation budget".to_owned());
                }
                let opened = open_component(prefix)?;
                self.handles.push(opened.handle);
                entry.insert(OpenedComponentMetadata {
                    final_path: opened.final_path,
                    attributes: opened.attributes,
                });
            }
        }
        let final_path = self
            .components_by_requested_prefix
            .get(&requested_path_key(final_prefix))
            .map(|component| component.final_path.clone())
            .ok_or_else(|| "Broker path final handle is unavailable".to_owned())?;
        if require_directory {
            let attributes = self
                .components_by_requested_prefix
                .get(&requested_path_key(final_prefix))
                .map(|component| component.attributes)
                .ok_or_else(|| "Broker path attributes are unavailable".to_owned())?;
            if attributes & FILE_ATTRIBUTE_DIRECTORY == 0 {
                return Err("Broker policy root or working directory is not a directory".to_owned());
            }
        }
        Ok(final_path)
    }
}

#[cfg(windows)]
impl Drop for OpenedPathSet {
    fn drop(&mut self) {
        for handle in self.handles.drain(..) {
            // A failed validation must not leak the prefix handles opened so far.
            unsafe { CloseHandle(handle) };
        }
    }
}

#[cfg(windows)]
struct OpenedComponentMetadata {
    final_path: String,
    attributes: u32,
}

#[cfg(windows)]
struct OpenedComponent {
    handle: HANDLE,
    final_path: String,
    attributes: u32,
}

#[cfg(windows)]
fn open_component(path: &str) -> Result<OpenedComponent, String> {
    let wide = wide(path);
    let handle = unsafe {
        CreateFileW(
            wide.as_ptr(),
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err("Broker could not open a policy path component".to_owned());
    }
    let attributes = match file_attributes(handle) {
        Ok(value) => value,
        Err(error) => {
            unsafe { CloseHandle(handle) };
            return Err(error);
        }
    };
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        unsafe { CloseHandle(handle) };
        return Err("Broker policy path contains a reparse point".to_owned());
    }
    let final_path = match final_path_for_handle(handle) {
        Ok(value) => value,
        Err(error) => {
            unsafe { CloseHandle(handle) };
            return Err(error);
        }
    };
    Ok(OpenedComponent {
        handle,
        final_path,
        attributes,
    })
}

#[cfg(windows)]
fn file_attributes(handle: HANDLE) -> Result<u32, String> {
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    if unsafe { GetFileInformationByHandle(handle, &mut information) } == 0 {
        return Err("Broker could not inspect a policy path component".to_owned());
    }
    Ok(information.dwFileAttributes)
}

#[cfg(windows)]
fn final_path_for_handle(handle: HANDLE) -> Result<String, String> {
    let mut capacity = 512usize;
    loop {
        let mut buffer = vec![0u16; capacity];
        let written = unsafe {
            GetFinalPathNameByHandleW(
                handle,
                buffer.as_mut_ptr(),
                capacity as u32,
                FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
            )
        } as usize;
        if written == 0 {
            return Err("Broker could not resolve a canonical policy path".to_owned());
        }
        if written < capacity {
            let value = String::from_utf16(&buffer[..written])
                .map_err(|_| "Broker canonical policy path is not valid UTF-16".to_owned())?;
            return canonical_path_key(&value);
        }
        capacity = written.saturating_add(1);
        if capacity > 32_768 {
            return Err("Broker canonical policy path exceeds the bounded length".to_owned());
        }
    }
}

fn windows_path_prefixes(path: &str) -> Result<Vec<String>, String> {
    let normalized = path.replace('/', "\\");
    let prefix = normalized
        .get(..3)
        .filter(|prefix| {
            let bytes = prefix.as_bytes();
            bytes.len() == 3
                && bytes[0].is_ascii_alphabetic()
                && bytes[1] == b':'
                && bytes[2] == b'\\'
        })
        .ok_or_else(|| "Broker path is not drive-letter absolute".to_owned())?;
    let components = normalized[3..]
        .split('\\')
        .filter(|component| !component.is_empty())
        .collect::<Vec<_>>();
    if components.len() > MAX_COMPONENTS_PER_PATH {
        return Err("Broker policy path exceeds the component-validation budget".to_owned());
    }
    let mut result = vec![prefix.to_owned()];
    let mut current = prefix.trim_end_matches('\\').to_owned();
    for component in components {
        current.push('\\');
        current.push_str(component);
        result.push(current.clone());
    }
    Ok(result)
}

#[cfg(windows)]
fn requested_path_key(path: &str) -> String {
    path.replace('/', "\\")
        .trim_end_matches('\\')
        .to_ascii_lowercase()
}

#[cfg(windows)]
fn canonical_path_key(path: &str) -> Result<String, String> {
    let path = path
        .strip_prefix("\\\\?\\")
        .ok_or_else(|| "Broker final policy path is not DOS-canonical".to_owned())?;
    if path.starts_with("UNC\\") {
        return Err("Broker final policy path resolved to a UNC path".to_owned());
    }
    let bytes = path.as_bytes();
    if bytes.len() < 3 || !bytes[0].is_ascii_alphabetic() || bytes[1] != b':' || bytes[2] != b'\\' {
        return Err("Broker final policy path is not a drive-letter path".to_owned());
    }
    Ok(path.trim_end_matches('\\').to_ascii_lowercase())
}

#[cfg(windows)]
fn is_within_any(path: &str, roots: &[String]) -> bool {
    roots.iter().any(|root| is_within(path, root))
}

#[cfg(windows)]
fn is_within(path: &str, root: &str) -> bool {
    path == root
        || path
            .strip_prefix(root)
            .is_some_and(|suffix| suffix.starts_with('\\'))
}

#[cfg(windows)]
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(test)]
mod tests {
    use super::windows_path_prefixes;

    #[test]
    fn creates_bounded_drive_prefixes_without_parent_normalization() {
        assert_eq!(
            windows_path_prefixes("C:\\work\\input\\field.nc").unwrap(),
            vec![
                "C:\\".to_owned(),
                "C:\\work".to_owned(),
                "C:\\work\\input".to_owned(),
                "C:\\work\\input\\field.nc".to_owned(),
            ]
        );
    }

    #[test]
    fn rejects_a_path_with_an_unbounded_number_of_components() {
        let path = format!("C:\\{}", vec!["one"; 129].join("\\"));

        assert!(windows_path_prefixes(&path).is_err());
    }
}
