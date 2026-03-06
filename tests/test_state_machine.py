import unittest

from app.utils.state_machine import can_transition, is_valid_status


class StateMachineTests(unittest.TestCase):
    def test_known_statuses_are_valid(self):
        for status in ["creating", "running", "stopped", "deleting", "failed"]:
            self.assertTrue(is_valid_status(status))

    def test_invalid_status_rejected(self):
        self.assertFalse(is_valid_status("unknown"))

    def test_stop_not_allowed_from_creating(self):
        self.assertFalse(can_transition("creating", "stopped"))

    def test_start_allowed_from_failed(self):
        self.assertTrue(can_transition("failed", "running"))


if __name__ == "__main__":
    unittest.main()
