# Evidence and test status (2026-11-02)

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
