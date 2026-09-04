//! Job Object lifetime and resource limits for one broker request.
//!
//! A Job Object is necessary for process-tree containment and deterministic
//! cleanup, but is never treated as filesystem or network isolation.  The
//! broker keeps this guard private until the constrained AppContainer launcher
//! can assign a suspended process before its first instruction runs.

const HUNDRED_NANOSECONDS_PER_SECOND: f64 = 10_000_000.0;

/// Win32 Job Object completion-port message values. They are intentionally
/// decoded here instead of being treated as an opaque successful poll, so a
/// future supervisor must explicitly handle every terminal/limit signal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JobNotificationKind {
    EndOfJobTime,
    EndOfProcessTime,
    ActiveProcessLimit,
    ActiveProcessZero,
    NewProcess,
    ExitProcess,
    AbnormalExitProcess,
    ProcessMemoryLimit,
    JobMemoryLimit,
    NotificationLimit,
    JobCycleTimeLimit,
    Unknown(u32),
}

fn job_notification_kind(code: u32) -> JobNotificationKind {
    match code {
        1 => JobNotificationKind::EndOfJobTime,
        2 => JobNotificationKind::EndOfProcessTime,
        3 => JobNotificationKind::ActiveProcessLimit,
        4 => JobNotificationKind::ActiveProcessZero,
        6 => JobNotificationKind::NewProcess,
        7 => JobNotificationKind::ExitProcess,
        8 => JobNotificationKind::AbnormalExitProcess,
        9 => JobNotificationKind::ProcessMemoryLimit,
        10 => JobNotificationKind::JobMemoryLimit,
        11 => JobNotificationKind::NotificationLimit,
        12 => JobNotificationKind::JobCycleTimeLimit,
        value => JobNotificationKind::Unknown(value),
    }
}

#[cfg(windows)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CompletionPoll {
    Timeout,
    Notification {
        kind: JobNotificationKind,
        process_id: usize,
    },
}

#[derive(Clone, Copy)]
pub struct JobLimits {
    pub cpu_time_seconds: f64,
    pub memory_bytes: f64,
}

impl JobLimits {
    pub fn validate(self) -> Result<(), String> {
        user_time_ticks(self.cpu_time_seconds)?;
        memory_limit_bytes(self.memory_bytes)?;
        Ok(())
    }
}

/// Convert an externally validated seconds budget to Windows 100ns ticks.
fn user_time_ticks(seconds: f64) -> Result<i64, String> {
    if !seconds.is_finite() || seconds <= 0.0 {
        return Err("Broker Job Object CPU limit is invalid".to_owned());
    }
    let ticks = seconds * HUNDRED_NANOSECONDS_PER_SECOND;
    if ticks > i64::MAX as f64 {
        return Err("Broker Job Object CPU limit exceeds the native range".to_owned());
    }
    Ok(ticks.ceil() as i64)
}

/// Convert the externally validated memory budget without a lossy wrap.
fn memory_limit_bytes(bytes: f64) -> Result<usize, String> {
    if !bytes.is_finite() || bytes <= 0.0 || bytes > usize::MAX as f64 {
        return Err("Broker Job Object memory limit is invalid".to_owned());
    }
    Ok(bytes.ceil() as usize)
}

#[cfg(windows)]
use std::ffi::c_void;

#[cfg(windows)]
use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HANDLE, INVALID_HANDLE_VALUE};
#[cfg(windows)]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectAssociateCompletionPortInformation,
    JobObjectExtendedLimitInformation, SetInformationJobObject, TerminateJobObject,
    JOBOBJECT_ASSOCIATE_COMPLETION_PORT, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_JOB_TIME,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_PROCESS_MEMORY,
};
#[cfg(windows)]
use windows_sys::Win32::System::IO::{CreateIoCompletionPort, GetQueuedCompletionStatus};

#[cfg(windows)]
const COMPLETION_KEY: usize = 1;
#[cfg(windows)]
const WAIT_TIMEOUT_ERROR: u32 = 258;

#[cfg(windows)]
pub struct SandboxJob {
    handle: HANDLE,
}

#[cfg(windows)]
impl SandboxJob {
    pub fn create(limits: JobLimits) -> Result<Self, String> {
        limits.validate()?;
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err("Broker could not create a Job Object".to_owned());
        }
        let mut information: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        information.BasicLimitInformation.PerJobUserTimeLimit =
            user_time_ticks(limits.cpu_time_seconds)?;
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_JOB_TIME
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_JOB_MEMORY;
        // Protocol v1 disallows child processes. The active-process limit is
        // deliberately stricter than a renderer-provided numeric preference.
        information.BasicLimitInformation.ActiveProcessLimit = 1;
        information.ProcessMemoryLimit = memory_limit_bytes(limits.memory_bytes)?;
        information.JobMemoryLimit = memory_limit_bytes(limits.memory_bytes)?;
        let configured = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &information as *const _ as *const c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) != 0
        };
        if !configured {
            unsafe { CloseHandle(handle) };
            return Err("Broker could not apply Job Object resource limits".to_owned());
        }
        Ok(Self { handle })
    }

    fn handle(&self) -> HANDLE {
        self.handle
    }

    fn close(&mut self) {
        if !self.handle.is_null() {
            // Closing a configured KILL_ON_JOB_CLOSE Job is the last-resort
            // cleanup path for broker errors/crashes. The caller uses it before
            // revoking AppContainer ACL grants if completion confirmation fails.
            unsafe { CloseHandle(self.handle) };
            self.handle = std::ptr::null_mut();
        }
    }

    fn assign(&self, process: HANDLE) -> Result<(), String> {
        if unsafe { AssignProcessToJobObject(self.handle, process) } == 0 {
            return Err("Broker could not assign the suspended child to its Job Object".to_owned());
        }
        Ok(())
    }

    fn terminate(&self, exit_code: u32) -> Result<(), String> {
        if unsafe { TerminateJobObject(self.handle, exit_code) } == 0 {
            return Err("Broker could not terminate the Job Object process tree".to_owned());
        }
        Ok(())
    }
}

#[cfg(windows)]
impl Drop for SandboxJob {
    fn drop(&mut self) {
        self.close();
    }
}

/// Holds a Job Object and its completion port in the required drop order.
///
/// `job` is declared before `completion_port`, so Rust closes the Job Object
/// first. A future supervisor can therefore drain the final completion packet
/// before the port itself closes. The non-blocking poll is intentionally only
/// an integrity probe until constrained launch and timeout/cancellation logic
/// use it to supervise a real child process.
#[cfg(windows)]
pub struct SupervisedJob {
    job: SandboxJob,
    completion_port: CompletionPort,
}

#[cfg(windows)]
impl SupervisedJob {
    pub fn create(limits: JobLimits) -> Result<Self, String> {
        let job = SandboxJob::create(limits)?;
        let completion_port = CompletionPort::attach(job.handle())?;
        Ok(Self {
            job,
            completion_port,
        })
    }

    pub fn is_attached(&self) -> bool {
        !self.job.handle().is_null() && !self.completion_port.handle.is_null()
    }

    pub fn poll(&self, timeout_milliseconds: u32) -> Result<CompletionPoll, String> {
        self.completion_port.poll(timeout_milliseconds)
    }

    /// Put a still-suspended process under `KILL_ON_JOB_CLOSE` before any
    /// model-authored instruction may run.
    pub fn assign_suspended_process(&self, process: HANDLE) -> Result<(), String> {
        self.job.assign(process)
    }

    /// Force every process currently associated with this request to exit.
    pub fn terminate_process_tree(&self, exit_code: u32) -> Result<(), String> {
        self.job.terminate(exit_code)
    }

    /// Close the Job Object before revoking AppContainer filesystem grants if
    /// the completion port could not prove that its tree has drained.
    pub fn close_for_unverified_cleanup(&mut self) {
        self.job.close();
    }
}

#[cfg(windows)]
struct CompletionPort {
    handle: HANDLE,
}

#[cfg(windows)]
impl CompletionPort {
    fn attach(job: HANDLE) -> Result<Self, String> {
        let handle =
            unsafe { CreateIoCompletionPort(INVALID_HANDLE_VALUE, std::ptr::null_mut(), 0, 1) };
        if handle.is_null() {
            return Err("Broker could not create a Job Object completion port".to_owned());
        }
        let association = JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
            CompletionKey: COMPLETION_KEY as *mut c_void,
            CompletionPort: handle,
        };
        let associated = unsafe {
            SetInformationJobObject(
                job,
                JobObjectAssociateCompletionPortInformation,
                &association as *const _ as *const c_void,
                std::mem::size_of::<JOBOBJECT_ASSOCIATE_COMPLETION_PORT>() as u32,
            ) != 0
        };
        if !associated {
            unsafe { CloseHandle(handle) };
            return Err("Broker could not associate the Job Object completion port".to_owned());
        }
        Ok(Self { handle })
    }

    fn poll(&self, timeout_milliseconds: u32) -> Result<CompletionPoll, String> {
        let mut message = 0u32;
        let mut completion_key = 0usize;
        let mut overlapped = std::ptr::null_mut();
        let completed = unsafe {
            GetQueuedCompletionStatus(
                self.handle,
                &mut message,
                &mut completion_key,
                &mut overlapped,
                timeout_milliseconds,
            )
        };
        if completed == 0 {
            let error = unsafe { GetLastError() };
            if error == WAIT_TIMEOUT_ERROR {
                return Ok(CompletionPoll::Timeout);
            }
            return Err(format!(
                "Broker completion-port poll failed with Win32 error {error}"
            ));
        }
        if completion_key != COMPLETION_KEY {
            return Err("Broker completion-port returned an unexpected completion key".to_owned());
        }
        Ok(CompletionPoll::Notification {
            kind: job_notification_kind(message),
            // Job Object notifications encode the affected process ID in the
            // completion's OVERLAPPED value rather than a file-I/O pointer.
            process_id: overlapped as usize,
        })
    }
}

#[cfg(windows)]
impl Drop for CompletionPort {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.handle) };
    }
}

#[cfg(test)]
mod tests {
    use super::{
        job_notification_kind, memory_limit_bytes, user_time_ticks, JobLimits, JobNotificationKind,
    };

    #[test]
    fn converts_finite_job_budgets_without_rounding_down() {
        assert_eq!(user_time_ticks(0.000_000_01).unwrap(), 1);
        assert_eq!(memory_limit_bytes(1.01).unwrap(), 2);
        assert!(JobLimits {
            cpu_time_seconds: 1.0,
            memory_bytes: 4096.0,
        }
        .validate()
        .is_ok());
    }

    #[test]
    fn rejects_nonfinite_or_overflowing_job_budgets() {
        assert!(user_time_ticks(f64::NAN).is_err());
        assert!(user_time_ticks(f64::INFINITY).is_err());
        assert!(memory_limit_bytes(0.0).is_err());
        assert!(memory_limit_bytes(f64::INFINITY).is_err());
    }

    #[test]
    fn maps_job_completion_messages_without_silently_ignoring_unknown_values() {
        assert_eq!(
            job_notification_kind(4),
            JobNotificationKind::ActiveProcessZero
        );
        assert_eq!(
            job_notification_kind(9),
            JobNotificationKind::ProcessMemoryLimit
        );
        assert_eq!(job_notification_kind(99), JobNotificationKind::Unknown(99));
    }
}
