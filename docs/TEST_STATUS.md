# Evidence and test status (updated 2026-11-07)

## Implemented in source

- Read-only router-state capture and state diff (Linux, FRRouting).
- Conservative Junos/IOS/EOS structured text parsers for recognized BGP peers, IPv4 routes and interfaces. Versions and unknown output lines are not treated as confirmed good state.
- Three synthetic scenario fixtures, Python unit tests and C++ nonblocking TCP probe.
- Three-router BGP Containerlab topology, a read-only all-peer BGP/HTTP smoke check and opt-in local fault controller.
- Bounded continuous HTTP sampling during test faults, with sampled failure spans and possible outage-window bounds.

## Not yet independently validated

- Fresh public checkout: test suite has not been executed by the assistant here; earlier 26-test pass result refers to a previous source revision, not this revision.
- Real Docker/Containerlab: no live three-router execution or end-to-end scenario run in this environment, which lacks Docker/Containerlab.
- Hardware: Junos, IOS and EOS text adapters have synthetic parser tests, NOT actual vendor-hardware validation.
- The stream does not measure exact BGP convergence, true packet loss, or microsecond forwarding latency.

## Verification on an authorized local Linux machine

~~~bash
python3 -m unittest discover -s tests -v
containerlab validate -t labs/three-router/topology.clab.yml
cd labs/three-router
docker build -t routeincident-endpoint:local -f Dockerfile.endpoint .
sudo containerlab deploy -t topology.clab.yml
cd ../..
PYTHONPATH=src python3 scripts/lab_smoke.py --output lab-output/smoke.json
PYTHONPATH=src python3 scripts/lab_run.py --scenario neighbor_outage --allow-faults
PYTHONPATH=src python3 scripts/lab_run.py --scenario backup_failover --allow-faults
PYTHONPATH=src python3 scripts/lab_run.py --scenario route_ok_blackhole --allow-faults
~~~

Review each live report and the recorded continuous HTTP samples before making recovery claims.
There are no scheduled jobs, paid services or GitHub Actions workflows.

## 2026-11-07 evidence note

- The newly added HTTP status parser and artifact audit have **10 new unit/real-loopback-socket tests passing in an isolated local checkout**. This is not a claim that the entire repository test suite ran successfully; the full revised suite still needs fresh checkout validation.
- The modified HTTP probe was run against a temporary real loopback HTTP server for five requests: 5/5 received HTTP 200. This validates only localhost HTTP parsing, not BGP/Containerlab execution.
- Full live FRR/Containerlab fault injection is **still unverified here** because Docker and Containerlab are unavailable. No real convergence or failover timing result is claimed.
- Completed local lab runs now produce `incident.md` and `audit.json` automatically, with independent offline recheck via `python3 scripts/audit_run.py --run-dir lab-output/<scenario>`.
- No GitHub Actions workflows have been added; no paid cloud compute is required for the test path.
