"""Dev-only mock IdP login page that makes the real OIDC Authorization-Code + PKCE flow runnable.

This is NOT part of the identity core. It stands in for the browser-facing IdP login screen so the
standard flow (`/auth/login` -> IdP -> `/auth/callback`) works end-to-end on a laptop without a live
identity provider. It is mounted ONLY when ``NORTHSTAR_DEV_IDP=1``. A real deployment points the
``OidcProviderPort`` at a configured IdP and never mounts this router.

The mock ``authorize`` endpoint receives the PKCE ``code_challenge`` + ``nonce`` + ``state`` +
``redirect_uri`` as query params (exactly as :meth:`MockOidcProvider.build_authorization_url` encodes
them), renders a tiny sign-in form, and on submit asks the SAME wired provider to
:meth:`issue_code`, then redirects back to the app's ``redirect_uri`` with ``code`` + ``state``.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

_STATE_KEY = "northstar_mock_idp_dependencies"


@dataclass(frozen=True, slots=True)
class MockIdpDependencies:
    """The wired mock provider + default identity, injected at the composition root."""

    provider: object  # MockOidcProvider (duck-typed: issue_code)
    default_email: str = "learner@bestinfopages.local"


def bind_mock_idp_dependencies(app_state: object, deps: MockIdpDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> MockIdpDependencies:
    return getattr(request.app.state, _STATE_KEY)


def create_mock_idp_router() -> APIRouter:
    router = APIRouter(prefix="/auth/mock-idp", tags=["identity-dev"])

    @router.get("/authorize")
    def authorize(request: Request) -> HTMLResponse:
        q = request.query_params
        fields = {
            "state": q.get("state", ""),
            "nonce": q.get("nonce", ""),
            "code_challenge": q.get("code_challenge", ""),
            "code_challenge_method": q.get("code_challenge_method", "S256"),
            "redirect_uri": q.get("redirect_uri", "/api/auth/callback"),
        }
        default_email = _deps(request).default_email
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}" />'
            for k, v in fields.items()
        )
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Sign in - Bestinfopages</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0f1117; color:#e7e9f1;
    display:grid; place-items:center; min-height:100vh; margin:0; }}
  .card {{ background:#171a22; border:1px solid #262a36; border-radius:16px; padding:32px;
    width:min(92vw,380px); box-shadow:0 24px 64px rgba(0,0,0,.4); }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  p {{ color:#9aa1b2; font-size:14px; margin:0 0 20px; }}
  label {{ display:block; font-size:13px; margin:0 0 6px; color:#c8ccd6; }}
  input[type=email] {{ width:100%; box-sizing:border-box; padding:11px 12px; border-radius:10px;
    border:1px solid #333949; background:#0f1117; color:#fff; font-size:15px; }}
  button {{ margin-top:18px; width:100%; padding:12px; border:0; border-radius:10px; cursor:pointer;
    font-weight:700; font-size:15px; color:#fff;
    background:linear-gradient(120deg,#4f46e5,#7c3aed); }}
  .brand {{ font-weight:800; background:linear-gradient(120deg,#6366f1,#8b5cf6);
    -webkit-background-clip:text; background-clip:text; color:transparent; }}
</style></head>
<body>
  <form class="card" method="post" action="login">
    <h1><span class="brand">Bestinfopages</span> sign in</h1>
    <p>Development identity provider. Enter any email to continue.</p>
    <label for="email">Email</label>
    <input id="email" type="email" name="email" value="{html.escape(default_email)}" required />
    {hidden}
    <button type="submit">Continue</button>
  </form>
</body></html>"""
        return HTMLResponse(content=page, status_code=200)

    @router.post("/login")
    async def login(request: Request) -> RedirectResponse:
        # Parse application/x-www-form-urlencoded manually (avoids a python-multipart dependency).
        raw = (await request.body()).decode("utf-8")
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        email = str(form.get("email") or _deps(request).default_email).strip()
        state = str(form.get("state") or "")
        nonce = str(form.get("nonce") or "")
        code_challenge = str(form.get("code_challenge") or "")
        method = str(form.get("code_challenge_method") or "S256")
        redirect_uri = str(form.get("redirect_uri") or "/api/auth/callback")
        provider = _deps(request).provider
        code = provider.issue_code(  # type: ignore[attr-defined]
            code_challenge=code_challenge,
            nonce=nonce or None,
            subject=email,
            code_challenge_method=method,
            email=email,
            email_verified=True,
        )
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(url=f"{redirect_uri}{sep}code={code}&state={state}", status_code=303)

    return router
