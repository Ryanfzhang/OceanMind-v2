#![cfg(all(windows, feature = "native-contract-fixture"))]

use std::fs;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Command, Output};

use serde_json::{json, Value};

const PROTOCOL_VERSION: &str = "ocean-windows-sandbox-broker/v1";

#[test]
fn doctor_probes_native_primitives_but_keeps_execution_unavailable() {
    let report = broker_json(&["doctor"], None);

    assert_eq!(
        report.get("schema_version").and_then(Value::as_str),
        Some("ocean-windows-sandbox-broker-doctor/v1")
    );
    assert_eq!(
        report.get("protocol_version").and_then(Value::as_str),
        Some(PROTOCOL_VERSION)
    );
    assert_eq!(
        report.get("available").and_then(Value::as_bool),
        Some(false)
    );
    assert!(report.get("reason").and_then(Value::as_str).is_some());
    assert_eq!(
        report
            .pointer("/probe/app_container")
            .and_then(Value::as_bool),
        Some(true)
    );
    assert_eq!(
        report
            .pointer("/probe/job_kill_on_close")
            .and_then(Value::as_bool),
        Some(true)
    );
}

#[test]
fn run_launches_a_bounded_appcontainer_broker_probe_and_drains_its_job() {
    let root = unique_temp_root();
    let work = root.join("work");
    let output = root.join("output");
    let temporary = root.join("temporary");
    for directory in [&work, &output, &temporary] {
        fs::create_dir_all(directory).expect("native contract fixture directory");
    }
    let executable = broker_path();
    let runtime = executable.parent().expect("broker runtime parent");
    let request = json!({
        "schema_version": PROTOCOL_VERSION,
        "executable": executable,
        "arguments": ["doctor"],
        "cwd": work,
        "roots": {
            "read_only": [work],
            "runtime_read": [runtime],
            "writable": [output, temporary],
        },
        "output_root": output,
        "temporary_root": temporary,
        "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
        "limits": {
            "wall_time_seconds": 1.0,
            "cpu_time_seconds": 1.0,
            "memory_bytes": 1048576.0,
            "disk_bytes": 1048576.0,
            "process_count": 1.0,
            "open_files": 16.0,
            "stdout_bytes": 1024.0,
            "stderr_bytes": 1024.0,
            "output_file_count": 1.0,
            "output_total_bytes": 1024.0,
            "termination_grace_seconds": 0.1,
        },
        "allow_child_processes": false,
    });

    let result = broker_json(&["run", "--stdio"], Some(request.to_string().as_bytes()));

    assert_eq!(
        result.get("schema_version").and_then(Value::as_str),
        Some("ocean-windows-sandbox-broker-result/v1")
    );
    assert_eq!(
        result.get("status").and_then(Value::as_str),
        Some("succeeded")
    );
    assert_eq!(result.get("returncode").and_then(Value::as_i64), Some(0));
    assert!(
        result.get("limit_trigger").is_some_and(Value::is_null),
        "a successful probe must not claim a limit trigger"
    );
    assert_eq!(
        result.get("job_terminated").and_then(Value::as_bool),
        Some(true)
    );
    let stdout = result
        .get("stdout_base64")
        .and_then(Value::as_str)
        .expect("broker result stdout");
    let stdout = base64::Engine::decode(&base64::engine::general_purpose::STANDARD, stdout)
        .expect("broker result stdout base64");
    let doctor: Value = serde_json::from_slice(&stdout).expect("inner broker doctor JSON");
    assert_eq!(
        doctor.get("schema_version").and_then(Value::as_str),
        Some("ocean-windows-sandbox-broker-doctor/v1")
    );
    assert_eq!(
        doctor.get("available").and_then(Value::as_bool),
        Some(false),
        "a native launch probe must not enable AnalysisRun before adversarial validation"
    );
    assert!(fs::read_dir(&output)
        .expect("empty output directory remains readable")
        .next()
        .is_none());
    fs::remove_dir_all(root).expect("native contract fixture cleanup");
}

#[test]
fn run_allows_a_fixture_to_write_only_to_the_declared_output_root() {
    let root = unique_temp_root();
    let fixture = fixture_paths(&root);
    let output_file = fixture.output.join("declared-output.txt");
    let request = fixture_request(&fixture, &["write", output_file.to_string_lossy().as_ref()]);

    let result = broker_json(&["run", "--stdio"], Some(request.to_string().as_bytes()));

    assert_eq!(
        result.get("status").and_then(Value::as_str),
        Some("succeeded")
    );
    assert_eq!(result.get("returncode").and_then(Value::as_i64), Some(0));
    assert_eq!(
        result.get("job_terminated").and_then(Value::as_bool),
        Some(true)
    );
    assert_eq!(
        fs::read(&output_file).expect("declared output"),
        b"fixture output\n"
    );
    fs::remove_dir_all(root).expect("native contract fixture cleanup");
}

#[test]
fn run_denies_a_fixture_read_outside_its_declared_roots() {
    let root = unique_temp_root();
    let fixture = fixture_paths(&root);
    let outside = root.join("outside").join("secret.txt");
    fs::create_dir_all(outside.parent().expect("outside parent")).expect("outside fixture parent");
    fs::write(&outside, b"not granted to appcontainer").expect("outside fixture secret");
    let request = fixture_request(&fixture, &["read", outside.to_string_lossy().as_ref()]);

    let result = broker_json(&["run", "--stdio"], Some(request.to_string().as_bytes()));

    assert_eq!(
        result.get("status").and_then(Value::as_str),
        Some("succeeded")
    );
    assert_eq!(result.get("returncode").and_then(Value::as_i64), Some(41));
    let stderr = decode_result_stream(&result, "stderr_base64");
    assert!(String::from_utf8_lossy(&stderr).contains("read failed"));
    assert_eq!(
        result.get("job_terminated").and_then(Value::as_bool),
        Some(true)
    );
    fs::remove_dir_all(root).expect("native contract fixture cleanup");
}

#[test]
fn run_denies_a_fixture_network_connection_without_an_appcontainer_capability() {
    let root = unique_temp_root();
    let fixture = fixture_paths(&root);
    let listener = TcpListener::bind("127.0.0.1:0").expect("loopback listener");
    let address = listener
        .local_addr()
        .expect("loopback listener address")
        .to_string();
    let request = fixture_request(&fixture, &["connect", &address]);

    let result = broker_json(&["run", "--stdio"], Some(request.to_string().as_bytes()));

    assert_eq!(
        result.get("status").and_then(Value::as_str),
        Some("succeeded")
    );
    assert_eq!(result.get("returncode").and_then(Value::as_i64), Some(41));
    let stderr = decode_result_stream(&result, "stderr_base64");
    assert!(String::from_utf8_lossy(&stderr).contains("connect failed"));
    assert_eq!(
        result.get("job_terminated").and_then(Value::as_bool),
        Some(true)
    );
    drop(listener);
    fs::remove_dir_all(root).expect("native contract fixture cleanup");
}

#[test]
fn run_terminates_the_job_when_stdout_exceeds_its_capture_budget() {
    let root = unique_temp_root();
    let fixture = fixture_paths(&root);
    let mut request = fixture_request(&fixture, &["stdout", "4096"]);
    request["limits"]["stdout_bytes"] = json!(64.0);

    let result = broker_json(&["run", "--stdio"], Some(request.to_string().as_bytes()));

    assert_eq!(
        result.get("status").and_then(Value::as_str),
        Some("resource_limited")
    );
    assert_eq!(
        result.get("limit_trigger").and_then(Value::as_str),
        Some("stdout_bytes")
    );
    assert_eq!(
        result.get("job_terminated").and_then(Value::as_bool),
        Some(true)
    );
    assert!(decode_result_stream(&result, "stdout_base64").len() <= 64);
    fs::remove_dir_all(root).expect("native contract fixture cleanup");
}

#[test]
fn run_terminates_the_job_when_wall_time_is_exhausted() {
    let root = unique_temp_root();
    let fixture = fixture_paths(&root);
    let mut request = fixture_request(&fixture, &["sleep", "1000"]);
    request["limits"]["wall_time_seconds"] = json!(0.1);

    let result = broker_json(&["run", "--stdio"], Some(request.to_string().as_bytes()));

    assert_eq!(
        result.get("status").and_then(Value::as_str),
        Some("timed_out")
    );
    assert_eq!(
        result.get("limit_trigger").and_then(Value::as_str),
        Some("wall_time_seconds")
    );
    assert_eq!(
        result.get("job_terminated").and_then(Value::as_bool),
        Some(true)
    );
    fs::remove_dir_all(root).expect("native contract fixture cleanup");
}

struct FixturePaths {
    executable: PathBuf,
    runtime: PathBuf,
    work: PathBuf,
    output: PathBuf,
    temporary: PathBuf,
}

fn fixture_paths(root: &std::path::Path) -> FixturePaths {
    let work = root.join("work");
    let output = root.join("output");
    let temporary = root.join("temporary");
    for directory in [&work, &output, &temporary] {
        fs::create_dir_all(directory).expect("native contract fixture directory");
    }
    let executable = fixture_path();
    let runtime = executable
        .parent()
        .expect("fixture runtime parent")
        .to_owned();
    FixturePaths {
        executable,
        runtime,
        work,
        output,
        temporary,
    }
}

fn fixture_request(fixture: &FixturePaths, arguments: &[&str]) -> Value {
    json!({
        "schema_version": PROTOCOL_VERSION,
        "executable": fixture.executable,
        "arguments": arguments,
        "cwd": fixture.work,
        "roots": {
            "read_only": [fixture.work],
            "runtime_read": [fixture.runtime],
            "writable": [fixture.output, fixture.temporary],
        },
        "output_root": fixture.output,
        "temporary_root": fixture.temporary,
        "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
        "limits": {
            "wall_time_seconds": 2.0,
            "cpu_time_seconds": 2.0,
            "memory_bytes": 67108864.0,
            "disk_bytes": 1048576.0,
            "process_count": 1.0,
            "open_files": 16.0,
            "stdout_bytes": 1024.0,
            "stderr_bytes": 1024.0,
            "output_file_count": 1.0,
            "output_total_bytes": 1024.0,
            "termination_grace_seconds": 0.1,
        },
        "allow_child_processes": false,
    })
}

fn decode_result_stream(result: &Value, field: &str) -> Vec<u8> {
    let encoded = result
        .get(field)
        .and_then(Value::as_str)
        .expect("broker result stream");
    base64::Engine::decode(&base64::engine::general_purpose::STANDARD, encoded)
        .expect("broker result stream base64")
}

fn broker_json(arguments: &[&str], input: Option<&[u8]>) -> Value {
    let mut command = Command::new(broker_path());
    command.args(arguments);
    if input.is_some() {
        command.stdin(std::process::Stdio::piped());
    }
    let output = if let Some(input) = input {
        let mut child = command.spawn().expect("start native broker");
        use std::io::Write;
        child
            .stdin
            .take()
            .expect("broker stdin")
            .write_all(input)
            .expect("write broker request");
        child.wait_with_output().expect("wait for native broker")
    } else {
        command.output().expect("run native broker")
    };
    assert_success(&output);
    serde_json::from_slice(&output.stdout).expect("broker stdout is strict JSON")
}

fn assert_success(output: &Output) {
    assert!(
        output.status.success(),
        "native broker failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn broker_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_ocean-sandbox-broker"))
}

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_native-contract-fixture"))
}

fn unique_temp_root() -> PathBuf {
    std::env::temp_dir().join(format!("ocean-broker-contract-{}", std::process::id()))
}
