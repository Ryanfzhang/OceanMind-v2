# ADR 0017: Windows Trusted Execution Broker

- Status: Superseded by ADR 0019; broker security rationale retained as history
- Date: 2026-07-15
- Decision IDs: FE-06, OD-07

## Context

`AnalysisRun` executes model-authored Python and must not silently fall back to
an ordinary Windows child process. A Job Object can bound a process tree but
does not confine filesystem or network access. Conversely, a restricted token
without a Job Object cannot prove cleanup, CPU/memory/process limits, or child
containment. The macOS Seatbelt backend is not transferable to native Windows.

The existing implementation therefore reports native Windows execution as
unavailable and rejects analysis attempts before process start. That fail-closed
behavior remains in force while this decision is implemented and verified.

## Decision

1. The Windows trusted backend will be a small, signed
   `ocean-sandbox-broker.exe`, shipped beside the frozen Ocean sidecar. Python
   and Electron invoke it only with a versioned JSON request over inherited
   handles; they do not receive an arbitrary command, capability SID, ACL, or
   host-path escape hatch.
2. Each attempt uses a newly created AppContainer identity with no internet or
   private-network capabilities. The broker creates the process with the
   AppContainer token and grants that identity only read access to the frozen
   runtime/code/input roots and write access to that attempt's output/temp
   roots. It never grants the workspace root, home profile, credential stores,
   UNC roots, removable media, or inherited environment.
3. The broker assigns the process to a fresh Job Object before it resumes. The
   Job Object has `KILL_ON_JOB_CLOSE`, active-process, CPU-time, memory, and
   per-process memory limits. Its completion port is supervised for timeout,
   limit, and unexpected-child events. Termination closes the Job Object first
   and records the observed process-tree outcome; it is not best-effort
   `taskkill` from the renderer.
4. The broker response is a bounded, duplicate-key-free JSON envelope. The
   sidecar rejects non-finite numbers, out-of-range exit codes, malformed limit
   identifiers, missing Job cleanup proof, and internally inconsistent terminal
   combinations such as `succeeded` with a nonzero exit code or limit trigger.
   A result cannot become a trusted `AnalysisRun` outcome merely because it is
   syntactically valid JSON.
5. The child environment is rebuilt from an allowlist equivalent to the macOS
   contract. It contains only run identifiers and declared input/output paths;
   `PATH`, `HOME`, `USERPROFILE`, proxy settings, cloud credentials, Python
   startup hooks, and parent environment variables are absent unless explicitly
   declared by the immutable runtime profile.
6. The broker opens input/runtime files and output roots through canonical
   handles before launch. It rejects reparse points, junctions, symlinks,
   alternate data streams, device names, UNC paths, volume GUID paths, and any
   path whose final handle escapes its allowed root. Postflight output
   enumeration applies the same checks plus regular-file, hardlink, size, and
   count requirements before an attempt can become publishable.
7. `get_sandbox_execution_capabilities()` may return a Windows backend only
   when the broker is present, Authenticode-valid, version/schema-compatible,
   and its local self-check proves declared-output write and outside-read/network
   denial. A missing or failed check keeps `AnalysisRun` unavailable and all
   publish/review/report gates fail closed.
8. The Windows broker protocol, its AppContainer profile/template, and the
   runtime/dependency fingerprint are release inputs. Any broker, Windows
   build, Python/runtime, or policy change creates a new fingerprint and
   requires fresh adversarial evidence.

## Non-goals

- Job Objects alone are not accepted as a sandbox.
- WSL is not a substitute for a native Windows Desktop claim.
- The renderer cannot select a broader container capability, relax limits, or
  submit a raw Win32 command line.
- A successful process exit does not establish scientific correctness; normal
  verification and publication gates remain required.

## Required Native Validation

The Windows 11 x64 CI/release runner must execute all of these with the final
signed broker and frozen sidecar:

1. Reads outside declared roots, `%USERPROFILE%`, credential paths, UNC shares,
   mapped drives, removable volumes, and registry-backed cloud configuration
   fail.
2. Network, proxy, loopback, named-pipe, and child-process escape attempts fail
   unless a separately versioned policy explicitly permits the mechanism.
3. Symlink, junction/reparse, hardlink, ADS, device-name, case-normalization,
   short-name, volume-GUID, and TOCTOU path fixtures fail before publication.
4. Timeout, cancel, broker crash, Desktop quit, and forced kill leave no child
   process in the assigned Job Object and recover unfinished attempts as
   `interrupted`.
5. CPU, memory, process-count, stdout/stderr, file-count, and byte limits are
   observable in durable attempt evidence and cannot be widened by a renderer
   request or model-authored code.
6. A local NetCDF fixture completes code -> checks -> SpatialLayer/LinkedPlot
   -> MapScene with the same frozen numeric tolerance as macOS.

## Consequences

- Native Windows support remains explicitly unavailable until the broker and
  validation suite ship together. This avoids a misleading partial release.
- Electron's ordinary sidecar lifecycle may use platform process cleanup for
  availability, but it is not evidence that the AnalysisRun sandbox contract
  has been met.
- The future broker is a security-critical release component and needs its own
  source review, reproducible build metadata, SBOM entry, signing, and rollback
  policy.

## Verification Status

The shared output inventory already rejects symlink, hardlink, non-regular,
junction/reparse, and ADS-shaped outputs, and macOS exercises the current
Seatbelt contract. The native source now lives in
`native/windows-sandbox-broker`: it has a strict, bounded JSON v1 request/result
contract shared with `oceanx.sandbox.windows_broker`, cross-compiles for
Windows x64, validates its input shape without a shell, and probes real
AppContainer-profile creation plus a Job Object carrying
`KILL_ON_JOB_CLOSE`/active-process limits. Before it launches a child, `run`
also opens every declared existing path component with
`FILE_FLAG_OPEN_REPARSE_POINT`, rejects reparse points, compares final DOS
handle paths to the policy roots, and holds the validation handles until the
request ends. A successful request validation creates a unique temporary
AppContainer profile and temporarily grants its SID only the validated
read/runtime and writable roots, retaining each original root DACL for
restoration. It then creates a per-request Job Object with
`KILL_ON_JOB_CLOSE`, a single active process, CPU user-time, process-memory,
and job-memory limits from the request budget, and associates a completion port
before any child could be assigned. The launcher creates its exact application
path suspended with the AppContainer security-capabilities attribute and an
explicit standard-stream handle list, assigns the process to the Job before
`ResumeThread`, drains bounded stdout/stderr, and terminates/closes the Job
before ACL restoration when it cannot prove the tree has drained. The Python
side result parser additionally requires one exact JSON object without
duplicate keys or non-standard numeric constants. It rejects non-finite
durations, 32-bit exit-code overflow, unsafe limit identifiers, false Job
cleanup claims, and terminal-state contradictions before any result can reach
the `SandboxExecutionResult` mapper. This protects the future launch path but
does not itself establish a Windows sandbox.

The native Windows CI contract builds a test-only `native-contract-fixture`
beside the broker only under Cargo's `native-contract-fixture` feature. It runs
that fixture through the AppContainer path to prove a declared output write,
an ungranted outside-root read denial, a denied loopback connection, stdout
capture termination, and wall-time termination, each with a Job tree-drain
result. The fixture is feature-gated and is not part of the production broker
build. This is still an adversarial
foundation rather than a model-code claim: Windows runner output is required
before it counts as target evidence. The Windows sidecar build copies only the
unsigned broker beside `ocean-backend.exe`, while the package verifier requires
that artifact to report itself fail closed.

This still does **not** enable `AnalysisRun`: the broker's `doctor` and
`self-check` deliberately report unavailable until the final signed sidecar,
Windows adversarial fixtures, AppContainer outside-read/network evidence,
timeout/cancel/crash evidence, and final runtime baseline have passed. The
required release manifest, SHA-256, Authenticode check, and model-free
self-check remain fail-closed prerequisites in Python; no unsigned or
incomplete broker can make `AnalysisRun` available on native Windows.
