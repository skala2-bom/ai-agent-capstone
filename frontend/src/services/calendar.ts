export function dateKey(d: Date) {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}
export function calendarDays(anchor: string, view: "month" | "week") {
  const d = new Date(anchor + "T00:00:00Z");
  if (view === "month") d.setUTCDate(1);
  d.setUTCDate(d.getUTCDate() - d.getUTCDay());
  return Array.from({ length: view === "month" ? 42 : 7 }, (_, i) => {
    const n = new Date(d);
    n.setUTCDate(d.getUTCDate() + i);
    return dateKey(n);
  });
}
export function movePeriod(
  anchor: string,
  view: "month" | "week",
  direction: number,
) {
  const d = new Date(anchor + "T00:00:00Z");
  if (view === "month") {
    d.setUTCDate(1);
    d.setUTCMonth(d.getUTCMonth() + direction);
  } else d.setUTCDate(d.getUTCDate() + 7 * direction);
  return dateKey(d);
}
