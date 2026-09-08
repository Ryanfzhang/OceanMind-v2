# Expert file handoff across rounds

OceanX retains a logical expert's checkpointed conversation and working directory.
Previously, code responses truncated long logs without giving their file paths;
an expert needed another Python execution to consult the input manifest and read
the original output. This encouraged filesystem searches and repeated computation.

Following EvoScientist's use of filesystem tools and file-backed results, experts
now receive exact stdout/stderr paths and a result bundle path from code execution.
`ocean_read_file` retrieves UTF-8 text from their persistent session with character
pagination. Follow-up memory includes those same paths, including for older execution
records. Reading does not run Python or consume a code execution attempt. Old read
responses are eligible for the existing context compaction mechanism.

The expert reads omitted evidence and returns its conclusions and existing candidates;
the coordinator receives that answer and continues to own publication. Existing
expert identity, checkpoints, candidate registration and publication remain in place.
This adopts the file-based handoff pattern, not EvoScientist's complete orchestration
implementation or unrestricted filesystem backend. Existing task/expert isolation
also applies to file reads, including symlink resolution.

Reference inspected: EvoScientist/EvoScientist, commit b36c19a, filesystem backend
construction in EvoScientist.py and subagents/data_analysis.yaml.

Regression coverage includes truncated-result retrieval, Unicode pagination,
cross-round saved-log reading without a new execution, and role tool surfaces.
Live model behavior still requires validation in a new OceanX runtime; these tests
do not establish that every repeated research round is eliminated.
