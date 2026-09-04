//! Temporary AppContainer DACL grants for one verified broker request.
//!
//! An AppContainer has no ambient access to the caller's files.  The broker
//! therefore grants its freshly-created SID only the minimum rights required
//! for the request's already handle-validated roots.  Each root's original
//! DACL is retained through process-tree termination and restored through the
//! same open handle.  The root ACE is inheritable so Windows applies it to the
//! existing immutable runtime/code/input tree and to newly-created attempt
//! outputs; restoring the parent DACL removes those inherited grant copies.

use std::collections::BTreeMap;
use std::ffi::c_void;
use std::mem::{size_of, zeroed};

use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::Foundation::{GENERIC_EXECUTE, GENERIC_READ, GENERIC_WRITE};
use windows_sys::Win32::Security::Authorization::{
    GetSecurityInfo, SetEntriesInAclW, SetSecurityInfo, EXPLICIT_ACCESS_W, GRANT_ACCESS,
    SE_FILE_OBJECT, TRUSTEE_IS_SID, TRUSTEE_IS_UNKNOWN, TRUSTEE_W,
};
use windows_sys::Win32::Security::{
    DACL_SECURITY_INFORMATION, PSID, SUB_CONTAINERS_AND_OBJECTS_INHERIT,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ATTRIBUTE_DIRECTORY, FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES, FILE_SHARE_READ, OPEN_EXISTING,
    READ_CONTROL, WRITE_DAC,
};

use crate::path_guard::NativePathGuard;

const READ_ONLY_RIGHTS: u32 = GENERIC_READ;
const RUNTIME_RIGHTS: u32 = GENERIC_READ | GENERIC_EXECUTE;
const WRITABLE_RIGHTS: u32 = GENERIC_READ | GENERIC_WRITE;

/// Owns every temporary DACL alteration made for an AppContainer attempt.
///
/// The caller must call `restore_all` only after the Job Object confirms the
/// whole process tree is gone.  `Drop` makes a second best-effort restore on
/// early errors; an unsuccessful explicit restore is never represented as a
/// trusted broker result.
pub struct AppContainerAclGrants {
    grants: Vec<AclGrant>,
}

impl AppContainerAclGrants {
    pub fn apply(path_guard: &NativePathGuard, sid: PSID) -> Result<Self, String> {
        if sid.is_null() {
            return Err("Broker AppContainer identity has no SID for DACL grants".to_owned());
        }
        let mut targets = BTreeMap::<String, u32>::new();
        for root in &path_guard.read_only_roots {
            merge_rights(&mut targets, root, READ_ONLY_RIGHTS);
        }
        for root in &path_guard.runtime_read_roots {
            merge_rights(&mut targets, root, RUNTIME_RIGHTS);
        }
        for root in &path_guard.writable_roots {
            merge_rights(&mut targets, root, WRITABLE_RIGHTS);
        }
        // A pre-existing nested cwd/output/temp directory can carry a
        // protected DACL, so the broker grants its exact handle too instead of
        // assuming inheritance from a parent root.
        merge_rights(&mut targets, &path_guard.cwd, READ_ONLY_RIGHTS);
        merge_rights(&mut targets, &path_guard.output_root, WRITABLE_RIGHTS);
        merge_rights(&mut targets, &path_guard.temporary_root, WRITABLE_RIGHTS);

        let mut grants = Vec::with_capacity(targets.len());
        for (path, rights) in targets {
            grants.push(AclGrant::grant_directory(&path, sid, rights)?);
        }
        Ok(Self { grants })
    }

    pub fn restore_all(&mut self) -> Result<(), String> {
        let mut first_error = None;
        for grant in self.grants.iter_mut().rev() {
            if let Err(error) = grant.restore() {
                first_error.get_or_insert(error);
            }
        }
        if let Some(error) = first_error {
            return Err(error);
        }
        Ok(())
    }
}

impl Drop for AppContainerAclGrants {
    fn drop(&mut self) {
        let _ = self.restore_all();
    }
}

fn merge_rights(targets: &mut BTreeMap<String, u32>, path: &str, rights: u32) {
    targets
        .entry(path.to_owned())
        .and_modify(|existing| *existing |= rights)
        .or_insert(rights);
}

struct AclGrant {
    handle: HANDLE,
    original_dacl: *mut windows_sys::Win32::Security::ACL,
    original_descriptor: windows_sys::Win32::Security::PSECURITY_DESCRIPTOR,
    active: bool,
}

impl AclGrant {
    fn grant_directory(path: &str, sid: PSID, rights: u32) -> Result<Self, String> {
        let handle = open_directory_for_acl(path)?;
        let mut original_dacl = std::ptr::null_mut();
        let mut original_descriptor = std::ptr::null_mut();
        let get_status = unsafe {
            GetSecurityInfo(
                handle,
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut original_dacl,
                std::ptr::null_mut(),
                &mut original_descriptor,
            )
        };
        if get_status != 0 || original_descriptor.is_null() {
            unsafe { CloseHandle(handle) };
            return Err("Broker could not read the original AppContainer DACL".to_owned());
        }

        let mut entry: EXPLICIT_ACCESS_W = unsafe { zeroed() };
        entry.grfAccessPermissions = rights;
        entry.grfAccessMode = GRANT_ACCESS;
        entry.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT;
        entry.Trustee = TRUSTEE_W {
            pMultipleTrustee: std::ptr::null_mut(),
            MultipleTrusteeOperation: 0,
            TrusteeForm: TRUSTEE_IS_SID,
            TrusteeType: TRUSTEE_IS_UNKNOWN,
            ptstrName: sid.cast(),
        };
        let mut granted_dacl = std::ptr::null_mut();
        let merge_status = unsafe { SetEntriesInAclW(1, &entry, original_dacl, &mut granted_dacl) };
        if merge_status != 0 || granted_dacl.is_null() {
            unsafe {
                LocalFree(original_descriptor.cast::<c_void>());
                CloseHandle(handle);
            }
            return Err("Broker could not construct the AppContainer DACL grant".to_owned());
        }
        let set_status = unsafe {
            SetSecurityInfo(
                handle,
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                granted_dacl,
                std::ptr::null_mut(),
            )
        };
        unsafe { LocalFree(granted_dacl.cast::<c_void>()) };
        if set_status != 0 {
            unsafe {
                LocalFree(original_descriptor.cast::<c_void>());
                CloseHandle(handle);
            }
            return Err("Broker could not apply the AppContainer DACL grant".to_owned());
        }
        Ok(Self {
            handle,
            original_dacl,
            original_descriptor,
            active: true,
        })
    }

    fn restore(&mut self) -> Result<(), String> {
        if !self.active {
            return Ok(());
        }
        let status = unsafe {
            SetSecurityInfo(
                self.handle,
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                self.original_dacl,
                std::ptr::null_mut(),
            )
        };
        if status != 0 {
            return Err("Broker could not restore an original AppContainer DACL".to_owned());
        }
        self.active = false;
        Ok(())
    }
}

impl Drop for AclGrant {
    fn drop(&mut self) {
        let _ = self.restore();
        unsafe {
            if !self.original_descriptor.is_null() {
                LocalFree(self.original_descriptor.cast::<c_void>());
            }
            CloseHandle(self.handle);
        }
    }
}

fn open_directory_for_acl(path: &str) -> Result<HANDLE, String> {
    let path = wide(path);
    let handle = unsafe {
        CreateFileW(
            path.as_ptr(),
            FILE_READ_ATTRIBUTES | READ_CONTROL | WRITE_DAC,
            FILE_SHARE_READ,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err("Broker could not open a policy root for AppContainer DACL control".to_owned());
    }
    // `NativePathGuard` already retained a no-reparse handle for every path
    // component. This second handle is only for WRITE_DAC and must still name
    // a directory, never an executable or a substituted special file.
    let mut information: windows_sys::Win32::Storage::FileSystem::BY_HANDLE_FILE_INFORMATION =
        unsafe { zeroed() };
    let is_directory = unsafe {
        windows_sys::Win32::Storage::FileSystem::GetFileInformationByHandle(
            handle,
            &mut information,
        ) != 0
            && information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0
    };
    if !is_directory {
        unsafe { CloseHandle(handle) };
        return Err("Broker AppContainer DACL target is not a verified directory".to_owned());
    }
    Ok(handle)
}

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

const _: () = {
    // This assertion keeps the FFI length calculation above tied to the actual
    // Windows structure when the crate is cross-compiled.
    let _ = size_of::<EXPLICIT_ACCESS_W>();
};
