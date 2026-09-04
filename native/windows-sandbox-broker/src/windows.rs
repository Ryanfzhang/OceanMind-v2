use std::ffi::c_void;
use std::io::{self, Read};
use std::mem::{size_of, zeroed};
use std::process;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use serde::{Deserialize, Serialize};
use windows_sys::Win32::Foundation::SetHandleInformation;
use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_BROKEN_PIPE, HANDLE, HANDLE_FLAG_INHERIT, WAIT_FAILED,
    WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeleteAppContainerProfile,
};
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::Security::{FreeSid, PSID, SECURITY_CAPABILITIES};
use windows_sys::Win32::Storage::FileSystem::ReadFile;
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess,
    InitializeProcThreadAttributeList, ResumeThread, UpdateProcThreadAttribute,
    WaitForSingleObject, CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT,
    EXTENDED_STARTUPINFO_PRESENT, LPPROC_THREAD_ATTRIBUTE_LIST, PROCESS_INFORMATION,
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
    STARTF_USESTDHANDLES, STARTUPINFOEXW,
};

use crate::acl_guard::AppContainerAclGrants;
use crate::job_guard::{CompletionPoll, JobLimits, SandboxJob, SupervisedJob};
use crate::path_guard::{validate_native_path_contract, NativePathGuard};
use crate::protocol::{validate_environment_keys, validate_path_contract};
use crate::startup::{build_windows_command_line, build_windows_environment_block};

const PROTOCOL_VERSION: &str = "ocean-windows-sandbox-broker/v1";
const RESPONSE_VERSION: &str = "ocean-windows-sandbox-broker-result/v1";
const SELF_CHECK_VERSION: &str = "ocean-windows-sandbox-broker-self-check/v1";
const MAX_STDIN_BYTES: usize = 512 * 1024;
const MAX_WALL_TIME_SECONDS: f64 = 86_400.0;
const MAX_TERMINATION_GRACE_SECONDS: f64 = 30.0;
const MAX_MEMORY_BYTES: f64 = 64.0 * 1024.0 * 1024.0 * 1024.0;
const MAX_CAPTURE_BYTES: f64 = 16.0 * 1024.0 * 1024.0;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BrokerRequest {
    schema_version: String,
    executable: String,
    arguments: Vec<String>,
    cwd: String,
    roots: BrokerRoots,
    output_root: String,
    temporary_root: String,
    environment: std::collections::BTreeMap<String, String>,
    limits: BrokerLimits,
    allow_child_processes: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BrokerRoots {
    read_only: Vec<String>,
    runtime_read: Vec<String>,
    writable: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BrokerLimits {
    wall_time_seconds: f64,
    cpu_time_seconds: f64,
    memory_bytes: f64,
    disk_bytes: f64,
    process_count: f64,
    open_files: f64,
    stdout_bytes: f64,
    stderr_bytes: f64,
    output_file_count: f64,
    output_total_bytes: f64,
    termination_grace_seconds: f64,
}

#[derive(Serialize)]
struct BrokerResult<'a> {
    schema_version: &'a str,
    status: &'a str,
    returncode: Option<i32>,
    stdout_base64: String,
    stderr_base64: String,
    duration_seconds: f64,
    limit_trigger: Option<&'a str>,
    job_terminated: bool,
}

#[derive(Serialize)]
struct SelfCheck {
    schema_version: &'static str,
    protocol_version: &'static str,
    passed: bool,
    checks: SelfCheckFlags,
}

#[derive(Serialize)]
struct SelfCheckFlags {
    app_container: bool,
    declared_output_written: bool,
    outside_read_denied: bool,
    network_denied: bool,
    job_kill_on_close: bool,
}

pub fn run() {
    let command = std::env::args().nth(1).unwrap_or_default();
    match command.as_str() {
        "doctor" => emit_doctor(),
        "self-check" => emit_self_check(),
        "run" if std::env::args().nth(2).as_deref() == Some("--stdio") => run_request(),
        _ => {
            eprintln!("usage: ocean-sandbox-broker <doctor|self-check|run --stdio>");
            process::exit(2);
        }
    }
}

fn emit_doctor() {
    let (app_container, job_kill_on_close) = probe_primitives();
    let report = serde_json::json!({
        "schema_version": "ocean-windows-sandbox-broker-doctor/v1",
        "protocol_version": PROTOCOL_VERSION,
        "available": false,
        "reason": "AppContainer ACL, handle-canonicalization, and adversarial validation are required before execution can be enabled",
        "probe": {"app_container": app_container, "job_kill_on_close": job_kill_on_close}
    });
    println!(
        "{}",
        serde_json::to_string(&report).expect("doctor JSON is serializable")
    );
}

fn emit_self_check() {
    let (app_container, job_kill_on_close) = probe_primitives();
    let result = SelfCheck {
        schema_version: SELF_CHECK_VERSION,
        protocol_version: PROTOCOL_VERSION,
        passed: false,
        checks: SelfCheckFlags {
            app_container,
            declared_output_written: false,
            outside_read_denied: false,
            network_denied: false,
            job_kill_on_close,
        },
    };
    println!(
        "{}",
        serde_json::to_string(&result).expect("self-check JSON is serializable")
    );
}

fn run_request() {
    let mut payload = Vec::new();
    if io::stdin()
        .take((MAX_STDIN_BYTES + 1) as u64)
        .read_to_end(&mut payload)
        .is_err()
        || payload.len() > MAX_STDIN_BYTES
    {
        emit_rejected(
            "broker_request_size",
            "Broker request is absent or exceeds the bounded input size",
        );
        return;
    }
    let request: BrokerRequest = match serde_json::from_slice(&payload) {
        Ok(value) => value,
        Err(_) => {
            emit_rejected(
                "broker_request_schema",
                "Broker request does not match the strict v1 schema",
            );
            return;
        }
    };
    if let Err(reason) = validate_request(&request) {
        emit_rejected("broker_request_policy", &reason);
        return;
    }
    let path_guard = match validate_native_path_contract(
        &request.executable,
        &request.cwd,
        &request.output_root,
        &request.temporary_root,
        &request.roots.read_only,
        &request.roots.runtime_read,
        &request.roots.writable,
    ) {
        Ok(guard) => guard,
        Err(reason) => {
            emit_rejected("broker_path_validation", &reason);
            return;
        }
    };
    let app_container = match AppContainerProfile::create_attempt() {
        Ok(profile) => profile,
        Err(reason) => {
            emit_rejected("broker_appcontainer_profile", &reason);
            return;
        }
    };
    let mut job = match SupervisedJob::create(JobLimits {
        cpu_time_seconds: request.limits.cpu_time_seconds,
        memory_bytes: request.limits.memory_bytes,
    }) {
        Ok(job) => {
            debug_assert!(job.is_attached());
            job
        }
        Err(reason) => {
            emit_rejected("broker_job_configuration", &reason);
            return;
        }
    };
    match job.poll(0) {
        Ok(CompletionPoll::Timeout) => {}
        Ok(notification) => {
            emit_rejected(
                "broker_completion_port",
                &format!("Broker completion port reported an unexpected pre-launch event: {notification:?}"),
            );
            return;
        }
        Err(reason) => {
            emit_rejected("broker_completion_port", &reason);
            return;
        }
    }
    let mut acl_grants = match AppContainerAclGrants::apply(&path_guard, app_container.sid()) {
        Ok(grants) => grants,
        Err(reason) => {
            emit_rejected("broker_appcontainer_acl", &reason);
            return;
        }
    };
    let mut result = match run_sandboxed_child(&request, &path_guard, &app_container, &job) {
        Ok(result) => result,
        Err(reason) => BrokerExecutionResult::failed("broker_launch", reason.as_bytes(), true),
    };
    if !result.job_terminated {
        job.close_for_unverified_cleanup();
    }
    if let Err(reason) = acl_grants.restore_all() {
        result = BrokerExecutionResult::failed(
            "broker_acl_restore",
            reason.as_bytes(),
            result.job_terminated,
        );
    }
    emit_execution_result(result);
}

struct BrokerExecutionResult {
    status: &'static str,
    returncode: Option<i32>,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    duration_seconds: f64,
    limit_trigger: Option<String>,
    job_terminated: bool,
}

impl BrokerExecutionResult {
    fn failed(trigger: &str, stderr: &[u8], job_terminated: bool) -> Self {
        Self {
            status: "failed",
            returncode: None,
            stdout: Vec::new(),
            stderr: stderr.to_vec(),
            duration_seconds: 0.0,
            limit_trigger: Some(trigger.to_owned()),
            job_terminated,
        }
    }
}

/// Launch one constrained AppContainer process and wait until the Job Object
/// proves that the associated process tree has drained.
///
/// `CreateProcessW` is deliberately called with the exact validated executable
/// as `lpApplicationName`, not a shell or a PATH lookup.  Its primary thread is
/// created suspended, assigned to the already configured Job Object, and only
/// then resumed.  Any error after creation terminates the Job before a result
/// is emitted.
fn run_sandboxed_child(
    request: &BrokerRequest,
    path_guard: &NativePathGuard,
    profile: &AppContainerProfile,
    job: &SupervisedJob,
) -> Result<BrokerExecutionResult, String> {
    let mut command_line = build_windows_command_line(&path_guard.executable, &request.arguments)?;
    let environment = build_windows_environment_block(&request.environment)?;
    let executable = wide(&path_guard.executable);
    let cwd = wide(&path_guard.cwd);
    let mut pipes = StandardPipes::create()?;
    let inherited_handles = pipes.child_handles();
    let startup = AppContainerStartup::new(profile, &inherited_handles, &pipes)?;
    debug_assert!(startup.is_configured());
    let mut process_information: PROCESS_INFORMATION = unsafe { zeroed() };
    let created = unsafe {
        CreateProcessW(
            executable.as_ptr(),
            command_line.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1,
            CREATE_SUSPENDED
                | CREATE_UNICODE_ENVIRONMENT
                | CREATE_NO_WINDOW
                | EXTENDED_STARTUPINFO_PRESENT,
            environment.as_ptr().cast::<c_void>(),
            cwd.as_ptr(),
            &startup.startup_info.StartupInfo,
            &mut process_information,
        )
    };
    if created == 0 {
        return Err("Broker could not create the suspended AppContainer process".to_owned());
    }
    let child = ChildProcess::new(process_information)?;
    if let Err(reason) = job.assign_suspended_process(child.process) {
        let terminated = child.terminate_directly_and_wait(Duration::from_secs(2));
        return Ok(BrokerExecutionResult::failed(
            "broker_job_assignment",
            reason.as_bytes(),
            terminated,
        ));
    }

    let readers = pipes.start_output_readers(
        bounded_capture_limit(request.limits.stdout_bytes)?,
        bounded_capture_limit(request.limits.stderr_bytes)?,
    );
    pipes.close_child_handles();
    if unsafe { ResumeThread(child.thread) } == u32::MAX {
        let terminated = terminate_and_wait(job, &child, request.limits.termination_grace_seconds);
        let (stdout, stderr) = readers.join();
        return Ok(BrokerExecutionResult {
            status: "failed",
            returncode: None,
            stdout: stdout.data,
            stderr: join_error_message(
                stderr.data,
                b"Broker could not resume the suspended AppContainer process",
            ),
            duration_seconds: 0.0,
            limit_trigger: Some("broker_resume".to_owned()),
            job_terminated: terminated,
        });
    }

    let started = Instant::now();
    let supervised = supervise_child(
        job,
        &child,
        &readers,
        request.limits.wall_time_seconds,
        request.limits.termination_grace_seconds,
    );
    let (stdout, stderr) = readers.join();
    let mut result = supervised;
    result.duration_seconds = started.elapsed().as_secs_f64();
    result.stdout = stdout.data;
    result.stderr = stderr.data;
    if stdout.read_error || stderr.read_error {
        result.status = "failed";
        result.returncode = None;
        result.limit_trigger = Some("broker_output_capture".to_owned());
        result.stderr = join_error_message(
            result.stderr,
            b"Broker could not capture complete child output",
        );
    } else if result.status == "succeeded" && (stdout.exceeded || stderr.exceeded) {
        result.status = "resource_limited";
        result.returncode = None;
        result.limit_trigger = Some(
            if stdout.exceeded {
                "stdout_bytes"
            } else {
                "stderr_bytes"
            }
            .to_owned(),
        );
    }
    Ok(result)
}

fn supervise_child(
    job: &SupervisedJob,
    child: &ChildProcess,
    readers: &OutputReaders,
    wall_time_seconds: f64,
    termination_grace_seconds: f64,
) -> BrokerExecutionResult {
    let started = Instant::now();
    let deadline = Duration::from_secs_f64(wall_time_seconds);
    let grace = Duration::from_secs_f64(termination_grace_seconds);
    let mut saw_active_process_zero = false;
    let mut terminal = None;

    loop {
        match job.poll(0) {
            Ok(CompletionPoll::Timeout) => {}
            Ok(CompletionPoll::Notification { kind, .. }) => {
                if matches!(
                    kind,
                    crate::job_guard::JobNotificationKind::ActiveProcessZero
                ) {
                    saw_active_process_zero = true;
                } else if let Some(trigger) = completion_limit_trigger(kind) {
                    terminal = Some(("resource_limited", trigger));
                } else if !matches!(
                    kind,
                    crate::job_guard::JobNotificationKind::ExitProcess
                        | crate::job_guard::JobNotificationKind::AbnormalExitProcess
                ) {
                    terminal = Some(("failed", "broker_completion_port"));
                }
            }
            Err(_) => terminal = Some(("failed", "broker_completion_port")),
        }
        if terminal.is_none() {
            match readers.limit_trigger.load(Ordering::SeqCst) {
                1 => terminal = Some(("resource_limited", "stdout_bytes")),
                2 => terminal = Some(("resource_limited", "stderr_bytes")),
                _ => {}
            }
        }
        if terminal.is_none() && started.elapsed() >= deadline {
            terminal = Some(("timed_out", "wall_time_seconds"));
        }
        if let Some((status, trigger)) = terminal {
            let job_terminated =
                terminate_and_wait_with_state(job, child, grace, &mut saw_active_process_zero);
            return BrokerExecutionResult {
                status,
                returncode: None,
                stdout: Vec::new(),
                stderr: Vec::new(),
                duration_seconds: 0.0,
                limit_trigger: Some(trigger.to_owned()),
                job_terminated,
            };
        }

        let wait = unsafe { WaitForSingleObject(child.process, 25) };
        if wait == WAIT_OBJECT_0 {
            let returncode = child.exit_code().ok();
            let job_terminated =
                wait_for_tree_termination(job, grace, &mut saw_active_process_zero);
            let (status, trigger) = match returncode {
                Some(0) => ("succeeded", None),
                Some(_) => ("failed", None),
                None => ("failed", Some("broker_exit_status")),
            };
            return BrokerExecutionResult {
                status,
                returncode,
                stdout: Vec::new(),
                stderr: Vec::new(),
                duration_seconds: 0.0,
                limit_trigger: trigger.map(str::to_owned),
                job_terminated,
            };
        }
        if wait == WAIT_FAILED {
            let job_terminated =
                terminate_and_wait_with_state(job, child, grace, &mut saw_active_process_zero);
            return BrokerExecutionResult {
                status: "failed",
                returncode: None,
                stdout: Vec::new(),
                stderr: Vec::new(),
                duration_seconds: 0.0,
                limit_trigger: Some("broker_process_wait".to_owned()),
                job_terminated,
            };
        }
        debug_assert_eq!(wait, WAIT_TIMEOUT);
    }
}

fn completion_limit_trigger(kind: crate::job_guard::JobNotificationKind) -> Option<&'static str> {
    use crate::job_guard::JobNotificationKind;

    match kind {
        JobNotificationKind::EndOfJobTime | JobNotificationKind::EndOfProcessTime => {
            Some("cpu_time_seconds")
        }
        JobNotificationKind::ActiveProcessLimit | JobNotificationKind::NewProcess => {
            Some("process_count")
        }
        JobNotificationKind::ProcessMemoryLimit | JobNotificationKind::JobMemoryLimit => {
            Some("memory_bytes")
        }
        JobNotificationKind::NotificationLimit | JobNotificationKind::JobCycleTimeLimit => {
            Some("broker_job_limit")
        }
        JobNotificationKind::Unknown(_) => Some("broker_completion_unknown"),
        JobNotificationKind::ActiveProcessZero
        | JobNotificationKind::ExitProcess
        | JobNotificationKind::AbnormalExitProcess => None,
    }
}

fn terminate_and_wait(job: &SupervisedJob, child: &ChildProcess, grace_seconds: f64) -> bool {
    let mut saw_active_process_zero = false;
    terminate_and_wait_with_state(
        job,
        child,
        Duration::from_secs_f64(grace_seconds),
        &mut saw_active_process_zero,
    )
}

fn terminate_and_wait_with_state(
    job: &SupervisedJob,
    child: &ChildProcess,
    grace: Duration,
    saw_active_process_zero: &mut bool,
) -> bool {
    if job.terminate_process_tree(1).is_err() {
        let _ = child.terminate_directly_and_wait(grace);
        return false;
    }
    let wait_milliseconds = duration_to_milliseconds(grace);
    let _ = unsafe { WaitForSingleObject(child.process, wait_milliseconds) };
    wait_for_tree_termination(job, grace, saw_active_process_zero)
}

fn wait_for_tree_termination(
    job: &SupervisedJob,
    grace: Duration,
    saw_active_process_zero: &mut bool,
) -> bool {
    if *saw_active_process_zero {
        return true;
    }
    let deadline = Instant::now() + grace;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return false;
        }
        match job.poll(duration_to_milliseconds(
            remaining.min(Duration::from_millis(50)),
        )) {
            Ok(CompletionPoll::Notification { kind, .. })
                if matches!(
                    kind,
                    crate::job_guard::JobNotificationKind::ActiveProcessZero
                ) =>
            {
                *saw_active_process_zero = true;
                return true;
            }
            Ok(CompletionPoll::Timeout) | Ok(CompletionPoll::Notification { .. }) => continue,
            Err(_) => return false,
        }
    }
}

fn duration_to_milliseconds(duration: Duration) -> u32 {
    duration.as_millis().clamp(1, u128::from(u32::MAX)) as u32
}

struct ChildProcess {
    process: HANDLE,
    thread: HANDLE,
}

impl ChildProcess {
    fn new(information: PROCESS_INFORMATION) -> Result<Self, String> {
        if information.hProcess.is_null() || information.hThread.is_null() {
            if !information.hProcess.is_null() {
                unsafe { CloseHandle(information.hProcess) };
            }
            if !information.hThread.is_null() {
                unsafe { CloseHandle(information.hThread) };
            }
            return Err(
                "Broker received incomplete process handles from CreateProcessW".to_owned(),
            );
        }
        Ok(Self {
            process: information.hProcess,
            thread: information.hThread,
        })
    }

    fn terminate_directly_and_wait(&self, grace: Duration) -> bool {
        // This path is used only before Job assignment has succeeded. Once the
        // child is in the Job, all termination goes through that Job Object.
        unsafe {
            windows_sys::Win32::System::Threading::TerminateProcess(self.process, 1) != 0
                && WaitForSingleObject(self.process, duration_to_milliseconds(grace))
                    == WAIT_OBJECT_0
        }
    }

    fn exit_code(&self) -> Result<i32, String> {
        let mut code = 0u32;
        if unsafe { GetExitCodeProcess(self.process, &mut code) } == 0 {
            return Err("Broker could not inspect the AppContainer process exit code".to_owned());
        }
        Ok(code as i32)
    }
}

impl Drop for ChildProcess {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.thread);
            CloseHandle(self.process);
        }
    }
}

struct OwnedHandle(HANDLE);

impl OwnedHandle {
    fn new(handle: HANDLE, message: &str) -> Result<Self, String> {
        if handle.is_null() {
            return Err(message.to_owned());
        }
        Ok(Self(handle))
    }

    fn raw(&self) -> HANDLE {
        self.0
    }

    fn close(&mut self) {
        if !self.0.is_null() {
            unsafe { CloseHandle(self.0) };
            self.0 = std::ptr::null_mut();
        }
    }

    fn take_for_reader(&mut self) -> usize {
        let handle = self.0 as usize;
        self.0 = std::ptr::null_mut();
        handle
    }
}

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        self.close();
    }
}

struct StandardPipes {
    child_stdin: OwnedHandle,
    child_stdout: OwnedHandle,
    child_stderr: OwnedHandle,
    parent_stdout: OwnedHandle,
    parent_stderr: OwnedHandle,
}

impl StandardPipes {
    fn create() -> Result<Self, String> {
        let (child_stdin, mut unused_stdin_writer) = create_inheritable_pipe()?;
        unused_stdin_writer.close();
        let (parent_stdout, child_stdout) = create_inheritable_pipe()?;
        let (parent_stderr, child_stderr) = create_inheritable_pipe()?;
        set_non_inheritable(parent_stdout.raw())?;
        set_non_inheritable(parent_stderr.raw())?;
        Ok(Self {
            child_stdin,
            child_stdout,
            child_stderr,
            parent_stdout,
            parent_stderr,
        })
    }

    fn child_handles(&self) -> [HANDLE; 3] {
        [
            self.child_stdin.raw(),
            self.child_stdout.raw(),
            self.child_stderr.raw(),
        ]
    }

    fn start_output_readers(&mut self, stdout_limit: usize, stderr_limit: usize) -> OutputReaders {
        OutputReaders::start(
            self.parent_stdout.take_for_reader(),
            stdout_limit,
            self.parent_stderr.take_for_reader(),
            stderr_limit,
        )
    }

    fn close_child_handles(&mut self) {
        self.child_stdin.close();
        self.child_stdout.close();
        self.child_stderr.close();
    }
}

fn create_inheritable_pipe() -> Result<(OwnedHandle, OwnedHandle), String> {
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: std::ptr::null_mut(),
        bInheritHandle: 1,
    };
    let mut read = std::ptr::null_mut();
    let mut write = std::ptr::null_mut();
    if unsafe { CreatePipe(&mut read, &mut write, &attributes, 0) } == 0 {
        return Err("Broker could not create a bounded standard-stream pipe".to_owned());
    }
    let read = OwnedHandle::new(
        read,
        "Broker received an invalid standard-stream read handle",
    )?;
    let write = OwnedHandle::new(
        write,
        "Broker received an invalid standard-stream write handle",
    )?;
    Ok((read, write))
}

fn set_non_inheritable(handle: HANDLE) -> Result<(), String> {
    if unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0) } == 0 {
        return Err("Broker could not remove inheritance from its pipe reader".to_owned());
    }
    Ok(())
}

struct OutputReaders {
    stdout: JoinHandle<StreamCapture>,
    stderr: JoinHandle<StreamCapture>,
    limit_trigger: Arc<AtomicU8>,
}

impl OutputReaders {
    fn start(
        stdout_handle: usize,
        stdout_limit: usize,
        stderr_handle: usize,
        stderr_limit: usize,
    ) -> Self {
        let limit_trigger = Arc::new(AtomicU8::new(0));
        let stdout_signal = Arc::clone(&limit_trigger);
        let stdout =
            thread::spawn(move || read_stream(stdout_handle, stdout_limit, 1, stdout_signal));
        let stderr_signal = Arc::clone(&limit_trigger);
        let stderr =
            thread::spawn(move || read_stream(stderr_handle, stderr_limit, 2, stderr_signal));
        Self {
            stdout,
            stderr,
            limit_trigger,
        }
    }

    fn join(self) -> (StreamCapture, StreamCapture) {
        let stdout = self
            .stdout
            .join()
            .unwrap_or_else(|_| StreamCapture::read_error());
        let stderr = self
            .stderr
            .join()
            .unwrap_or_else(|_| StreamCapture::read_error());
        (stdout, stderr)
    }
}

struct StreamCapture {
    data: Vec<u8>,
    exceeded: bool,
    read_error: bool,
}

impl StreamCapture {
    fn read_error() -> Self {
        Self {
            data: Vec::new(),
            exceeded: false,
            read_error: true,
        }
    }
}

fn read_stream(
    handle: usize,
    limit: usize,
    signal_value: u8,
    limit_trigger: Arc<AtomicU8>,
) -> StreamCapture {
    let handle = handle as HANDLE;
    let mut capture = StreamCapture {
        data: Vec::with_capacity(limit.min(16 * 1024)),
        exceeded: false,
        read_error: false,
    };
    let mut buffer = [0u8; 4096];
    loop {
        let mut read = 0u32;
        let read_ok = unsafe {
            ReadFile(
                handle,
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                &mut read,
                std::ptr::null_mut(),
            ) != 0
        };
        if !read_ok {
            if unsafe { GetLastError() } != ERROR_BROKEN_PIPE {
                capture.read_error = true;
            }
            break;
        }
        if read == 0 {
            break;
        }
        let bytes = &buffer[..read as usize];
        let remaining = limit.saturating_sub(capture.data.len());
        let retained = remaining.min(bytes.len());
        capture.data.extend_from_slice(&bytes[..retained]);
        if retained != bytes.len() {
            capture.exceeded = true;
            let _ =
                limit_trigger.compare_exchange(0, signal_value, Ordering::SeqCst, Ordering::SeqCst);
        }
    }
    unsafe { CloseHandle(handle) };
    capture
}

fn join_error_message(mut existing: Vec<u8>, message: &[u8]) -> Vec<u8> {
    if !existing.is_empty() {
        existing.push(b'\n');
    }
    existing.extend_from_slice(message);
    existing
}

fn validate_request(request: &BrokerRequest) -> Result<(), String> {
    if request.schema_version != PROTOCOL_VERSION || request.allow_child_processes {
        return Err("Broker protocol version or child-process policy is invalid".to_owned());
    }
    if request.arguments.len() > 127
        || request.environment.len() > 64
        || request.roots.read_only.is_empty()
        || request.roots.runtime_read.is_empty()
        || request.roots.writable.is_empty()
        || request.roots.read_only.len() > 64
        || request.roots.runtime_read.len() > 64
        || request.roots.writable.len() > 64
    {
        return Err("Broker request exceeds a bounded collection limit".to_owned());
    }
    let paths = std::iter::once(&request.executable)
        .chain(std::iter::once(&request.cwd))
        .chain(std::iter::once(&request.output_root))
        .chain(std::iter::once(&request.temporary_root))
        .chain(request.roots.read_only.iter())
        .chain(request.roots.runtime_read.iter())
        .chain(request.roots.writable.iter());
    if paths.clone().any(|path| !is_absolute_windows_path(path)) {
        return Err("Broker request contains a non-absolute or device path".to_owned());
    }
    validate_path_contract(
        &request.executable,
        &request.cwd,
        &request.output_root,
        &request.temporary_root,
        &request.roots.read_only,
        &request.roots.runtime_read,
        &request.roots.writable,
    )?;
    if request
        .arguments
        .iter()
        .any(|value| !is_bounded_string(value))
        || request.environment.iter().any(|(key, value)| {
            key.is_empty()
                || key.contains('=')
                || !is_bounded_string(key)
                || !is_bounded_string(value)
        })
        || !validate_environment_keys(&request.environment)
    {
        return Err("Broker request contains invalid argv or environment text".to_owned());
    }
    let limits = [
        request.limits.wall_time_seconds,
        request.limits.cpu_time_seconds,
        request.limits.memory_bytes,
        request.limits.disk_bytes,
        request.limits.process_count,
        request.limits.open_files,
        request.limits.stdout_bytes,
        request.limits.stderr_bytes,
        request.limits.output_file_count,
        request.limits.output_total_bytes,
        request.limits.termination_grace_seconds,
    ];
    if limits
        .iter()
        .any(|value| !value.is_finite() || *value <= 0.0)
    {
        return Err("Broker request contains invalid resource limits".to_owned());
    }
    if request.limits.wall_time_seconds > MAX_WALL_TIME_SECONDS
        || request.limits.cpu_time_seconds > MAX_WALL_TIME_SECONDS
        || request.limits.termination_grace_seconds > MAX_TERMINATION_GRACE_SECONDS
        || request.limits.memory_bytes > MAX_MEMORY_BYTES
        || request.limits.stdout_bytes > MAX_CAPTURE_BYTES
        || request.limits.stderr_bytes > MAX_CAPTURE_BYTES
    {
        return Err("Broker request exceeds a hard resource-budget ceiling".to_owned());
    }
    Ok(())
}

fn bounded_capture_limit(value: f64) -> Result<usize, String> {
    if !value.is_finite() || value <= 0.0 || value > MAX_CAPTURE_BYTES {
        return Err("Broker standard-stream budget is invalid".to_owned());
    }
    Ok(value as usize)
}

fn is_bounded_string(value: &str) -> bool {
    !value.is_empty() && value.len() <= 32_768 && !value.contains('\0')
}

fn is_absolute_windows_path(value: &str) -> bool {
    let bytes = value.as_bytes();
    value.len() <= 32_768
        && !value.contains('\0')
        && !value.starts_with("\\\\?\\")
        && !value.starts_with("\\\\.\\")
        && !value.starts_with("\\\\")
        && bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && (bytes[2] == b'\\' || bytes[2] == b'/')
}

fn emit_rejected(trigger: &str, message: &str) {
    let result = BrokerExecutionResult::failed(trigger, message.as_bytes(), true);
    emit_execution_result(result);
}

fn emit_execution_result(result: BrokerExecutionResult) {
    let envelope = BrokerResult {
        schema_version: RESPONSE_VERSION,
        status: result.status,
        returncode: result.returncode,
        stdout_base64: BASE64.encode(result.stdout),
        stderr_base64: BASE64.encode(result.stderr),
        duration_seconds: result.duration_seconds,
        limit_trigger: result.limit_trigger.as_deref(),
        job_terminated: result.job_terminated,
    };
    println!(
        "{}",
        serde_json::to_string(&envelope).expect("broker result is serializable")
    );
}

fn probe_primitives() -> (bool, bool) {
    let created = AppContainerProfile::create_attempt().is_ok();
    let job_configured = SandboxJob::create(JobLimits {
        cpu_time_seconds: 1.0,
        memory_bytes: 16.0 * 1024.0 * 1024.0,
    })
    .is_ok();
    (created, job_configured)
}

struct AppContainerProfile {
    name: Vec<u16>,
    sid: PSID,
}

impl AppContainerProfile {
    fn create_attempt() -> Result<Self, String> {
        let elapsed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "Broker could not create a unique AppContainer identity".to_owned())?;
        let name = wide(&format!(
            "OceanPartnerSandbox.{}.{}",
            process::id(),
            elapsed.as_nanos()
        ));
        let display_name = wide("Ocean Partner sandbox attempt");
        let description = wide("Temporary least-privilege analysis attempt");
        let mut sid: PSID = std::ptr::null_mut();
        let status = unsafe {
            CreateAppContainerProfile(
                name.as_ptr(),
                display_name.as_ptr(),
                description.as_ptr(),
                std::ptr::null(),
                0,
                &mut sid,
            )
        };
        if status != 0 || sid.is_null() {
            if !sid.is_null() {
                unsafe { FreeSid(sid) };
            }
            return Err("Broker could not create an AppContainer identity".to_owned());
        }
        Ok(Self { name, sid })
    }

    fn sid(&self) -> PSID {
        self.sid
    }
}

impl Drop for AppContainerProfile {
    fn drop(&mut self) {
        // `run_request` retains the profile until the child Job has drained, or
        // has been closed for KILL_ON_JOB_CLOSE cleanup and DACL restoration.
        unsafe { DeleteAppContainerProfile(self.name.as_ptr()) };
        unsafe { FreeSid(self.sid) };
    }
}

struct AppContainerStartup {
    attribute_storage: Vec<usize>,
    attribute_list: LPPROC_THREAD_ATTRIBUTE_LIST,
    capabilities: Box<SECURITY_CAPABILITIES>,
    inherited_handles: Box<[HANDLE]>,
    startup_info: STARTUPINFOEXW,
}

impl AppContainerStartup {
    fn new(
        profile: &AppContainerProfile,
        inherited_handles: &[HANDLE],
        pipes: &StandardPipes,
    ) -> Result<Self, String> {
        if inherited_handles.len() != 3
            || inherited_handles.iter().any(|handle| handle.is_null())
            || inherited_handles[0] == inherited_handles[1]
            || inherited_handles[0] == inherited_handles[2]
            || inherited_handles[1] == inherited_handles[2]
        {
            return Err("Broker standard-handle inheritance list is invalid".to_owned());
        }
        let mut bytes = 0usize;
        // The sizing call intentionally reports failure with a required size.
        unsafe {
            InitializeProcThreadAttributeList(std::ptr::null_mut(), 2, 0, &mut bytes);
        }
        if bytes == 0 {
            return Err("Broker could not size AppContainer startup attributes".to_owned());
        }
        let words = bytes
            .checked_add(size_of::<usize>() - 1)
            .and_then(|value| value.checked_div(size_of::<usize>()))
            .ok_or_else(|| {
                "Broker AppContainer startup attributes exceed the native range".to_owned()
            })?;
        let mut attribute_storage = vec![0usize; words];
        let attribute_list = attribute_storage.as_mut_ptr() as LPPROC_THREAD_ATTRIBUTE_LIST;
        if unsafe { InitializeProcThreadAttributeList(attribute_list, 2, 0, &mut bytes) } == 0 {
            return Err("Broker could not initialize AppContainer startup attributes".to_owned());
        }
        let mut capabilities = Box::new(SECURITY_CAPABILITIES {
            AppContainerSid: profile.sid(),
            Capabilities: std::ptr::null_mut(),
            CapabilityCount: 0,
            Reserved: 0,
        });
        let updated = unsafe {
            UpdateProcThreadAttribute(
                attribute_list,
                0,
                PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES as usize,
                capabilities.as_mut() as *mut _ as *const c_void,
                size_of::<SECURITY_CAPABILITIES>(),
                std::ptr::null_mut(),
                std::ptr::null(),
            ) != 0
        };
        if !updated {
            unsafe { DeleteProcThreadAttributeList(attribute_list) };
            return Err("Broker could not attach the AppContainer security capability".to_owned());
        }
        let inherited_handles = inherited_handles.to_vec().into_boxed_slice();
        let handles_updated = unsafe {
            UpdateProcThreadAttribute(
                attribute_list,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                inherited_handles.as_ptr() as *const c_void,
                size_of::<HANDLE>() * inherited_handles.len(),
                std::ptr::null_mut(),
                std::ptr::null(),
            ) != 0
        };
        if !handles_updated {
            unsafe { DeleteProcThreadAttributeList(attribute_list) };
            return Err(
                "Broker could not restrict inherited handles to standard streams".to_owned(),
            );
        }
        let mut startup_info: STARTUPINFOEXW = unsafe { zeroed() };
        startup_info.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup_info.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup_info.StartupInfo.hStdInput = pipes.child_stdin.raw();
        startup_info.StartupInfo.hStdOutput = pipes.child_stdout.raw();
        startup_info.StartupInfo.hStdError = pipes.child_stderr.raw();
        startup_info.lpAttributeList = attribute_list;
        Ok(Self {
            attribute_storage,
            attribute_list,
            capabilities,
            inherited_handles,
            startup_info,
        })
    }

    fn is_configured(&self) -> bool {
        !self.attribute_storage.is_empty()
            && !self.attribute_list.is_null()
            && !self.capabilities.AppContainerSid.is_null()
            && self.inherited_handles.len() == 3
            && self.startup_info.StartupInfo.cb == size_of::<STARTUPINFOEXW>() as u32
            && self.startup_info.lpAttributeList == self.attribute_list
    }
}

impl Drop for AppContainerStartup {
    fn drop(&mut self) {
        unsafe { DeleteProcThreadAttributeList(self.attribute_list) };
    }
}

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}
