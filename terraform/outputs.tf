# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "app_name" {
  description = "Name of the deployed application."
  value       = juju_application.openstack_integrator.name
}

output "provides" {
  value = {
    clients = "clients"
    credentials = "credentials"
    lb_consumers = "lb-consumers"
    # intentionally skipping deprecated loadbalancer relation
  }
}
