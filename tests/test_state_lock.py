import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "dark-factory"
loader = importlib.machinery.SourceFileLoader("dark_factory_state_lock", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
dark_factory = importlib.util.module_from_spec(spec)
loader.exec_module(dark_factory)

DEFAULT_STATE = dark_factory.DEFAULT_STATE
StateStore = dark_factory.StateStore
ControllerLock = dark_factory.ControllerLock


class StateStoreTests(unittest.TestCase):
    def test_missing_state_loads_an_independent_default(self):
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            state = store.load()
            self.assertEqual(state, DEFAULT_STATE)
            state["attempts"]["provider"] = 1
            self.assertEqual(store.load()["attempts"], {})

    def test_save_round_trips_state_with_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            store.save({"phase": "running", "issue": 42})

            self.assertEqual(
                store.load(),
                DEFAULT_STATE | {"phase": "running", "issue": 42},
            )
            self.assertEqual(
                json.loads((Path(root) / ".dark-factory" / "controller.json").read_text()),
                DEFAULT_STATE | {"phase": "running", "issue": 42},
            )
            self.assertEqual(
                list((Path(root) / ".dark-factory").glob(".controller.json.*")),
                [],
            )


class ControllerLockTests(unittest.TestCase):
    def test_live_controller_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as root:
            first = ControllerLock(root).acquire()
            self.addCleanup(first.release)

            record = json.loads(
                (Path(root) / ".dark-factory" / "controller.lock").read_text()
            )
            self.assertEqual(record["pid"], os.getpid())
            self.assertTrue(record["started_at"])
            with self.assertRaisesRegex(RuntimeError, "already running"):
                ControllerLock(root).acquire()

    def test_dead_controller_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as root:
            lock_path = Path(root) / ".dark-factory" / "controller.lock"
            lock_path.parent.mkdir()
            lock_path.write_text('{"pid": 2147483647, "started_at": "old"}\n')

            lock = ControllerLock(root).acquire()
            try:
                record = json.loads(lock_path.read_text())
                self.assertEqual(record["pid"], os.getpid())
            finally:
                lock.release()

            self.assertFalse(lock_path.exists())

    def test_short_write_still_publishes_a_complete_record(self):
        original_write = os.write

        def short_write(descriptor, contents):
            return original_write(descriptor, contents[:3])

        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(dark_factory.os, "write", side_effect=short_write):
                with ControllerLock(root):
                    record = json.loads(
                        (Path(root) / ".dark-factory" / "controller.lock").read_text()
                    )
            self.assertEqual(record["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()
