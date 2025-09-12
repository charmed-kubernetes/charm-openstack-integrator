# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import random
import string

import pytest
from lightkube import AsyncClient, KubeConfig
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Namespace

log = logging.getLogger(__name__)


def pytest_addoption(parser):
    parser.addoption(
        "--charmed-solution",
        action="store",
        default=None,
        help="Which charmed solution to test (e.g. kubernetes-core, canonical-k8s).",
    )


@pytest.fixture(scope="session")
def charmed_solution(pytestconfig):
    """Return the charmed-solution if specified."""
    return pytestconfig.getoption("charmed_solution")


@pytest.fixture(scope="session")
def control_plane_application(charmed_solution):
    """Return the control plane application name based on the charmed solution."""
    if charmed_solution in ["kubernetes-core", "charmed-kubernetes"]:
        return "kubernetes-control-plane"
    elif charmed_solution == "canonical-kubernetes":
        return "k8s"
    else:
        pytest.skip("No charmed solution specified, skipping tests.")


@pytest.fixture(scope="module")
def module_name(request):
    return request.module.__name__.replace("_", "-")


@pytest.fixture()
async def kubeconfig(ops_test, control_plane_application):
    kubeconfig_path = ops_test.tmp_path / "kubeconfig"
    if kubeconfig_path.exists() and kubeconfig_path.stat().st_size:
        yield kubeconfig_path
        return

    kcp = ops_test.model.applications[control_plane_application]
    action = await kcp.units[0].run_action("get-kubeconfig")
    result = await action.wait()
    completed = result.status == "completed" or result.results["return-code"] == 0
    assert completed, f"Failed to get kubeconfig {result=}"
    kubeconfig_path.parent.mkdir(exist_ok=True, parents=True)
    kubeconfig_path.write_text(result.results["kubeconfig"])
    assert kubeconfig_path.stat().st_size, "kubeconfig file is 0 bytes"
    yield kubeconfig_path


@pytest.fixture()
async def kubernetes(kubeconfig, module_name):
    rand_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    namespace = f"{module_name}-{rand_str}"
    config = KubeConfig.from_file(kubeconfig)
    client = AsyncClient(
        config=config.get(context_name=config.current_context),
        namespace=namespace,
        trust_env=False,
    )
    namespace_obj = Namespace(metadata=ObjectMeta(name=namespace))
    await client.create(namespace_obj)
    yield client
    await client.delete(Namespace, namespace)
