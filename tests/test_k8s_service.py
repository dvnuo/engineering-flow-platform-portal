import unittest
from types import SimpleNamespace


class K8sServiceNoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app.services.k8s_service import K8sService
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(f"Missing dependency in environment: {exc}")
        cls.K8sService = K8sService

    def setUp(self):
        self.service = self.K8sService()
        self.service.enabled = False

    def test_create_robot_runtime_noop_running(self):
        robot = SimpleNamespace()
        status = self.service.create_robot_runtime(robot)
        self.assertEqual(status.status, "running")

    def test_stop_robot_runtime_noop_stopped(self):
        robot = SimpleNamespace()
        status = self.service.stop_robot(robot)
        self.assertEqual(status.status, "stopped")


if __name__ == "__main__":
    unittest.main()
