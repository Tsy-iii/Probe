import type { Evidence } from './api'

export interface AssertionComparison { expression: string; expected: number; actual: number }

// 只使用本次保存的 pytest 输出。仅支持能明确对应到“表达式 == 整数常量”
// 的整数比较；变量预期、反向表达式、重复预期值等不明确情况保留原始错误。
export function assertionComparisons(details: string): AssertionComparison[] {
  const lines = details.split('\n')
  const sources = lines.filter(line => /^\s*>\s+assert\b/.test(line))
  if (sources.length !== 1) return []
  const expressionPattern = /([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[(?:['"][A-Za-z_]\w*['"]|\d+)\])*)\s*==\s*(-?\d+)(?![\w.])(?=\s*(?:and\b|or\b|,|#|$|\)))/g
  const expressions = [...sources[0].matchAll(expressionPattern)]
    .map(match => ({ expression: match[1], expected: Number(match[2]) }))
    .filter(item => Number.isSafeInteger(item.expected))
  const summary = lines.map(line => line.trim().replace(/^E\s+/, '')).find(line =>
    /^assert\b/.test(line) && /(?<![\w.])-?\d+\s*==\s*-?\d+(?![\w.])/.test(line))
  if (!summary) return []
  const pairs = [...summary.matchAll(/(?<![\w.])(-?\d+)\s*==\s*(-?\d+)(?![\w.])/g)]
    .map(match => ({ actual: Number(match[1]), expected: Number(match[2]) }))
    .filter(pair => Number.isSafeInteger(pair.actual) && Number.isSafeInteger(pair.expected) && pair.actual !== pair.expected)
  const comparisons: AssertionComparison[] = []
  for (const { actual, expected } of pairs) {
    if (pairs.filter(pair => pair.expected === expected).length !== 1) continue
    const candidates = expressions.filter(item => item.expected === expected)
    if (candidates.length !== 1) continue
    const expression = candidates[0].expression
    if (!comparisons.some(item => item.expression === expression && item.actual === actual && item.expected === expected)) comparisons.push({ expression, expected, actual })
  }
  return comparisons
}

// 只有字段名、实际值和响应内容能唯一对应，才把失败断言关联到请求表。
// 多个请求或多个断言都能匹配时，保留独立失败信息，不覆盖采集值。
export function linkAssertionChecks(evidence: Evidence[], comparisons: AssertionComparison[]): Record<number, AssertionComparison>[] {
  const links = evidence.map(() => ({} as Record<number, AssertionComparison>))
  const candidates: { evidenceIndex: number; checkIndex: number; comparison: AssertionComparison }[] = []
  for (const comparison of comparisons) {
    const field = comparison.expression.match(/\[['"]([A-Za-z_]\w*)['"]\]$/)?.[1] ?? comparison.expression.match(/\.([A-Za-z_]\w*)$/)?.[1]
    if (!field) continue
    const matches: { evidenceIndex: number; checkIndex: number }[] = []
    evidence.forEach((item, evidenceIndex) => item.checks.forEach((check, checkIndex) => {
      if (check.actual !== comparison.actual) return
      const path = check.name.split('.')
      const bodyValue = path.reduce<unknown>((body, key) => body && typeof body === 'object' && !Array.isArray(body) ? (body as Record<string, unknown>)[key] : undefined, item.response.body)
      const statusMatch = field === 'status_code' && check.name === 'HTTP 状态码' && item.response.status_code === comparison.actual
      if (statusMatch || (path.at(-1) === field && bodyValue === comparison.actual)) matches.push({ evidenceIndex, checkIndex })
    }))
    if (matches.length === 1) candidates.push({ ...matches[0], comparison })
  }
  for (const candidate of candidates) {
    if (candidates.filter(item => item.evidenceIndex === candidate.evidenceIndex && item.checkIndex === candidate.checkIndex).length === 1) links[candidate.evidenceIndex][candidate.checkIndex] = candidate.comparison
  }
  return links
}
