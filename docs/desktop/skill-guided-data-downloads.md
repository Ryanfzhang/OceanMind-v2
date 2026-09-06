# Skill-guided data acquisition

**Read Skill → LLM writes Python → `ocean_expert_run_code` → download.**
There is no separate download tool, download/analysis mode, or provider-specific
backend. Experts select the general acquisition, MODIS, SeaWiFS or Copernicus Marine
Skills through the existing role-filtered catalog. Their heads retain `name`,
`description`, `metadata.origin` and `metadata.roles`. The Data and Literature
Experts can search the web and read provider documentation.

## Execution and storage

- Expert Python execution allows networking on macOS and Linux/WSL. Original
  sources remain read-only and unrelated files are not added to readable roots.
- Reusable downloads go in `OCEAN_WORK_DIR/downloads`, persistent across the logical
  Expert session. Formal shared outputs use `OCEAN_OUTPUT_DIR` and the existing
  result/handoff contract; downloads are not automatically registered Task Sources.
  No separate audit copy is created.
- Scripts implement subsets, volume checks, timeouts, bounded retries, partial-file
  handling, cache validation and scientific checks. These are not backend HTTP rules.
- Existing resource limits and cancellation remain. Use in-process Python clients;
  network access does not enable arbitrary shell subprocesses.
- Agents must not upload local data without explicit user authorization or read
  unrelated credentials. **This instruction is not an enforced no-exfiltration guarantee.**
- Internal non-Expert sandbox consumers retain the offline default. This is not
  an Agent-facing mode switch. The existing Windows broker cannot run networked
  code and fails explicitly rather than falling back to unrestricted execution.

## Dependencies and accounts

Public HTTP scripts can use the Python standard library. Provider clients such as
`copernicusmarine` and `earthaccess` must be installed in the configured scientific
environment. Networking does not install packages or supply provider accounts.
The executor still has a scrubbed environment and temporary HOME: backend API keys
and SDK login files are not inherited. No credential UI or token-forwarding mechanism
is introduced here. Missing authentication must be reported, or the user can attach
a locally downloaded source. Never request secrets in chat or claim an authenticated
download succeeded without verification.

Acquisition within the user's request needs no new per-file approval tool. Ask when
scope, volume or cost materially changes. Verified reusable methods can optionally
be saved with `ocean_save_experience`; the existing Curator decides how to use them.
No additional review rules or fixed repetition thresholds are introduced.
