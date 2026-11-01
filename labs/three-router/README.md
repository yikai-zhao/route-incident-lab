# Three-router local BGP testbed

This is a planned and configured lab, not a claim that the live Containerlab topology has passed validation.

Requires Linux, Docker, Containerlab, Python 3.10+, and suitable privileges.

Build a local endpoint image from this directory:

```bash
cd labs/three-router
docker build -t routeincident-endpoint:local -f Dockerfile.endpoint .
sudo containerlab deploy -t topology.clab.yml
```

Router names are r1, r2, r3, and Linux endpoints h1, h2. The source endpoint is 10.10.1.2, the HTTP target 10.10.2.2:8080. Only test addresses and local containers are used.

From the repository root, after deployment:

```bash
PYTHONPATH=src python3 scripts/lab_run.py --scenario neighbor_outage --allow-faults
PYTHONPATH=src python3 scripts/lab_run.py --scenario backup_failover --allow-faults
PYTHONPATH=src python3 scripts/lab_run.py --scenario route_ok_blackhole --allow-faults
```

The normal collector only reads state. The separate lab controller refuses to inject faults without `--allow-faults`, checks each expected container's `routeincident.testbed=true` label, and changes only local lab containers. Do not run it against company or production networks.

Destroy only the explicitly named local lab when finished:

```bash
sudo containerlab destroy -t topology.clab.yml
```

Check the output report's assertions rather than assuming every scenario succeeded. Snapshot timestamps are observation windows, not exact forwarding convergence times.
