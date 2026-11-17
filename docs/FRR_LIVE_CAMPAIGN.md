# Repeatable FRR/Containerlab live validation

Status 2026-11-17: **not executed** here because Docker/Containerlab are
unavailable. Existing successful static-route namespace experiments do **not**
measure dynamic BGP failover.

This campaign reuses the existing isolated five-container topology and
labeled-container-only controller. It runs a fresh read-only BGP/HTTP smoke
check before every scenario, repeats all three faults three times, then
independently verifies the resulting reports, six initial Established peer
views, snapshot phases and continuous HTTP samples. No CI, cloud or money.

On a local authorized Linux host:

    cd labs/three-router
    docker build -t routeincident-endpoint:local -f Dockerfile.endpoint .
    sudo containerlab deploy -t topology.clab.yml
    cd ../..
    python3 scripts/run_frr_campaign.py --allow-faults --repeats 3 --output lab-output/frr-campaign-1

Review the report. Independent read-only re-audit:

    python3 scripts/run_frr_campaign.py --audit-only --repeats 3 --output lab-output/frr-campaign-1

A clean destination is required for each new live campaign, preventing stale
successful artifacts from being reused after a failed run. Missing artifacts,
failed assertions, missing continuous HTTP observations or incomplete runs
cause an INCOMPLETE_OR_FAILED verdict. The summary includes SHA-256 digests
of the evidence at inspection time; it is not a cryptographic proof of
independent remote-machine execution.

Important limits: sampled outage-window bounds are not exact convergence
times. Dynamic BGP success remains unverified until actually run and the
saved reports inspected. Do not use on production/employer networks.
