import { useState } from "react";
import { api, ApiError, type RunResult } from "../api/client";

export interface CodeRunnerProps {
  initialCode: string;
  language: string;
  lessonId?: string | null;
  blockId?: string | null;
  authenticated: boolean;
}

/**
 * An editable code cell with a Run button. Running dispatches to the server sandbox
 * (`POST /codelab/runs`), which executes under resource limits and durably tracks the run.
 */
export function CodeRunner({
  initialCode,
  language,
  lessonId,
  blockId,
  authenticated,
}: CodeRunnerProps): React.JSX.Element {
  const [code, setCode] = useState(initialCode);
  const [result, setResult] = useState<RunResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(): Promise<void> {
    setRunning(true);
    setError(null);
    try {
      const r = await api.runCode({ code, language, lesson_id: lessonId, block_id: blockId });
      setResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to run");
    } finally {
      setRunning(false);
    }
  }

  const runnable = language === "python";

  return (
    <div className="bip-runner">
      <div className="bip-runner__bar">
        <span className="bip-runner__lang">{language}</span>
        {runnable ? (
          <button
            type="button"
            className="bip-runner__run"
            onClick={run}
            disabled={running || !authenticated}
            title={authenticated ? "Run this code" : "Sign in to run code"}
          >
            {running ? "Running…" : "Run"}
          </button>
        ) : null}
      </div>
      <textarea
        className="bip-runner__code"
        value={code}
        spellCheck={false}
        onChange={(e) => setCode(e.target.value)}
        rows={Math.min(Math.max(code.split("\n").length, 3), 24)}
        aria-label={`Editable ${language} code`}
      />
      {!authenticated && runnable ? (
        <p className="bip-runner__hint">
          <a href={api.loginUrl()}>Sign in</a> to run code and track your runs.
        </p>
      ) : null}
      {error ? <p className="bip-runner__error">{error}</p> : null}
      {result ? (
        <div
          className={`bip-runner__out bip-runner__out--${result.outcome}`}
          role="group"
          aria-label="Run output"
        >
          <div className="bip-runner__status">
            {result.outcome} · exit {result.exit_code} · {result.duration_ms} ms
            {result.timed_out ? " · timed out" : ""}
          </div>
          {result.stdout ? <pre className="bip-runner__stdout">{result.stdout}</pre> : null}
          {result.stderr ? <pre className="bip-runner__stderr">{result.stderr}</pre> : null}
          {!result.stdout && !result.stderr ? (
            <pre className="bip-runner__stdout">(no output)</pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
