# Security policy

BrainGate is a privacy control: a bug that lets personal data reach an external provider is a security issue.

**Please do not open public issues for vulnerabilities.** Report them privately through
[GitHub Security Advisories](https://github.com/rvst312/brain-gate/security/advisories/new).

Examples of in-scope reports:

- A request shape that reaches `provider.send` with unmasked fields
- Input text appearing in logs, error bodies or exceptions
- Network access during model loading or inference
- A policy bypass (an integration using a policy it isn't allowed to use)

Never include real personal data in a report. Reproduce with synthetic values.
