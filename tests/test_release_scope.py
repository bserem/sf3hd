import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_hd_test import release_replacements, check_sidecar_directory
from decode_stages import STAGES


class ReleaseScopeTests(unittest.TestCase):
    def test_mixed_work_directory_excludes_all_experiments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for _, name, _, _ in STAGES:
                (root / name).touch()
            for name in ('Opening.ppg', 'Select.ppg', 'scrscrn.ppg', 'TitleTM.ppg', 'stage99.ppg'):
                (root / name).touch()
            self.assertEqual({p.name for p in release_replacements(root)}, {s[1] for s in STAGES})
            (root / STAGES[0][1]).unlink()
            with self.assertRaises(SystemExit):
                release_replacements(root)

    def test_stale_object_sidecar_is_rejected_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pl00.hdcg').touch()
            check_sidecar_directory(root)
            extra = root / 'ef02_usa.hdcg'
            extra.write_bytes(b'keep')
            with self.assertRaises(SystemExit):
                check_sidecar_directory(root)
            self.assertEqual(extra.read_bytes(), b'keep')
