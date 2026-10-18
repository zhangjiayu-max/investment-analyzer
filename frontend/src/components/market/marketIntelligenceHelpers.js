export function isFreshMarketIntelligence(result, today = new Date().toISOString().slice(0, 10)) {
  const fetchedAt = result?.fetched_at
  return typeof fetchedAt === 'string' && fetchedAt.slice(0, 10) === today
}
