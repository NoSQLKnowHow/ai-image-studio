import { useEffect, useId, useState } from "react";

interface Props {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  integer?: boolean;
  disabled?: boolean;
  hint?: string;
  onChange: (value: number) => void;
}

/** A number input that lets you type freely (even briefly invalid text) and only commits valid values. */
export function NumberField({ label, value, min, max, step = 1, integer = true, disabled, hint, onChange }: Props) {
  const id = useId();
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);

  const parsed = Number(text);
  const valid = text.trim() !== "" && Number.isFinite(parsed) && (!integer || Number.isInteger(parsed)) && parsed >= min && parsed <= max;
  const error = valid ? null : `Enter ${integer ? "a whole number" : "a number"} from ${min} to ${max}.`;

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="number"
        inputMode={integer ? "numeric" : "decimal"}
        min={min}
        max={max}
        step={step}
        value={text}
        disabled={disabled}
        aria-invalid={!valid}
        aria-describedby={error || hint ? `${id}-help` : undefined}
        onChange={(e) => {
          setText(e.target.value);
          const next = Number(e.target.value);
          if (e.target.value.trim() !== "" && Number.isFinite(next) && (!integer || Number.isInteger(next)) && next >= min && next <= max) {
            onChange(next);
          }
        }}
        onBlur={() => setText(String(value))}
      />
      {(error || hint) && (
        <p id={`${id}-help`} className={error ? "field-error" : "field-hint"}>
          {error ?? hint}
        </p>
      )}
    </div>
  );
}
