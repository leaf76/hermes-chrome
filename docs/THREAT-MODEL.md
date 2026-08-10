# Hermes Chrome — threat model

## What we protect

- **Local control plane** for daily Chrome (open tabs, capture, light DOM).
- **Token** in `~/.hermes/run/hermes-chrome/bridge.env` (treat like a password to this machine’s browser control).
- **Workspace isolation** by default (agent Tab Group; sensitive ops stay in-group).

## Trust assumptions

| Trusted | Not trusted |
|---------|-------------|
| You (OS login on this machine) | Random websites |
| Agent CLI / MCP you intentionally run | Other local users (if multi-user machine) |
| Official extension id (`mkoaoadlkijccmmbkioagnlngbbeocfa`) | Arbitrary other Chrome extensions |
| Official GitHub install scripts | Unofficial forks posing as “official” |

**Same-user local malware** that can read `bridge.env` or inject into your session can control the bridge. That is outside the product boundary (same as any local agent tool).

## Assets

| Asset | Location |
|-------|----------|
| Bridge token | `~/.hermes/run/hermes-chrome/bridge.env` (chmod 600) |
| Extension storage token | Chrome extension storage (device) |
| Agent tabs / captures | Local only; not uploaded by Hermes |
| Open source code | GitHub — no secrets |

## Attack surfaces & controls

| Surface | Control |
|---------|---------|
| Bind address | Default `127.0.0.1` only; non-local requires `HERMES_CHROME_BRIDGE_ALLOW_NONLOCAL=1` |
| Auth | Token ON by default; constant-time compare; header `X-Hermes-Chrome-Token` |
| Query `?token=` | **Off** by default; enable only with `HERMES_CHROME_ALLOW_QUERY_TOKEN=1` |
| CORS / pairing Origin | **Allowlisted** official extension id (+ `HERMES_CHROME_ALLOWED_EXTENSION_IDS`) |
| Pairing window | Time-limited; one-shot; loopback only |
| Auto-reopen pairing | **Off** by default; `HERMES_CHROME_AUTO_REPAIR=1` to enable |
| `/v1/health` | Public = liveness only; full detail needs token |
| Download / check-url | Block private/link-local hosts by default (`policy.py`) |
| eval | ISOLATED world by default; MAIN opt-in |
| Workspace | Sensitive ops require Hermes group unless Options opt-in |
| ALLOW_NO_AUTH | Explicit footgun; not default |

## Out of scope

- Protecting against a fully compromised OS user account
- DRM / anti-fork of open source
- Remote multi-tenant SaaS isolation

## Operator checklist

1. Do not set `HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH=1` on shared machines.
2. Do not commit `bridge.env` or paste tokens into issues.
3. Prefer official CWS extension + official GitHub install.
4. After first pair, use stored token; run `pair-open` only when re-linking.
5. Close agent workspace with `hermes-chrome stop` when done (does not remove token).

## Related

- [GUIDE.md](./GUIDE.md) — setup and daily use  
- [PACKAGING.md](./PACKAGING.md) — release channels  
- `store/UPLOAD_GUIDE.md` — CWS permission justifications  
