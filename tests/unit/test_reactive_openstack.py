import pytest

from reactive.openstack import lb_manage_security_groups


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
