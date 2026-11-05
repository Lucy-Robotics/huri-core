# Security

HuRI can drive hardware near people. A security or safety flaw is reported in
private, not in a public issue or pull request, until a fix is released.

## What to report this way

- a way around the safety defaults: running a module the server does not list,
  actuating without the operator's second consent (`HURI_ACTUATION_MODULES`),
  loading a module whose manifest or version range fails the startup checks;
- a way for a client or a module to reach what it should not: another session,
  the server's host, the secrets;
- any other vulnerability in huri-core, huri-modules (the curated modules, the
  hub, the installer, the Docker images, the Helm chart) or HuRI-CLI.

A flaw in a module of another repository goes to its authors; if the module is
verified (listed in huri-modules' `registry.toml`), tell us too.

## How to report

Use **Report a vulnerability**, in the Security tab of the repository
concerned:
[huri-core](https://github.com/Lucy-Robotics/huri-core/security),
[huri-modules](https://github.com/Lucy-Robotics/huri-modules/security) or
[HuRI-CLI](https://github.com/Lucy-Robotics/HuRI-CLI/security). Not sure which:
use huri-core.

Say what is affected (repository, version or commit), how to reproduce it and
what it allows.

## What happens then

The maintainers answer within 7 days, fix the flaw in a private security
advisory, release the fix, then publish the advisory, crediting you unless you
prefer otherwise. Fixes go to the latest release only.
