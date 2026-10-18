// 家庭账本按上海时区展示月份，月份运算不经过本地日期和 UTC 转换。
export function shanghaiDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now)
  return `${parts.find(p => p.type === 'year').value}-${parts.find(p => p.type === 'month').value}-${parts.find(p => p.type === 'day').value}`
}
export function shanghaiMonth(now = new Date()) { return shanghaiDate(now).slice(0, 7) }
export function previousMonth(month) {
  const [year, value] = month.split('-').map(Number)
  return value === 1 ? `${year - 1}-12` : `${year}-${String(value - 1).padStart(2, '0')}`
}
