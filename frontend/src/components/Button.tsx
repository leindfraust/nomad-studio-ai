type Props = {
  label: string;
  onClick?: () => void;
  variant?: "primary" | "secondary";
  loading?: boolean;
  disabled?: boolean;
};

/**
 * One button for every async action, so a pending request is always visible.
 *
 * The label does not change while loading: the spinner appears beside it and the
 * button keeps its width, so the row does not jump under the pointer.
 */
export function Button({ label, onClick, variant, loading = false, disabled = false }: Props) {
  return (
    <button
      type="button"
      className={variant === "primary" ? "primary" : undefined}
      onClick={onClick}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      data-loading={loading ? "true" : undefined}
    >
      {loading ? <span className="spinner" aria-hidden="true" /> : null}
      <span>{label}</span>
      {loading ? <span className="sr-only"> in progress</span> : null}
    </button>
  );
}
