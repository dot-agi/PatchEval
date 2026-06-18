"""Docker-free tests that the work-container/image-pull helpers forward the
platform from ``DOCKER_DEFAULT_PLATFORM``.

The docker-py SDK does NOT auto-read ``DOCKER_DEFAULT_PLATFORM`` -- the
``platform=`` kwarg must be passed explicitly -- so ``docker_platform`` from the
config (which is exported to that env var) would otherwise be silently ignored
for claudecode container creation and image pulls.

These tests stub ``docker.from_env`` with a fake client whose ``containers.run``
and ``images.pull`` record their kwargs. No real Docker daemon is touched. They
assert the platform is forwarded as ``linux/amd64`` when the env var is set, and
as ``None`` (native) when it is unset.
"""
from contextlib import nullcontext

import docker
import pytest

import patcheval.docker_utils as du


class _FakeContainer:
    """Stand-in for a docker-py Container that looks healthy."""

    status = "running"
    id = "0123456789abcdef"

    def reload(self):  # pragma: no cover - status is already "running"
        pass


class _FakeContainers:
    def __init__(self, recorder):
        self._recorder = recorder

    def run(self, **kwargs):
        self._recorder["run_kwargs"] = kwargs
        return _FakeContainer()

    def get(self, name):
        # Exercised by stop_container(); pretend nothing pre-exists so it takes
        # its NotFound "nothing to do" branch instead of touching a daemon.
        raise docker.errors.NotFound(f"no such container: {name}")


class _FakeImages:
    def __init__(self, recorder):
        self._recorder = recorder

    def pull(self, image, **kwargs):
        self._recorder["pull_args"] = (image, kwargs)
        return object()


class _FakeClient:
    def __init__(self, recorder):
        self.containers = _FakeContainers(recorder)
        self.images = _FakeImages(recorder)


@pytest.fixture
def recorder(monkeypatch):
    """Patch docker.from_env -> fake client and silence the stabilize sleep."""
    rec = {}
    monkeypatch.setattr(du.docker, "from_env", lambda: _FakeClient(rec))
    monkeypatch.setattr(du.time, "sleep", lambda *a, **k: None)
    return rec


def test_run_work_container_forwards_platform(recorder, monkeypatch):
    monkeypatch.setenv("DOCKER_DEFAULT_PLATFORM", "linux/amd64")

    cid = du.run_work_container_no_mount(
        "img:latest", "CVE-2025-0001", nullcontext(), "model"
    )

    assert cid == _FakeContainer.id
    assert recorder["run_kwargs"]["platform"] == "linux/amd64"


def test_run_work_container_platform_none_when_unset(recorder, monkeypatch):
    monkeypatch.delenv("DOCKER_DEFAULT_PLATFORM", raising=False)

    du.run_work_container_no_mount(
        "img:latest", "CVE-2025-0001", nullcontext(), "model"
    )

    assert recorder["run_kwargs"]["platform"] is None


def test_pull_image_forwards_platform(recorder, monkeypatch):
    monkeypatch.setenv("DOCKER_DEFAULT_PLATFORM", "linux/amd64")

    du.pull_image_with_retry("img:latest", nullcontext())

    image, kwargs = recorder["pull_args"]
    assert image == "img:latest"
    assert kwargs["platform"] == "linux/amd64"


def test_pull_image_platform_none_when_unset(recorder, monkeypatch):
    monkeypatch.delenv("DOCKER_DEFAULT_PLATFORM", raising=False)

    du.pull_image_with_retry("img:latest", nullcontext())

    _image, kwargs = recorder["pull_args"]
    assert kwargs["platform"] is None
