# Overview

This charm acts as a proxy to OpenStack and provides an [interface][] to provide
a set of credentials for a somewhat limited project user to the applications that
are related to this charm.

## Usage

This charm is a component of Charmed Kubernetes. For full information,
please visit the [official Charmed Kubernetes docs](https://www.ubuntu.com/kubernetes/docs/charm-openstack-integrator).

## Authentication Methods

This charm supports two ways to authenticate against OpenStack:

- Username/password credentials.
- Application credentials (application credential ID or name, plus secret).

When setting credentials via charm config, the auth type is inferred from the
provided credential fields.

Both methods can be provided either with `juju trust` / `credential-get`, or via
charm config using either the `credentials` blob or individual config options.



[interface]: https://github.com/juju-solutions/interface-openstack-integration

