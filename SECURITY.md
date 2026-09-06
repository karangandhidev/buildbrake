# Security

## Supported version

Security fixes currently target the latest `0.1.x` release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do
not open a public issue containing credentials, private source code, or an
unpatched exploit. Include the affected version, reproduction steps, impact,
and any suggested mitigation.

## Local trust model

BuildBrake runs locally with the permissions of the user who starts it. The
dashboard binds to `127.0.0.1` by default and receipts remain under the target
project's Git-ignored `.buildbrake/` directory. Do not bind the dashboard to a
public interface on an untrusted network.

BuildBrake launches the user's own Codex CLI. Codex authentication and usage
belong to that user; BuildBrake does not collect or transmit credentials.
Task prompts and project context are sent by Codex according to the user's
Codex configuration and OpenAI account settings.
