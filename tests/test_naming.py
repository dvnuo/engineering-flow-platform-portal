import unittest

from app.utils.naming import runtime_names, to_k8s_name


class NamingTests(unittest.TestCase):
    def test_to_k8s_name_sanitizes(self):
        self.assertEqual(to_k8s_name("My Robot_v1"), "robot-my-robot-v1")

    def test_to_k8s_name_limits_length(self):
        source = "x" * 200
        name = to_k8s_name(source)
        self.assertLessEqual(len(name), 63)

    def test_runtime_names_use_robot_id(self):
        deploy, svc, pvc, endpoint = runtime_names("abc-123")
        self.assertEqual(deploy, "robot-abc-123")
        self.assertEqual(svc, "robot-abc-123-svc")
        self.assertEqual(pvc, "robot-abc-123-pvc")
        self.assertEqual(endpoint, "/r/abc-123")


if __name__ == "__main__":
    unittest.main()
