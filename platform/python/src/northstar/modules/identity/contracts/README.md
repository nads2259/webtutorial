# Identity module contracts

Module-owned contract shapes for the identity capabilities (LAW-11, rule 40). These describe the
public request/response payloads the module accepts through the kernel command/query buses. They
are *module-local* schemas; promotion to the global contract registry (`spec/contracts/`) is a
governance step (`GATE-CONTRACT`) and is out of scope for this task.

| Capability | Kind | Payload schema |
| --- | --- | --- |
| `identity.authentication.begin` | command | `schemas/authentication.schema.json` (`BeginAuthentication`) |
| `identity.authentication.complete` | command | `schemas/authentication.schema.json` (`CompleteAuthentication`) |
| `identity.session.describe` | query | `schemas/authentication.schema.json` (`SessionView`) |

Security notes (docs/07 §3-4, rule 50):

- Browser auth is OAuth 2.0 **Authorization Code + PKCE** (`S256`); `state`, `nonce`, `issuer` and
  `audience` are validated on the callback.
- Sessions are **server-managed**: the client only ever holds an opaque token in an `HttpOnly`,
  `Secure`, `SameSite` cookie. No long-lived browser token or JWT-in-cookie is issued. The store
  persists only the SHA-256 of the token, never the raw value.
- Authentication responses are **uniform** (anti-enumeration): failures never reveal which
  accounts/tokens exist.
