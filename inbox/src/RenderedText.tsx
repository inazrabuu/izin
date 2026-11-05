import type { Segment } from "./api";

export function RenderedText({
  text,
  segments,
}: {
  text: string;
  segments?: Segment[] | null;
}) {
  // Trust segments only if they reproduce the plain text exactly.
  if (!segments?.length || segments.map((s) => s.text).join("") !== text) {
    return <>{text}</>;
  }
  return (
    <>
      {segments.map((s, i) =>
        s.agent ? (
          <mark key={i} className="agent" title="Supplied by the agent">
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}
