export function truncateWords(text: string, limit: number): string {
  const words = text.trim().split(/\s+/);
  return words.length <= limit ? text : `${words.slice(0, limit).join(" ")}...`;
}

export function formatValue(value: unknown): string {
  return typeof value === "object" && value !== null
    ? JSON.stringify(value)
    : String(value);
}

function relative(seconds: number): string {
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}

export function timeAgo(iso: string): string {
  return relative(-Math.round((Date.now() - new Date(iso).getTime()) / 1000));
}

export function timeUntil(iso: string): string {
  return relative(Math.round((new Date(iso).getTime() - Date.now()) / 1000));
}
