"""Safety validation and optional real isolated namespace integration.

Live test is opt-in because some developer Linux hosts disallow user namespaces;
its absence must never be reported as an actual live-test pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_isolated_kernel_lab import NODES, NAMESPACE_EDGES, marker, ns


class IsolatedLabSafetyTests(unittest.TestCase):
    def test_requires_new_user_and_network_namespace(self):
        with self.assertRaises(RuntimeError):
            marker()

    def test_only_documented_2_host_virtual_links(self):
        self.assertEqual(NODES, ('ril-h1', 'ril-h2'))
        self.assertEqual(len(NAMESPACE_EDGES), 2)
        self.assertEqual({x[0] for x in NAMESPACE_EDGES}, {'primary', 'backup'})

    def test_denies_unrecognized_host_namespace(self):
        with self.assertRaises(ValueError):
            ns('some-production-router', 'ip', 'link', 'set', 'eth0', 'down')

    def test_manual_inside_without_namespace_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ)
            env.pop('RIL_PARENT_USER_NS', None)
            env.pop('RIL_PARENT_NET_NS', None)
            p = subprocess.run([sys.executable, str(ROOT / 'scripts/run_isolated_kernel_lab.py'),
                                '--inside', '--output', d,
                                '--compare', sys.executable,
                                '--netlink', sys.executable],
                               capture_output=True, text=True, timeout=8, env=env)
            self.assertEqual(p.returncode, 2)
            self.assertIn('refusing to modify host namespace', p.stderr)


@unittest.skipUnless(os.environ.get('RIL_RUN_NETNS_LIVE') == '1',
                     'requires explicit RIL_RUN_NETNS_LIVE=1, user namespaces and veth')
class OptInActualKernelFaultTest(unittest.TestCase):
    def test_dual_link_route_fault_and_tcp_correlation(self):
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, str(ROOT / 'scripts/run_isolated_kernel_lab.py'),
                                '--output', d,
                                '--compare', str(ROOT / 'build/connect_reactor_compare'),
                                '--netlink', str(ROOT / 'build/rtnetlink_monitor')],
                               capture_output=True, text=True, timeout=25)
            self.assertEqual(p.returncode, 0, p.stderr)
            report = json.loads((Path(d)/'report.json').read_text())
            self.assertEqual(report['status'], 'PASS')
            self.assertTrue(all(report['checks'].values()))
            self.assertEqual(report['sampler']['attempts'], 360)
            self.assertGreater(report['monitor']['route_notifications'], 0)
            self.assertGreater(report['monitor']['link_notifications'], 0)
            self.assertGreater(report['per_phase']['blackhole']['failures'], 0)
            self.assertGreater(report['per_phase']['backup']['successes'], 0)


if __name__ == '__main__': unittest.main()
