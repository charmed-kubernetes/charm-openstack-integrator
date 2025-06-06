import pytest
import unittest.mock as mock
from reactive.openstack import analyze_proxy, lb_manage_security_groups


@mock.patch("reactive.openstack.endpoint_from_name")
@mock.patch("reactive.openstack.layer")
@mock.patch("reactive.openstack.set_flag")
def test_analyze_proxy_currently_unset(set_flag, layer, endpoint_from_name):
    # Test with no proxy settings
    layer.openstack.current_proxy_settings.return_value = {}
    client_request = mock.MagicMock()
    client_request.proxy_config = {}
    client_endpoint = endpoint_from_name.return_value
    client_endpoint.all_requests = []
    analyze_proxy()
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
    layer.openstack.current_proxy_settings.return_value = settings
    analyze_proxy()
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
    layer.openstack.current_proxy_settings.return_value = settings
    analyze_proxy()
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
    assert lb_manage_security_groups(config) is expected
