from typing import TYPE_CHECKING, Any, Mapping, Optional
import json
import subprocess
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

OPENSTACKCLIENTS_READY_FLAG = "charm.openstackclients.ready"


def _normalize_snap_channel(channel: str) -> str:
    channel = (channel or "stable").strip()
    if "/" not in channel:
        return f"latest/{channel}"
    return channel


def _openstackclients_snap_info() -> Optional[Mapping[str, str]]:
    result = subprocess.run(
        ("snap", "list", "openstackclients"),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return None

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    for line in lines[1:]:
        parts = line.split()
        if parts and parts[0] == "openstackclients":
            return {
                "version": parts[1] if len(parts) > 1 else "",
                "tracking": parts[3] if len(parts) > 3 else "",
            }
    return None


def _openstackclients_snap_installed(channel: Optional[str] = None) -> bool:
    info = _openstackclients_snap_info()
    if not info:
        return False
    if channel is None:
        return True
    tracking = info.get("tracking", "")
    return _normalize_snap_channel(tracking) == _normalize_snap_channel(channel)


if TYPE_CHECKING:
    from loadbalancer_interface.schemas.v1 import (
        Request as LBRequest,
        Response as LBResponse,
    )

SUPPORTED_LB_PROTOS = ["udp", "tcp", "https"]
SUPPORTED_LB_ALGS = ["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"]
SUPPORTED_LB_HC_PROTOS = ["ping", "http", "https", "tls-hello", "udp-connect", "sctp"]


def _parse_additional_cloud_conf_options(config: Mapping[str, Any]):
    """Parse and validate additional-cloud-conf-options JSON config."""
    raw = config.get("additional-cloud-conf-options")
    if raw in (None, "", "null"):
        return None, None

    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        return None, f"Invalid JSON for config additional-cloud-conf-options: {e}"

    if not isinstance(parsed, dict):
        return (
            None,
            "Invalid value for config additional-cloud-conf-options: "
            "expected a JSON object",
        )

    for section, values in parsed.items():
        if not isinstance(section, str) or not section:
            return (
                None,
                "Invalid value for config additional-cloud-conf-options: "
                "section names must be non-empty strings",
            )
        if not isinstance(values, dict):
            return (
                None,
                "Invalid value for config additional-cloud-conf-options: "
                "section values must be JSON objects",
            )
        for key in values.keys():
            if not isinstance(key, str) or not key:
                return (
                    None,
                    "Invalid value for config additional-cloud-conf-options: "
                    "option names must be non-empty strings",
                )

    return parsed, None


def _parse_use_octavia(config: Mapping[str, Any], detected: bool) -> Optional[bool]:
    """Return effective has_octavia value from config and detection."""
    value = config.get("use-octavia", "auto")
    if isinstance(value, str):
        value = value.strip().lower()
    if value in (None, "", "auto"):
        return detected
    if isinstance(value, bool):
        return value
    parsed = str2bool(value)
    return parsed


def _request_credentials_payload(creds: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Return only fields accepted by interface-openstack-integration set_credentials.
    """
    keys = [
        "auth_url",
        "region",
        "username",
        "password",
        "user_domain_name",
        "project_domain_name",
        "project_name",
        "endpoint_tls_ca",
        "domain_id",
        "domain_name",
        "project_id",
        "project_domain_id",
        "user_domain_id",
        "version",
        "application_credential_id",
        "application_credential_name",
        "application_credential_secret",
        "auth_type",
    ]
    return {k: creds.get(k) for k in keys}


@when_not(OPENSTACKCLIENTS_READY_FLAG)
@when_not("upgrade.series.in-progress")
def ensure_openstackclients_snap():
    """Ensure openstackclients snap is installed and mark the custom ready flag."""
    channel = hookenv.config()["openstackclients-snap-channel"]
    if _openstackclients_snap_installed(channel):
        set_flag(OPENSTACKCLIENTS_READY_FLAG)
        return

    if _openstackclients_snap_installed():
        layer.status.maintenance(f"Refreshing openstackclients snap to {channel}")
        subprocess.run(
            ("snap", "refresh", "openstackclients", "--channel", channel),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    else:
        layer.status.maintenance(f"Installing openstackclients snap from {channel}")
        subprocess.run(
            ("snap", "install", "openstackclients", "--classic", "--channel", channel),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    set_flag(OPENSTACKCLIENTS_READY_FLAG)


@when_all(OPENSTACKCLIENTS_READY_FLAG)
def set_app_ver():
    """Set app version from installed openstackclients snap when channel matches."""
    channel = hookenv.config()["openstackclients-snap-channel"]
    if not _openstackclients_snap_installed(channel):
        return
    info = _openstackclients_snap_info()
    if info and info.get("version"):
        hookenv.application_version_set(info["version"])


@when_all(OPENSTACKCLIENTS_READY_FLAG)
@when_any("config.changed.openstackclients-snap-channel")
def refresh_openstackclients_snap_channel():
    """Refresh openstackclients snap to the configured channel if changed."""
    channel = hookenv.config()["openstackclients-snap-channel"]
    if _openstackclients_snap_installed(channel):
        set_flag(OPENSTACKCLIENTS_READY_FLAG)
        return
    layer.status.maintenance(f"Refreshing openstackclients snap to {channel}")
    subprocess.run(
        ("snap", "refresh", "openstackclients", "--channel", channel),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    set_flag(OPENSTACKCLIENTS_READY_FLAG)


@when_any(
    "config.changed.credentials",
    "config.changed.auth-url",
    "config.changed.username",
    "config.changed.password",
    "config.changed.application-credential-id",
    "config.changed.application-credential-name",
    "config.changed.application-credential-secret",
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


@when_all(OPENSTACKCLIENTS_READY_FLAG, "charm.openstack.creds.set")
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
    OPENSTACKCLIENTS_READY_FLAG,
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
    detected_octavia = layer.openstack.detect_octavia()
    has_octavia = _parse_use_octavia(config, detected_octavia)
    if has_octavia is None:
        layer.status.blocked("Invalid value for config use-octavia")
        return
    if (manage_security_groups := lb_manage_security_groups(config)) is None:
        layer.status.blocked(f"Invalid value for config {manage_security_groups=}")
        return
    additional_cloud_conf_options, error = _parse_additional_cloud_conf_options(config)
    if error:
        layer.status.blocked(error)
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
        request.set_credentials(**_request_credentials_payload(creds))
        request.set_lbaas_config(
            config["subnet-id"],
            config["floating-network-id"],
            config["lb-method"],
            manage_security_groups,
            has_octavia,
            lb_enabled=config["lb-enabled"],
            internal_lb=config["internal-lb"],
        )

        # Preserve compatibility with the reactive interface while allowing
        # newer consumers to pick up additional options directly from relation data.
        if hasattr(request, "_to_publish"):
            request._to_publish.update(
                {
                    "member_subnet_id": config.get("member-subnet-id") or None,
                    "create_monitor": config.get("create-monitor"),
                    "monitor_delay": config.get("monitor-delay") or None,
                    "monitor_timeout": config.get("monitor-timeout") or None,
                    "monitor_max_retries": config.get("monitor-max-retries"),
                    "node_selector": config.get("node-selector") or None,
                    "internal_network_name": config.get("internal-network-name")
                    or None,
                    "public_network_name": config.get("public-network-name") or None,
                    "lb_flavor_id": config.get("lb-flavor-id") or None,
                    "key_id": config.get("key-id") or None,
                    "verify_ssl": config.get("verify-ssl"),
                    "tls_insecure": config.get("tls-insecure"),
                    "verify": config.get("verify"),
                    "additional_cloud_conf_options": additional_cloud_conf_options,
                }
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


def _lb_proto(request):
    """
    Choose a supported protocol for the request.
    """
    if not hasattr(request, "protocol") or not request.protocol:
        return None
    if request.protocol.value not in SUPPORTED_LB_PROTOS:
        return None
    return request.protocol.value.upper()


def _validate_loadbalancer_request(request: "LBRequest") -> "LBResponse":
    """
    Validate the incoming request.
    """
    response = request.response
    error_fields = response.error_fields = {}
    if not request.public:
        error_fields["public"] = "Only support public loadbalancers"

    if not _lb_proto(request):
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
        if i > 0:
            error_fields[f"hc[{i}]"] = "Only supports up to 1 health check"
        if hc.protocol.value not in SUPPORTED_LB_HC_PROTOS:
            error_fields[f"hc[{i}].protocol"] = "Must be one of: {}".format(
                ", ".join(SUPPORTED_LB_HC_PROTOS)
            )
        if hc.path and hc.protocol.value not in ("http", "https"):
            error_fields[f"hc[{i}].path"] = "Only valid with http(s) protocol"

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
                _lb_proto(request),
                endpoint_name="loadbalancer",
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
                request.name,
                members,
                lb_port,
                _lb_algo(request),
                _lb_proto(request),
                request.health_checks[0] if request.health_checks else None,
                "lb-consumers",
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
