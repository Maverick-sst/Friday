const SYMBOLS: Record<string, string> = { INR: '₹', USD: '$', EUR: '€' }

export function formatMinor(minor: number | null | undefined, currency = 'INR'): string {
  if (minor === null || minor === undefined) return '—'
  const symbol = SYMBOLS[currency] ?? ''
  const major = minor / 100
  const formatted = major.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
  return `${symbol}${formatted}`
}

export function timeOf(iso: string | null | undefined): string {
  if (!iso) return '--:--:--'
  return new Date(iso).toLocaleTimeString('en-GB', { hour12: false })
}

export function dateTimeOf(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', { hour12: false })
}
