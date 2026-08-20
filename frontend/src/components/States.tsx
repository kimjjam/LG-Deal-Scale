export function LoadingState({ label }: { label: string }) {
  return <div className="loading-state" role="status"><span className="spinner dark" aria-hidden="true" /><span>{label}</span></div>;
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return <div className="empty-state"><span className="empty-icon" aria-hidden="true">—</span><strong>{title}</strong><p>{description}</p></div>;
}
