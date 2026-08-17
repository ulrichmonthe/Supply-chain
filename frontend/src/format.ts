export const MODE_COLOUR: Record<string, string> = {
  road: '#6ea8d8',
  sea: '#46c5b6',
  air: '#e8955a',
  river: '#8fbf6a',
  foot: '#b98cf0',
  drone: '#d98ec4',
}

export function modeColour(mode: string): string {
  return MODE_COLOUR[mode] ?? '#8fa3b8'
}

export function money(value: number | null | undefined, currency = ''): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const abs = Math.abs(value)
  let text: string
  if (abs >= 1_000_000) text = `${(value / 1_000_000).toFixed(2)}M`
  else if (abs >= 1_000) text = `${(value / 1_000).toFixed(0)}k`
  else if (abs >= 100) text = value.toFixed(0)
  // Per-capita figures live in single digits, and the difference between 1.8 and
  // 34.6 per person is the entire point of the equity panel. Rounding it away makes
  // every quintile look identical.
  else if (abs >= 10) text = value.toFixed(1)
  else text = value.toFixed(2)
  return currency ? `${text} ${currency}` : text
}

export function exact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function signedPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const text = `${(value * 100).toFixed(digits)}%`
  return value > 0 ? `+${text}` : text
}

/** Format a KPI according to its declared unit so the scorecard stays comparable. */
export function formatKpi(value: number | null | undefined, unit: string, currency = ''): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  switch (unit) {
    case 'percent':
      return pct(value)
    case 'currency':
      return money(value, currency)
    case 'count':
      return exact(value)
    case 'm3':
      return `${exact(value)} m³`
    case 'm3-km':
      return `${money(value)} m³·km`
    default:
      return exact(value)
  }
}

export function riskColour(risk: number): string {
  if (risk >= 0.5) return '#ef6b6b'
  if (risk >= 0.2) return '#e8b23a'
  if (risk >= 0.05) return '#c9c15a'
  return '#3fb984'
}

export function fillColour(fill: number): string {
  if (fill >= 0.99) return '#3fb984'
  if (fill >= 0.9) return '#8fbf6a'
  if (fill >= 0.6) return '#e8b23a'
  if (fill > 0) return '#e8955a'
  return '#ef6b6b'
}

export const FREQUENCIES = [
  'DAILY',
  'TWICE_WEEKLY',
  'WEEKLY',
  'FORTNIGHTLY',
  'MONTHLY',
  'SIX_WEEKLY',
  'QUARTERLY',
  'BIANNUAL',
]

export function frequencyLabel(frequency: string | null | undefined): string {
  if (!frequency) return 'on demand'
  return frequency.toLowerCase().replace(/_/g, ' ')
}
