# Evidence and test status (2026-11-01)

- Python unit tests: 26 passed in the original local v0.2.0 source package.
- Three scenario fixtures are synthetic. They are analyzer regression tests, not live BGP convergence measurements.
- C++ TCP connect probe was compiled and exercised against a local TCP listener.
- Real Containerlab/FRRouting scenarios are prepared but NOT yet run end-to-end in the available execution environment.
- No GitHub Actions workflow, scheduled job, paid API, cloud server, or public target probing.
- Findings from route tables, BGP state and HTTP delivery are observations and heuristics, not automatic proof of root cause.

To validate on your own machine:

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m routeincident.cli scenario-demo
```

The test totals refer to the original downloaded source package. Confirm them again from a fresh clone before citing the public repository.
