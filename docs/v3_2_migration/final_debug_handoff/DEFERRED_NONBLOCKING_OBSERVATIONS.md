# Deferred Nonblocking Observations

These items prevent formal certification but do not represent a reproduced
failure in the local candidate:

- Hosted CI is `PENDING_PUSH`. This turn intentionally made no push and did not
  dispatch a workflow.
- A real POSIX interruption/process-group run was not executed. WSL is not
  installed and Docker is unavailable on the current Windows host. Bash syntax
  parsing and the Windows interrupt race passed.
- The final independent certification audit has not been run. The local repair
  candidate is ready for that audit, but its probe labels do not replace the
  independent auditor's disposition.

The historical denied reauthorization report and historical finding records are
preserved unchanged.
