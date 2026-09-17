"""Agent pods receive the chat attachment contract (cap + extension allowlist)."""

import unittest
from types import SimpleNamespace


class K8sServiceUploadEnvTest(unittest.TestCase):
    def setUp(self):
        from app.services.k8s_service import K8sService

        self.service = K8sService()
        self.service.enabled = False
        self._snapshot = (
            self.service.settings.chat_upload_extensions,
            self.service.settings.max_upload_mb,
        )

    def tearDown(self):
        self.service.settings.chat_upload_extensions, self.service.settings.max_upload_mb = self._snapshot

    def _env_map(self, runtime_type):
        agent = SimpleNamespace(id="a1", runtime_type=runtime_type, mount_path="/workspace", name="A")
        return {e.name: getattr(e, "value", None) for e in self.service._build_agent_container_env(agent)}

    def test_both_runtimes_receive_the_chat_upload_contract(self):
        self.service.settings.chat_upload_extensions = "pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml"
        self.service.settings.max_upload_mb = 25
        for runtime_type in ("native", "opencode"):
            env = self._env_map(runtime_type)
            self.assertEqual(env["EFP_MAX_UPLOAD_MB"], "25", runtime_type)
            self.assertEqual(env["EFP_CHAT_UPLOAD_EXTENSIONS"], "pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml", runtime_type)

    def test_env_follows_the_portal_settings(self):
        self.service.settings.chat_upload_extensions = " .MD; txt "
        self.service.settings.max_upload_mb = 40
        env = self._env_map("native")
        self.assertEqual(env["EFP_MAX_UPLOAD_MB"], "40")
        self.assertEqual(env["EFP_CHAT_UPLOAD_EXTENSIONS"], "md,txt")

    def test_garbage_settings_fall_back_to_the_defaults(self):
        self.service.settings.chat_upload_extensions = "???"
        self.service.settings.max_upload_mb = 0
        env = self._env_map("opencode")
        self.assertEqual(env["EFP_MAX_UPLOAD_MB"], "25")
        self.assertEqual(env["EFP_CHAT_UPLOAD_EXTENSIONS"], "pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml")


if __name__ == "__main__":
    unittest.main()
