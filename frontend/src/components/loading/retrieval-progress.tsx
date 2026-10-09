type RetrievalProgressProps = {
  eyebrow: string;
  title: string;
  description: string;
  status: string;
  detail: string;
  ariaLabel: string;
  progressLabel?: string;
  progress?: {
    total: number;
    loaded: number;
    remaining: number;
    percent: number;
  } | null;
  progressUnit?: string;
};

/** Full-screen, indeterminate progress shown while a view retrieves its data. */
export function RetrievalProgress({
  eyebrow,
  title,
  description,
  status,
  detail,
  ariaLabel,
  progressLabel = ariaLabel,
  progress = null,
  progressUnit = "items",
}: RetrievalProgressProps) {
  const isDeterminate = progress !== null && (progress.total > 0 || progress.percent === 100);
  const isComplete = progress?.percent === 100;
  return (
    <div className="retrieval-progress" role="status" aria-live="polite" aria-label={ariaLabel}>
      <div className="retrieval-progress-card">
        <div className="retrieval-progress-heading">
          <span className="retrieval-progress-icon" aria-hidden="true"><span /></span>
          <div>
            <span className="sms-eyebrow">{eyebrow}</span>
            <strong>{title}</strong>
            <p>{description}</p>
          </div>
        </div>
        <div
          className={`retrieval-progress-track${isDeterminate ? " determinate" : ""}`}
          role="progressbar"
          aria-label={progressLabel}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={isDeterminate ? progress.percent : undefined}
        >
          <span style={isDeterminate ? { width: `${progress.percent}%` } : undefined} />
        </div>
        <div className="retrieval-progress-meta">
          <span><i aria-hidden="true" />{isComplete ? "Retrieval complete" : status}</span>
          <small>
            {isDeterminate
              ? `${progress.total.toLocaleString()} ${progressUnit} total · ${progress.loaded.toLocaleString()} loaded · ${progress.remaining.toLocaleString()} remaining`
              : detail}
          </small>
        </div>
      </div>
    </div>
  );
}
