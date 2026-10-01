/**
 * Insert a blank row (all series null) between two consecutive rows more than
 * maxGapSeconds apart, so the chart breaks the line where nothing was polled
 * instead of drawing a straight ramp across the gap. Rows must be sorted by t
 * (epoch ms).
 */
export function withGaps<T extends { t: number }>(rows: T[], maxGapSeconds: number, blank: (t: number) => T): T[] {
  const out: T[] = []
  for (const row of rows) {
    const previous = out.at(-1)
    if (previous && row.t - previous.t > maxGapSeconds * 1000) out.push(blank(previous.t + 1))
    out.push(row)
  }
  return out
}
