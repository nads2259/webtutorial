import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { navigate } from "../lib/nav";

type AuthView = "login" | "register" | "forgot" | "reset" | "confirm";

export interface AuthPagesProps {
  view: AuthView;
  /** Called after a successful login so the app can refresh the session. */
  onAuthed: () => void;
}

function tokenFromUrl(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("token") ?? "";
}

function intercept(e: React.MouseEvent<HTMLDivElement>): void {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
  const anchor = (e.target as HTMLElement).closest("a");
  const href = anchor?.getAttribute("href");
  if (!anchor || !href || !href.startsWith("/") || href.startsWith("/api")) return;
  e.preventDefault();
  navigate(href);
}

function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="bip-auth" onClickCapture={intercept}>
      <div className="bip-auth__card">
        <a className="bip-auth__brand" href="/">
          Bestinfopages
        </a>
        <h1 className="bip-auth__title">{title}</h1>
        {subtitle ? <p className="bip-auth__subtitle">{subtitle}</p> : null}
        {children}
      </div>
    </div>
  );
}

function Field({
  label,
  type,
  value,
  onChange,
  autoComplete,
  autoFocus,
}: {
  label: string;
  type: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete?: string;
  autoFocus?: boolean;
}): React.JSX.Element {
  return (
    <label className="bip-auth__field">
      <span>{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        // biome-ignore lint/a11y/noAutofocus: focusing the first field is expected on auth pages
        autoFocus={autoFocus}
        required
      />
    </label>
  );
}

function Alert({ kind, children }: { kind: "error" | "info"; children: React.ReactNode }) {
  return (
    <p className={`bip-auth__alert bip-auth__alert--${kind}`} role="alert">
      {children}
    </p>
  );
}

function LoginPage({ onAuthed }: { onAuthed: () => void }): React.JSX.Element {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needConfirm, setNeedConfirm] = useState(false);
  const [info, setInfo] = useState<string | null>(null);

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);
    setNeedConfirm(false);
    try {
      await api.login(email, password);
      onAuthed();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setNeedConfirm(true);
        setError("Please confirm your email address before signing in.");
      } else {
        setError(err instanceof ApiError ? err.message : "Sign in failed.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function resend(): Promise<void> {
    await api.resendConfirmation(email).catch(() => undefined);
    setInfo("Confirmation email re-sent. Check your inbox.");
  }

  return (
    <AuthShell title="Sign in" subtitle="Welcome back to Bestinfopages.">
      <form className="bip-auth__form" onSubmit={submit}>
        {error ? <Alert kind="error">{error}</Alert> : null}
        {info ? <Alert kind="info">{info}</Alert> : null}
        <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
        <Field
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
        />
        <button type="submit" className="bip-auth__submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {needConfirm ? (
          <button type="button" className="bip-auth__link-btn" onClick={resend}>
            Resend confirmation email
          </button>
        ) : null}
      </form>
      <div className="bip-auth__links">
        <a href="/forgot-password">Forgot password?</a>
        <a href="/register">Create an account</a>
      </div>
    </AuthShell>
  );
}

function RegisterPage(): React.JSX.Element {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await api.register(email, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Registration failed.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <AuthShell title="Check your email" subtitle={`We sent a confirmation link to ${email}.`}>
        <p className="bip-auth__note">
          Click the link in that email to activate your account, then sign in.
        </p>
        <div className="bip-auth__links">
          <a href="/login">Back to sign in</a>
          <button
            type="button"
            className="bip-auth__link-btn"
            onClick={() => api.resendConfirmation(email)}
          >
            Resend email
          </button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Create your account" subtitle="Learn Python by doing — free.">
      <form className="bip-auth__form" onSubmit={submit}>
        {error ? <Alert kind="error">{error}</Alert> : null}
        <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
        <Field
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
        />
        <Field
          label="Confirm password"
          type="password"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
        />
        <button type="submit" className="bip-auth__submit" disabled={busy}>
          {busy ? "Creating…" : "Create account"}
        </button>
      </form>
      <div className="bip-auth__links">
        <a href="/login">Already have an account? Sign in</a>
      </div>
    </AuthShell>
  );
}

function ForgotPage(): React.JSX.Element {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setBusy(true);
    await api.forgotPassword(email).catch(() => undefined);
    setBusy(false);
    setDone(true);
  }

  if (done) {
    return (
      <AuthShell
        title="Check your email"
        subtitle={`If an account exists for ${email}, we've sent a password-reset link.`}
      >
        <div className="bip-auth__links">
          <a href="/login">Back to sign in</a>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Reset your password" subtitle="We'll email you a reset link.">
      <form className="bip-auth__form" onSubmit={submit}>
        <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
        <button type="submit" className="bip-auth__submit" disabled={busy}>
          {busy ? "Sending…" : "Send reset link"}
        </button>
      </form>
      <div className="bip-auth__links">
        <a href="/login">Back to sign in</a>
      </div>
    </AuthShell>
  );
}

function ResetPage(): React.JSX.Element {
  const [token] = useState(tokenFromUrl);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await api.resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reset failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthShell title="Invalid reset link" subtitle="This link is missing its token.">
        <div className="bip-auth__links">
          <a href="/forgot-password">Request a new link</a>
        </div>
      </AuthShell>
    );
  }

  if (done) {
    return (
      <AuthShell title="Password updated" subtitle="You can now sign in with your new password.">
        <div className="bip-auth__links">
          <a href="/login">Go to sign in</a>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Choose a new password">
      <form className="bip-auth__form" onSubmit={submit}>
        {error ? <Alert kind="error">{error}</Alert> : null}
        <Field
          label="New password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          autoFocus
        />
        <Field
          label="Confirm password"
          type="password"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
        />
        <button type="submit" className="bip-auth__submit" disabled={busy}>
          {busy ? "Updating…" : "Update password"}
        </button>
      </form>
    </AuthShell>
  );
}

function ConfirmPage(): React.JSX.Element {
  const [status, setStatus] = useState<"pending" | "ok" | "error">("pending");
  const [message, setMessage] = useState("");
  // Guard against React StrictMode double-invocation consuming the single-use token twice.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const token = tokenFromUrl();
    if (!token) {
      setStatus("error");
      setMessage("This confirmation link is missing its token.");
      return;
    }
    api
      .confirmEmail(token)
      .then(() => setStatus("ok"))
      .catch((err) => {
        setStatus("error");
        setMessage(err instanceof ApiError ? err.message : "Confirmation failed.");
      });
  }, []);

  if (status === "pending") {
    return <AuthShell title="Confirming your email…" subtitle="One moment." />;
  }
  if (status === "ok") {
    return (
      <AuthShell title="Email confirmed" subtitle="Your account is now active.">
        <div className="bip-auth__links">
          <a href="/login">Sign in</a>
        </div>
      </AuthShell>
    );
  }
  return (
    <AuthShell title="Confirmation failed" subtitle={message}>
      <div className="bip-auth__links">
        <a href="/login">Back to sign in</a>
      </div>
    </AuthShell>
  );
}

export function AuthPages({ view, onAuthed }: AuthPagesProps): React.JSX.Element {
  switch (view) {
    case "register":
      return <RegisterPage />;
    case "forgot":
      return <ForgotPage />;
    case "reset":
      return <ResetPage />;
    case "confirm":
      return <ConfirmPage />;
    default:
      return <LoginPage onAuthed={onAuthed} />;
  }
}
