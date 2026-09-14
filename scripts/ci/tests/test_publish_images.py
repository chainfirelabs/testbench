import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "publish-images.sh"
SHA = "a" * 40


class PublisherTests(unittest.TestCase):
    def run_publisher(self, overrides=None, gitlab=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['CALL_LOG'], 'a') as f:\n"
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if sys.argv[1] == 'login': sys.stdin.read()\n"
                "if sys.argv[1] == os.environ.get('FAIL_COMMAND'): sys.exit(1)\n"
            )
            docker.chmod(0o755)
            env = {"PATH": f"{root}:{os.environ['PATH']}", "CALL_LOG": str(root / "calls")}
            if gitlab:
                env.update(GITLAB_CI="true", CI_REGISTRY="registry.gitlab.example",
                           CI_REGISTRY_IMAGE="registry.gitlab.example/group/project",
                           CI_COMMIT_SHA=SHA, CI_PROJECT_URL="https://gitlab.example/group/project",
                           CI_REGISTRY_USER="ci-user", CI_REGISTRY_PASSWORD="test-password")
            else:
                env.update(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="Owner/Project",
                           GITHUB_SHA=SHA, GITHUB_SERVER_URL="https://github.com",
                           GITHUB_REF_TYPE="branch", GITHUB_REF_NAME="main",
                           GITHUB_ACTOR="ci-user", GITHUB_TOKEN="test-password")
            env.update(overrides or {})
            result = subprocess.run(["sh", str(SCRIPT)], env=env, text=True, capture_output=True)
            calls = [json.loads(line) for line in (root / "calls").read_text().splitlines()] if (root / "calls").exists() else []
            self.assertNotIn("test-password", result.stdout + result.stderr + json.dumps(calls))
            return result, calls

    def test_defaults_and_build_before_push(self):
        for gitlab, prefix in [(False, "ghcr.io/owner/project"), (True, "registry.gitlab.example/group/project")]:
            with self.subTest(gitlab=gitlab):
                result, calls = self.run_publisher(gitlab=gitlab)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([c[0] for c in calls[:7]], ["login"] + ["build"] * 6)
                pushes = [c[1] for c in calls if c[0] == "push"]
                self.assertEqual(len(pushes), 18)
                for component in ["backend", "frontend", "mcp", "network-scan", "device-info", "reboot"]:
                    self.assertIn(f"{prefix}/{component}:latest", pushes)
                    version = (SCRIPT.parents[2] / "helm/testbench/Chart.yaml").read_text().split('appVersion: "')[1].split('"')[0]
                    self.assertIn(f"{prefix}/{component}:{version}", pushes)
                    self.assertIn(f"{prefix}/{component}:sha-{SHA}", pushes)
                builds = [c for c in calls if c[0] == "build"]
                self.assertTrue(all(f"VERSION={version}" in c for c in builds))

    def test_release_and_prerelease(self):
        for version in ["1.6.7", "1.6.8-rc.1"]:
            result, calls = self.run_publisher({"CI_COMMIT_TAG": f"v{version}"}, gitlab=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            pushes = [c[1] for c in calls if c[0] == "push"]
            self.assertEqual(sum(p.endswith(f":{version}") for p in pushes), 6)
            self.assertFalse(any(p.endswith(":latest") for p in pushes))

    def test_custom_registry(self):
        result, calls = self.run_publisher({"TB_REGISTRY": "internal:5000/", "TB_IMAGE_PREFIX": "internal:5000/mirror/testbench/", "TB_REGISTRY_USER": "robot", "TB_REGISTRY_PASSWORD": "test-password"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0], ["login", "internal:5000", "--username", "robot", "--password-stdin"])
        self.assertTrue(all(c[1].startswith("internal:5000/mirror/testbench/") for c in calls if c[0] == "push"))

    def test_reject_bad_configuration_before_login(self):
        for overrides in [{"TB_REGISTRY": "internal"}, {"TB_REGISTRY": "https://internal"}, {"TB_IMAGE_PREFIX": "another/project"}, {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "vbad/tag"}]:
            result, calls = self.run_publisher(overrides)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls, [])

    def test_failures_stop_publish(self):
        for command in ["login", "build", "push"]:
            result, calls = self.run_publisher({"FAIL_COMMAND": command})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls[-1][0], command)
            self.assertEqual(sum(c[0] == command for c in calls), 1)

    def test_private_mirror_builder_and_authentication(self):
        result, calls = self.run_publisher({
            "TB_BUILDKIT_CONFIG": str(SCRIPT.parent / "buildkitd.toml.example"),
            "TB_BUILDKIT_IMAGE": "registry.example.com/docker/moby/buildkit:buildx-stable-1",
            "TB_MIRROR_REGISTRY": "registry.example.com",
            "TB_MIRROR_USER": "robot$builder", "TB_MIRROR_PASSWORD": "test-password",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1], ["login", "registry.example.com", "--username", "robot$builder", "--password-stdin"])
        create = next(c for c in calls if c[:2] == ["buildx", "create"])
        self.assertIn("image=registry.example.com/docker/moby/buildkit:buildx-stable-1", create)
        builder = create[create.index("--name") + 1]
        builds = [c for c in calls if c[:2] == ["buildx", "build"]]
        self.assertEqual(len(builds), 6)
        for build in builds:
            self.assertIn("--load", build)
            self.assertEqual(build[build.index("--builder") + 1], builder)
        self.assertEqual(calls[-1], ["buildx", "rm", builder])

    def test_builder_failure_prevents_publishing(self):
        result, calls = self.run_publisher({"TB_BUILDKIT_CONFIG": str(SCRIPT.parent / "buildkitd.toml.example"), "TB_BUILDKIT_IMAGE": "registry.example.com/buildkit:stable", "FAIL_COMMAND": "buildx"})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "push" for c in calls))
        self.assertEqual(calls[-1][:2], ["buildx", "rm"])

    def test_tls_dind_uses_explicit_context_and_cleans_up(self):
        for failure in ["", "buildx", "context"]:
            with self.subTest(failure=failure):
                result, calls = self.run_publisher({
                    "TB_BUILDKIT_CONFIG": str(SCRIPT.parent / "buildkitd.toml.example"),
                    "TB_BUILDKIT_IMAGE": "registry.example.com/buildkit:stable",
                    "DOCKER_HOST": "tcp://docker:2376",
                    "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": "/certs/client",
                    "FAIL_COMMAND": failure,
                }, gitlab=True)
                context_create = next(c for c in calls if c[:2] == ["context", "create"])
                context = context_create[2]
                self.assertEqual(calls[-1], ["context", "rm", "-f", context])
                if failure:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(any(c[0] == "push" for c in calls))
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    builder_create = next(c for c in calls if c[:2] == ["buildx", "create"])
                    self.assertEqual(builder_create[-1], context)
                    self.assertLess(calls.index(context_create), calls.index(builder_create))
                    self.assertEqual(calls[-2][:2], ["buildx", "rm"])

    def test_pip_override_is_gitlab_only_and_uses_secret_reference(self):
        private_index = "https://packages.example.com/root/pypi/+simple/"
        for gitlab in [False, True]:
            result, calls = self.run_publisher({"TB_PIP_INDEX_URL": private_index}, gitlab=gitlab)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(private_index, json.dumps(calls) + result.stdout + result.stderr)
            builds = [c for c in calls if c[0] == "build"]
            self.assertEqual(len(builds), 6)
            for build in builds:
                expected = gitlab and not build[-1].endswith("/frontend")
                self.assertEqual("--secret" in build, expected)
                if expected:
                    self.assertEqual(build[build.index("--secret") + 1], "id=pip_index_url,env=TB_PIP_INDEX_URL")

    def test_pip_override_unset_keeps_public_default(self):
        result, calls = self.run_publisher(gitlab=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("--secret" in c for c in calls))

    def test_http_pip_trusted_host_is_gitlab_only(self):
        for gitlab in [False, True]:
            result, calls = self.run_publisher({
                "TB_PIP_INDEX_URL": "http://packages.example.com/root/pypi/+simple/",
                "TB_PIP_TRUSTED_HOST": "packages.example.com",
            }, gitlab=gitlab)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("packages.example.com", json.dumps(calls))
            for build in [c for c in calls if c[0] == "build"]:
                expected = gitlab and not build[-1].endswith("/frontend")
                self.assertEqual("id=pip_trusted_host,env=TB_PIP_TRUSTED_HOST" in build, expected)
        # The trusted host is meaningless without an index override, so it is ignored alone.
        result, calls = self.run_publisher({"TB_PIP_TRUSTED_HOST": "packages.example.com"}, gitlab=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("--secret" in c for c in calls))

    def test_proxy_is_frontend_and_gitlab_only(self):
        proxy = "http://squid.example.com:3128"
        for gitlab in [False, True]:
            with self.subTest(gitlab=gitlab):
                result, calls = self.run_publisher({"TB_HTTP_PROXY": proxy}, gitlab=gitlab)
                self.assertEqual(result.returncode, 0, result.stderr)
                builds = [c for c in calls if c[0] == "build"]
                self.assertEqual(len(builds), 6)
                for build in builds:
                    # Only the frontend build fetches npm and Debian packages.
                    expected = gitlab and build[-1].endswith("/frontend")
                    self.assertEqual("id=http_proxy,env=TB_HTTP_PROXY" in build, expected)
                    self.assertNotIn(proxy, str(build))
        for overrides in [{}, {"TB_HTTP_PROXY": ""}]:
            result, calls = self.run_publisher(overrides, gitlab=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any("id=http_proxy,env=TB_HTTP_PROXY" in c for c in calls))

    def test_frontend_uses_optional_per_command_proxy_secrets(self):
        frontend = (SCRIPT.parents[2] / "src/frontend/Dockerfile").read_text()
        self.assertNotIn("ARG TB_HTTP_PROXY", frontend)
        self.assertNotIn("npm config set", frontend)
        self.assertNotIn("01-testbench-proxy", frontend)
        for variable in ["npm_config_proxy", "npm_config_https_proxy", "http_proxy", "https_proxy"]:
            self.assertIn(f"--mount=type=secret,id=http_proxy,env={variable}", frontend)
        self.assertNotIn("required=true", frontend)

    def test_dockerfiles_consume_the_pip_secrets(self):
        # A secret the Dockerfile never mounts would silently leave pip on PyPI.
        contexts = {"backend": "src/backend", "frontend": "src/frontend", "mcp": "src/mcp-server",
                    "network-scan": "src/plugins/network-scan", "device-info": "src/plugins/device-info",
                    "reboot": "src/plugins/reboot"}
        for component, context in contexts.items():
            with self.subTest(component=component):
                dockerfile = (SCRIPT.parents[2] / context / "Dockerfile").read_text()
                for mount in ["--mount=type=secret,id=pip_index_url,env=PIP_INDEX_URL",
                              "--mount=type=secret,id=pip_trusted_host,env=PIP_TRUSTED_HOST"]:
                    self.assertEqual(mount in dockerfile, component != "frontend")



    def test_github_release_tag(self):
        result, calls = self.run_publisher({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.7.3"})
        self.assertEqual(result.returncode, 0, result.stderr)
        pushes = [c[1] for c in calls if c[0] == "push"]
        self.assertEqual(sum(p.endswith(":1.7.3") for p in pushes), 6)
        self.assertFalse(any(p.endswith(":latest") for p in pushes))


if __name__ == "__main__":
    unittest.main()
