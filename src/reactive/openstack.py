from typing import TYPE_CHECKING, Any, Mapping, Optional
from str2bool import str2bool
from charmhelpers.core import hookenv
from charms.reactive import (
    hook,
    when_all,
    when_any,
    when_not,
    is_flag_set,
    toggle_flag,
    set_flag,
    clear_flag,
)
from charms.reactive.relations import endpoint_from_name

from charms import layer

if TYPE_CHECKING:
    from loadbalancer_interface.schemas.v1 import (
        Request as LBRequest,
        Response as LBResponse,
    )

SUPPORTED_LB_PROTOS = ["udp", "tcp"]
SUPPORTED_LB_ALGS = ["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"]
SUPPORTED_LB_HC_PROTOS = ["http", "https", "tcp"]


@when_all("snap.installed.openstackclients")
def set_app_ver():
    version = layer.snap.get_installed_version("openstackclients")
    hookenv.application_version_set(version)


@when_any(
    "config.changed.credentials",
    "config.changed.auth-url",
    "config.changed.username",
    "config.changed.password",
    "config.changed.domain-name",
    "config.changed.domain-id",
    "config.changed.project-name",
    "config.changed.project-id",
    "config.changed.user-domain-name",
    "config.changed.user-domain-id",
    "config.changed.project-domain-name",
    "config.changed.project-domain-id",
    "config.changed.region",
    "config.changed.endpoint-tls-ca",
)
def update_creds():
    clear_flag("charm.openstack.creds.set")


@when_any(
    "config.changed.http-proxy",
    "config.changed.https-proxy",
    "config.changed.no-proxy",
    "config.changed.web-proxy-enable",
)
def update_proxy():
    clear_flag("charm.openstack.proxy.set")


@hook("upgrade-charm")
def upgrade_charm():
    # when the charm is upgraded, recheck the creds in case anything
    # has changed or we want to handle any of the fields differently
    clear_flag("charm.openstack.creds.set")
    clear_flag("charm.openstack.proxy.set")


@hook("update-status")
def update_status():
    # need to recheck creds in case the credentials from Juju have changed
    clear_flag("charm.openstack.creds.set")
    clear_flag("charm.openstack.proxy.set")


@hook("pre-series-upgrade")
def pre_series_upgrade():
    layer.status.blocked("Series upgrade in progress")


@when_not("charm.openstack.proxy.set")
def analyze_proxy():
    settings, updated = layer.openstack.cached_openstack_proxied(), False

    clients = endpoint_from_name("clients")
    for request in clients.all_requests:
        if request.proxy_config != settings:
            updated = True
    if updated:
        layer.status.maintenance("Clients proxy settings changed")
        set_flag("charm.openstack.proxy.changed")

    set_flag("charm.openstack.proxy.set")


@when_not("charm.openstack.creds.set")
def get_creds():
    prev_creds = layer.openstack.get_credentials()
    credentials_exist = layer.openstack.update_credentials()
    toggle_flag("charm.openstack.creds.set", credentials_exist)
    creds = layer.openstack.get_credentials()
    if creds != prev_creds:
        set_flag("charm.openstack.creds.changed")


@when_all("snap.installed.openstackclients", "charm.openstack.creds.set")
@when_not("endpoint.clients.requests-pending")
@when_not("upgrade.series.in-progress")
def no_requests():
    layer.status.active("Ready")


def lb_manage_security_groups(config: Mapping[str, Any]) -> Optional[bool]:
    """Returns if the charm supports Managed Security Groups."""
    manage_security_groups = config["manage-security-groups"]
    if not isinstance(manage_security_groups, bool):
        manage_security_groups = str2bool(manage_security_groups)
    return manage_security_groups


@when_all(
    "snap.installed.openstackclients",
    "charm.openstack.creds.set",
    "endpoint.clients.joined",
)
@when_any(
    "endpoint.clients.requests-pending",
    "config.changed",
    "charm.openstack.creds.changed",
    "charm.openstack.proxy.changed",
)
@when_not("upgrade.series.in-progress")
def handle_requests():
    layer.status.maintenance("Granting integration requests")
    clients = endpoint_from_name("clients")
    config = hookenv.config()
    has_octavia = layer.openstack.detect_octavia()
    if (manage_security_groups := lb_manage_security_groups(config)) is None:
        layer.status.blocked(f"Invalid value for config {manage_security_groups=}")
        return

    settings = layer.openstack.cached_openstack_proxied()
    config_change = is_flag_set("config.changed")
    creds_changed = is_flag_set("charm.openstack.creds.changed")
    proxy_changed = is_flag_set("charm.openstack.proxy.changed")
    refresh_requests = config_change or creds_changed or proxy_changed
    requests = clients.all_requests if refresh_requests else clients.new_requests
    for request in requests:
        layer.status.maintenance("Granting request for {}".format(request.unit_name))
        creds = layer.openstack.get_credentials()
        request.set_proxy_config(settings)
        request.set_credentials(**creds)
        request.set_lbaas_config(
            config["subnet-id"],
            config["floating-network-id"],
            config["lb-method"],
            manage_security_groups,
            has_octavia,
            lb_enabled=config["lb-enabled"],
            internal_lb=config["internal-lb"],
        )

        def _or_none(val):
            if val in (None, "", "null"):
                return None
            else:
                return val

        request.set_block_storage_config(
            _or_none(config.get("bs-version")),
            _or_none(config.get("trust-device-path")),
            _or_none(config.get("ignore-volume-az")),
        )
        layer.openstack.log("Finished request for {}", request.unit_name)
    clients.mark_completed()
    clear_flag("charm.openstack.creds.changed")
    clear_flag("charm.openstack.proxy.changed")


@when_all("charm.openstack.creds.set", "credentials.connected")
@when_not("upgrade.series.in-progress")
def write_credentials():
    credentials = endpoint_from_name("credentials")
    reformatted_creds = layer.openstack.get_creds_and_reformat()
    credentials.expose_credentials(reformatted_creds)


def allow_lb_consumers_to_read_requests():
    lb_consumers = endpoint_from_name("lb-consumers")
    lb_consumers.follower_perms(read=True)
    return lb_consumers


def _lb_algo(request):
    """
    Choose a supported algorithm for the request.
    """
    if not hasattr(request, "algorithm") or not request.algorithm:
        return hookenv.config()["lb-method"]
    for supported in SUPPORTED_LB_ALGS:
        if supported in request.algorithm:
            return supported
    return None


def _validate_loadbalancer_request(request: "LBRequest") -> "LBResponse":
    """
    Validate the incoming request.
    """
    response = request.response
    error_fields = response.error_fields = {}
    if not request.public:
        error_fields["public"] = "Only support public loadbalancers"

    if request.protocol.value not in SUPPORTED_LB_PROTOS:
        error_fields["protocol"] = "Must be one of: {}".format(
            ", ".join(SUPPORTED_LB_PROTOS)
        )

    if not _lb_algo(request):
        error_fields["algorithm"] = "Must be one of: {}".format(
            ", ".join(SUPPORTED_LB_ALGS)
        )

    if request.tls_termination:
        error_fields["tls_termination"] = "Not yet supported"

    for i, hc in enumerate(request.health_checks):
        if hc.protocol.value not in SUPPORTED_LB_HC_PROTOS:
            error_fields["hc[{}].protocol".format(i)] = "Must be one of: {}".format(
                ", ".join(SUPPORTED_LB_PROTOS)
            )
        if hc.path and hc.protocol.value not in ("http", "https"):
            error_fields["hc[{}].path".format(i)] = "Only valid with http(s) protocol"

    remote_port: Optional[int] = None
    config = hookenv.config()
    lb_port = int(config["lb-port"])
    if request.port_mapping:
        if len(request.port_mapping) > 1:
            hookenv.log(
                "Multiple port mappings are specified in the request.\n"
                + f"{request.port_mapping=}",
                hookenv.WARNING,
            )

        # NOTE(Hue): For compatibility reasons, we only respect a single
        # port mapping. we will first try to find the port mapping for the
        # configured `lb-port`.
        # If that fails, we will use on from the port_mapping dict.

        # Try finding the port mapping for the configured `lb-port`.
        remote_port = request.port_mapping.get(lb_port)

        # If the port mapping for the configured `lb-port` is not found,
        # use one from the port_mapping dict.
        if not remote_port:
            lb_port, remote_port = next(iter(request.port_mapping.items()))
            hookenv.log(
                f"No port mapping found for the configured `lb-port`. "
                f"Defaulting to the requested port pair {lb_port=} {remote_port=}",
                hookenv.INFO,
            )

    if request.backends and (not remote_port or not lb_port):
        error_fields["port_mapping"] = (
            f"Invalid port mapping, {lb_port=}, {remote_port=}"
        )

    # Overwrite the port mapping with the one we found
    # so that when we come out of the validation we have a single valid port mapping.
    if lb_port and remote_port:
        request.port_mapping = {int(lb_port): int(remote_port)}

    if error_fields:
        hookenv.log("Unsupported features: {}".format(error_fields), hookenv.ERROR)
    return response


@when_all("charm.openstack.creds.set", "endpoint.loadbalancer.joined")
@when_not("upgrade.series.in-progress")
def manage_loadbalancers_via_loadbalancer():
    layer.status.maintenance("Managing load balancers")
    config = hookenv.config()
    lb_port = str(config["lb-port"])
    lb_clients = endpoint_from_name("loadbalancer")
    try:
        for request in lb_clients.requests:
            if not request.members:
                continue
            lb = layer.openstack.manage_loadbalancer(
                request.application_name,
                request.members,
                lb_port,
                _lb_algo(request),
                "loadbalancer",
            )
            request.set_address_port(lb.fip or lb.address, lb.port)
    except layer.openstack.OpenStackError as e:
        layer.status.blocked(str(e))


@when_all("charm.openstack.creds.set")
@when_any(
    "endpoint.lb-consumers.requests_changed",
    "config.changed",
    "charm.openstack.creds.changed",
    "charm.openstack.proxy.changed",
)
@when_not("upgrade.series.in-progress")
def manage_loadbalancers_via_lb_consumers():
    layer.status.maintenance("Managing load balancers")
    lb_consumers = allow_lb_consumers_to_read_requests()
    errors = []
    config_change = is_flag_set("config.changed")
    creds_changed = is_flag_set("charm.openstack.creds.changed")
    proxy_changed = is_flag_set("charm.openstack.proxy.changed")
    refresh_requests = config_change or creds_changed or proxy_changed
    requests = (
        lb_consumers.all_requests if refresh_requests else lb_consumers.new_requests
    )
    for request in requests:
        response = _validate_loadbalancer_request(request)
        if response.error_fields:
            lb_consumers.send_response(request)
            continue

        lb_port, remote_port = next(iter(request.port_mapping.items()))
        try:
            members = [(addr, remote_port) for addr in request.backends]
            lb = layer.openstack.manage_loadbalancer(
                request.name, members, lb_port, _lb_algo(request), "lb-consumers"
            )
            response.address = lb.fip or lb.address
            response.error = None
            response.error_message = ""
        except layer.openstack.OpenStackError as e:
            response.error = response.error_types.provider_error
            response.error_message = str(e)
            errors.append(str(e))
        lb_consumers.send_response(request)
    if errors:
        layer.status.blocked(", ".join(errors))


@hook("stop")
def cleanup():
    layer.status.maintenance("Cleaning load balancers")
    for _, cached_info in layer.openstack.get_all_cached_lbs().items():
        lb = layer.openstack.LoadBalancer.load_from_cached(cached_info)
        lb.delete()
        hookenv.log("loadbalancer '{}' was deleted".format(lb.name))
