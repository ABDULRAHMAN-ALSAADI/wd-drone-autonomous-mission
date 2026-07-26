# Security Policy

## Reporting A Vulnerability

Do not open a public issue for a vulnerability that could enable unsafe vehicle
control, expose credentials or private flight data, or bypass a safety check.

Use GitHub's private vulnerability reporting feature for this repository. If it
is not enabled, contact the repository owner privately through their GitHub
profile and share only enough detail to establish a secure reporting channel.

Include:

- the affected branch and commit;
- the relevant file or subsystem;
- a minimal reproduction that does not endanger people or hardware;
- the possible impact;
- any suggested mitigation.

Do not test a suspected vulnerability on an armed aircraft. Use unit tests,
simulation, or a propeller-off bench setup.

## Supported Versions

Security and safety fixes are applied to the actively maintained branches.
Older commits and personal forks are not supported.

## Sensitive Data

Do not commit tokens, passwords, SSH keys, `.env` files, private location data,
raw telemetry, or identifiable camera footage. If sensitive data enters Git
history, rotate the credential or protect the affected data immediately; a
normal follow-up commit does not remove it from history.
