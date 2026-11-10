import sys
import unittest
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'scripts'))
from profile_native_rx import run,MODES

class NativeReceiverProfile(unittest.TestCase):
    def test_four_receiver_modes_unique(self):
        self.assertEqual(len(MODES),4)
        self.assertEqual(len(set(MODES)),4)
    def test_reject_invalid_design_without_running(self):
        for options in ({'count':0},{'rates':(100000,)},{'repeats':0}):
            with self.assertRaises(ValueError):run(Path('nonexistent'),Path('nonexistent'),Path('unused'),**options)
if __name__=='__main__':unittest.main()
