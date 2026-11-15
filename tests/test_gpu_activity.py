from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

spec = spec_from_file_location('gpu_activity', Path(__file__).resolve().parents[1] /
                               'deploy/aic-10/gpu_activity.py')
module = module_from_spec(spec)
spec.loader.exec_module(module)


class GpuActivityTests(unittest.TestCase):
    def test_overlap_idle_and_restart(self):
        with TemporaryDirectory() as directory, patch.object(module.time, 'time') as clock:
            path = Path(directory) / 'activity.db'
            activity = module.GpuActivity(path)
            clock.return_value = 100
            activity.begin()
            clock.return_value = 102
            activity.begin()
            clock.return_value = 103
            activity.end()
            clock.return_value = 107
            activity.end()
            clock.return_value = 110
            self.assertEqual(activity.report()['total_seconds'], 7)
            activity.db.close()
            activity = module.GpuActivity(path)
            self.assertEqual(activity.report()['total_seconds'], 7)
            activity.db.close()
