import enum
import pytest
import unittest.mock as mock
import reactive.openstack as charm
import charms.reactive

from charmhelpers.core import hookenv
from charms.layer.openstack import OpenStackError


@mock.patch("reactive.openstack.endpoint_from_name")
@mock.patch("reactive.openstack.layer")
@mock.patch("reactive.openstack.set_flag")
def test_analyze_proxy_currently_unset(set_flag, layer, endpoint_from_name):
    # Test with no proxy settings
    layer.openstack.cached_openstack_proxied.return_value = {}
    client_request = mock.MagicMock()
    client_request.proxy_config = {}
    client_endpoint = endpoint_from_name.return_value
    client_endpoint.all_requests = []
    charm.analyze_proxy()
    endpoint_from_name.assert_called_once_with("clients")
    layer.status.maintenance.assert_not_called()
    set_flag.assert_called_once_with("charm.openstack.proxy.set")


@mock.patch("reactive.openstack.endpoint_from_name")
@mock.patch("reactive.openstack.layer")
@mock.patch("reactive.openstack.set_flag")
def test_analyze_proxy_matched_settings(set_flag, layer, endpoint_from_name):
    # Test with matching proxy settings
    settings = {
        "HTTP_PROXY": "http://proxy.example.com:8080",
        "HTTPS_PROXY": "https://proxy.example.com:8080",
        "NO_PROXY": "127.0.0.1,localhost,::1",
    }
    settings.update({k.lower(): v for k, v in settings.items()})
    client_request = mock.MagicMock()
    client_request.proxy_config = settings
    client_endpoint = endpoint_from_name.return_value
    client_endpoint.all_requests = [client_request]
    layer.openstack.cached_openstack_proxied.return_value = settings
    charm.analyze_proxy()
    endpoint_from_name.assert_called_once_with("clients")
    layer.status.maintenance.assert_not_called()
    set_flag.assert_called_once_with("charm.openstack.proxy.set")


@mock.patch("reactive.openstack.endpoint_from_name")
@mock.patch("reactive.openstack.layer")
@mock.patch("reactive.openstack.set_flag")
def test_analyze_proxy_unmatched_settings(set_flag, layer, endpoint_from_name):
    # Test with updated proxy settings
    settings = {
        "HTTP_PROXY": "http://proxy.example.com:8080",
        "HTTPS_PROXY": "https://proxy.example.com:8080",
        "NO_PROXY": "127.0.0.1,localhost,::1",
    }
    client_request = mock.MagicMock()
    client_request.proxy_config = {**settings, "NO_PROXY": ""}
    client_endpoint = endpoint_from_name.return_value
    client_endpoint.all_requests = [client_request]
    layer.openstack.cached_openstack_proxied.return_value = settings
    charm.analyze_proxy()
    layer.status.maintenance.assert_called_once_with("Clients proxy settings changed")
    endpoint_from_name.assert_called_once_with("clients")
    set_flag.assert_any_call("charm.openstack.proxy.changed")
    set_flag.assert_any_call("charm.openstack.proxy.set")


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("yes", True),
        ("invalid", None),
    ],
)
def test_lb_secgroup_valid(value, expected):
    config = {"manage-security-groups": value}
    assert charm.lb_manage_security_groups(config) is expected


def test_validate_loadbalancer_request_no_errors():
    request = mock.MagicMock()
    request.protocol.value = "tcp"
    request.algorithm = "ROUND_ROBIN"
    request.tls_termination = False
    request.health_checks = []
    request.port_mapping = {80: 8080}
    request.backends = None
    hookenv.config.return_value = {
        "lb-port": 443,
    }
    response = charm._validate_loadbalancer_request(request)
    assert response is request.response
    assert response.error_fields == {}


class TestProtocol(enum.Enum):
    http = "http"


@pytest.mark.parametrize(
    "field, value, error_msg",
    [
        ("public", None, "Only support public loadbalancers"),
        (
            "protocol",
            TestProtocol.http,
            "Must be one of: udp, tcp",
        ),
        (
            "algorithm",
            "INVALID",
            "Must be one of: ROUND_ROBIN, LEAST_CONNECTIONS, SOURCE_IP",
        ),
        ("tls_termination", True, "Not yet supported"),
        (
            "health_checks",
            [mock.MagicMock(protocol=mock.MagicMock(value="ftp"))],
            {
                "hc[0].path": "Only valid with http(s) protocol",
                "hc[0].protocol": "Must be one of: udp, tcp",
            },
        ),
        (
            "backends",
            ["invalid_ip"],
            {
                "port_mapping": "Invalid port mapping, lb_port=0, remote_port=None",
            },
        ),
    ],
)
def test_validate_loadbalancer_request_error(field, value, error_msg):
    request = mock.MagicMock()
    request.protocol.value = "tcp"
    request.algorithm = "ROUND_ROBIN"
    request.tls_termination = False
    request.health_checks = []
    request.port_mapping = {}
    request.backends = None
    hookenv.config.return_value = {
        "lb-port": 0,
    }

    setattr(request, field, value)
    response = charm._validate_loadbalancer_request(request)
    assert response is request.response
    if isinstance(error_msg, dict):
        assert response.error_fields == error_msg
    else:
        assert response.error_fields == {field: error_msg}


@mock.patch.object(charm, "_validate_loadbalancer_request")
@mock.patch("charms.layer.openstack.manage_loadbalancer")
@pytest.mark.parametrize("failure_message", ["", "Failed to allocate floating IP"])
def test_manage_loadbalancer_via_lb_consumers(
    manage_loadbalancer, validate, failure_message
):
    lb_consumers = charms.reactive.relations.endpoint_from_name.return_value
    lb_consumers.send_response.reset_mock()
    request = mock.MagicMock(name="req-1")
    response = request.response
    lb_consumers.new_requests = [request]

    validate.side_effect = lambda req: req.response
    response.error_fields = {}
    request.port_mapping = {443: 6443}
    request.backends = ["1.2.3.4"]
    request.algorithm = "ROUND_ROBIN"

    if failure_message:
        manage_loadbalancer.side_effect = OpenStackError(failure_message)
        charm.manage_loadbalancers_via_lb_consumers()
        validate.assert_called_once_with(request)
        assert response.error == response.error_types.provider_error
        assert response.error_message == failure_message
        lb_consumers.send_response.assert_called_once_with(request)

    else:
        lb = manage_loadbalancer.return_value
        charm.manage_loadbalancers_via_lb_consumers()
        validate.assert_called_once_with(request)
        assert response.address == lb.fip
        assert response.error is None
        assert response.error_message == ""
        lb_consumers.send_response.assert_called_once_with(request)
