import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "publish-chart.sh"
CHART = SCRIPT.parents[2] / "helm/testbench/Chart.yaml"
VERSION = next(line.split(":", 1)[1].strip().strip('"')
               for line in CHART.read_text().splitlines() if line.startswith("version:"))


class ChartPublisherTests(unittest.TestCase):
    def run_publisher(self, overrides=None, published=False, with_helm=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if with_helm:
                helm = root / "helm"
                helm.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, os, sys\n"
                    "with open(os.environ['CALL_LOG'], 'a') as f:\n"
                    "    f.write(json.dumps({'argv': sys.argv[1:],\n"
                    "        'registry_config': os.environ.get('HELM_REGISTRY_CONFIG', '')}) + '\\n')\n"
                    "if sys.argv[1:3] == ['registry', 'login']: sys.stdin.read()\n"
                    "if sys.argv[1:3] == ['show', 'chart'] and not os.environ.get('PUBLISHED'): sys.exit(1)\n"
                    "if sys.argv[1] == os.environ.get('FAIL_COMMAND'): sys.exit(1)\n"
                )
                helm.chmod(0o755)
            # Without helm, PATH holds only the empty temp dir: the script must stop
            # at its helm check, which needs nothing but shell builtins.
            path = f"{root}:{os.environ['PATH']}" if with_helm else str(root)
            env = {"PATH": path, "CALL_LOG": str(root / "calls"),
                   "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "Owner/Project",
                   "GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "main",
                   "GITHUB_ACTOR": "ci-user", "GITHUB_TOKEN": "test-password"}
            if published:
                env["PUBLISHED"] = "1"
            env.update(overrides or {})
            result = subprocess.run(["/bin/sh", str(SCRIPT)], env=env, text=True, capture_output=True)
            log = root / "calls"
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            self.assertNotIn("test-password", result.stdout + result.stderr + json.dumps(calls))
            return result, [c["argv"] for c in calls], calls

    def test_default_branch_packages_and_pushes_to_ghcr(self):
        result, calls, raw = self.run_publisher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c[:2] for c in calls],
                         [["registry", "login"], ["show", "chart"], ["package", str(CHART.parent)], ["push", calls[3][1]]])
        self.assertEqual(calls[0][2:], ["ghcr.io", "--username", "ci-user", "--password-stdin"])
        self.assertEqual(calls[1][2:], ["oci://ghcr.io/owner/project/charts/testbench", "--version", VERSION])
        self.assertTrue(calls[3][1].endswith(f"/testbench-{VERSION}.tgz"))
        self.assertEqual(calls[3][2], "oci://ghcr.io/owner/project/charts")
        # Credentials go to a temporary registry config, never the checkout or $HOME.
        config = raw[0]["registry_config"]
        self.assertTrue(config.endswith("/registry.json"))
        self.assertFalse(config.startswith(str(SCRIPT.parents[2])))
        self.assertFalse(Path(config).exists())

    def test_existing_version_is_not_overwritten(self):
        result, calls, _ = self.run_publisher(published=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already published", result.stdout)
        self.assertFalse(any(c[0] in ("package", "push") for c in calls))

    def test_matching_release_tag(self):
        result, calls, _ = self.run_publisher({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": f"v{VERSION}"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1][0], "push")

    def test_mismatched_release_tag_fails_before_login(self):
        result, calls, _ = self.run_publisher({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.0.1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match chart version", result.stderr)
        self.assertEqual(calls, [])

    def test_failures_stop_publish(self):
        for command in ["registry", "package", "push"]:
            with self.subTest(command=command):
                result, calls, _ = self.run_publisher({"FAIL_COMMAND": command})
                self.assertNotEqual(result.returncode, 0)
                if command != "push":
                    self.assertFalse(any(c[0] == "push" for c in calls))

    def test_requires_github_actions_and_helm(self):
        result, calls, _ = self.run_publisher({"GITHUB_ACTIONS": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        result, calls, _ = self.run_publisher(with_helm=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("helm is required", result.stderr)

    def test_workflow_publishes_chart_after_images(self):
        workflow = (SCRIPT.parents[2] / ".github/workflows/publish-images.yml").read_text()
        job = workflow.split("  publish-chart:", 1)[1]
        self.assertIn("needs: publish", job)
        self.assertIn("packages: write", job)
        self.assertIn("sh scripts/ci/publish-chart.sh", job)


if __name__ == "__main__":
    unittest.main()
