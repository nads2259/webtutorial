import type { InputHTMLAttributes, ReactNode } from "react";
import { useId } from "react";

type NativeInputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "id" | "aria-invalid" | "aria-describedby"
>;

export interface FieldProps extends NativeInputProps {
  label: ReactNode;
  /** Optional error text; when present the input is marked invalid and described by it. */
  error?: string;
  /** Optional helper/instructions text (WCAG 3.3.2). */
  description?: string;
}

/**
 * Labelled form field with programmatic label + error association.
 * Covers WCAG 1.3.1 (Info and Relationships), 3.3.1 (Error Identification),
 * 3.3.2 (Labels or Instructions) and 4.1.2 (Name, Role, Value).
 */
export function Field({ label, error, description, ...inputProps }: FieldProps): React.JSX.Element {
  const inputId = useId();
  const errorId = useId();
  const descriptionId = useId();

  const describedBy = [description ? descriptionId : null, error ? errorId : null]
    .filter(Boolean)
    .join(" ");

  return (
    <div>
      <label htmlFor={inputId}>{label}</label>
      {description ? <span id={descriptionId}>{description}</span> : null}
      <input
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy.length > 0 ? describedBy : undefined}
        {...inputProps}
      />
      {error ? (
        <p id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
