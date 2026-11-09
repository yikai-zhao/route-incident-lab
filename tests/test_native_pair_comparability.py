import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from compare_native_pairs import assess_pairs, eligible_p99_summary


def trial(idle, rate=1000, achieved=1000, pair=0, p99=100, status='PASS'):
    return {'idle': idle, 'rate_target_pps': rate, 'pair': pair,
            'status': status, 'sender': {'achieved_sendto_pps': achieved,
                                       'successful_sendto': 100},
            'receiver': {'processed': 100, 'queue_p99_us': p99}}


class NativePairValidityTests(unittest.TestCase):
    def test_identical_target_and_achieved_load_is_comparable(self):
        x = assess_pairs([trial('sleep', p99=200), trial('spin', p99=80)])
        self.assertEqual(len(x), 1)
        self.assertTrue(x[0]['comparable'])
        self.assertEqual(eligible_p99_summary(x)[0]['median_queue_p99_us'],
                         {'sleep': 200, 'spin': 80})

    def test_integrity_pass_does_not_make_wrong_pacing_comparable(self):
        x = assess_pairs([trial('sleep', achieved=500), trial('spin', achieved=650)])
        self.assertFalse(x[0]['comparable'])
        self.assertIn('sleep_target_rate_not_attained', x[0]['rejection_reasons'])
        self.assertIsNone(eligible_p99_summary(x)[0]['median_queue_p99_us'])

    def test_both_in_target_band_but_mismatched_loads(self):
        x = assess_pairs([trial('sleep', achieved=945), trial('spin', achieved=1055)])
        self.assertFalse(x[0]['comparable'])
        self.assertIn('within_pair_load_mismatch', x[0]['rejection_reasons'])

    def test_failed_run_blocks_comparison_even_with_good_rate(self):
        x = assess_pairs([trial('sleep'), trial('spin', status='FAIL')])
        self.assertEqual(x[0]['rejection_reasons'], ['spin_integrity_failed'])

    def test_missing_and_duplicate_modes_rejected(self):
        x = assess_pairs([trial('sleep'), trial('sleep'), trial('spin', pair=1)])
        self.assertEqual(len(x), 2)
        self.assertTrue(all(not y['comparable'] for y in x))

    def test_partial_pair_and_missing_metrics_rejected(self):
        broken = trial('spin'); broken['sender'] = None
        x = assess_pairs([trial('sleep'), broken])
        self.assertIn('spin_missing_raw_metrics', x[0]['rejection_reasons'])

    def test_count_mismatch_blocks_comparison(self):
        broken = trial('spin'); broken['receiver']['processed'] = 97
        x = assess_pairs([trial('sleep'), broken])
        self.assertIn('spin_sender_receiver_count_mismatch', x[0]['rejection_reasons'])

    def test_same_target_band_but_6_percent_spread_rejected(self):
        x = assess_pairs([trial('sleep', achieved=980), trial('spin', achieved=1040)])
        self.assertIn('within_pair_load_mismatch', x[0]['rejection_reasons'])

    def test_group_order_no_effect(self):
        r = [trial('sleep'), trial('spin'), trial('sleep', pair=1), trial('spin', pair=1)]
        self.assertEqual(assess_pairs(r), assess_pairs(list(reversed(r))))

    def test_invalid_tolerance_rejected(self):
        for t in (0, 1, -1):
            with self.assertRaises(ValueError): assess_pairs([], target_tolerance=t)

    def test_archived_sample_is_seven_of_nine_under_published_limits(self):
        sample=Path(__file__).resolve().parents[1]/'evidence'/'native-pacing'/'archived-pinned-v2-minimal.json'
        data=json.loads(sample.read_text())
        decisions=assess_pairs(data['records'])
        self.assertEqual(sum(d['comparable'] for d in decisions),7)
        self.assertEqual(len(decisions),9)
        summary=eligible_p99_summary(decisions)
        self.assertEqual(summary[0]['median_queue_p99_us']['sleep'],308.934)
        self.assertEqual(summary[2]['rejected_pairs'],[0,2])

    def test_only_matched_pairs_enter_median(self):
        records = [trial('sleep',pair=0,p99=200),trial('spin',pair=0,p99=100),
                   trial('sleep',pair=1,achieved=500,p99=5),
                   trial('spin',pair=1,achieved=550,p99=6)]
        x=eligible_p99_summary(assess_pairs(records))[0]
        self.assertEqual(x['eligible_pairs'],1)
        self.assertEqual(x['rejected_pairs'],[1])
        self.assertEqual(x['median_queue_p99_us'], {'sleep':200,'spin':100})

if __name__ == '__main__': unittest.main()
