import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const components = ['QcReview', 'PcaReview', 'ClusterReview', 'MarkerReview', 'PcaDimensions'] as const
const controlTags = new Set(['Button', 'button', 'Input', 'input', 'Textarea', 'textarea', 'select'])

function readSource(relativePath: string): string {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8')
}

function tagAt(source: string, index: number): string | null {
  const start = source.lastIndexOf('<', index)
  if (start === -1 || source.slice(start, index).includes('>')) return null
  return source.slice(start + 1, index).match(/^([A-Za-z][\w.]*)/)?.[1] ?? null
}

function lineAt(source: string, index: number): string {
  const start = source.lastIndexOf('\n', index) + 1
  const end = source.indexOf('\n', index)
  return source.slice(start, end === -1 ? undefined : end)
}

function inlineAuthorizationViolations(source: string): string[] {
  const violations: string[] = []
  for (const match of source.matchAll(/disabled=\{([^}]*)\}/g)) {
    const tag = tagAt(source, match.index)
    if (!tag || !controlTags.has(tag)) continue
    const expression = match[1].trim().replace(/\s+/g, ' ')
    const line = lineAt(source, match.index)
    const gateDerived = /^!(?:[A-Za-z]\w*Gate|gate)\.(?:available|allowed)$/.test(expression)
    const readOnlyRefresh = expression === 'busy' && line.includes('只读刷新')
    const candidatePaging = expression.includes('offset') && line.includes('候选')
    if (!gateDerived && !readOnlyRefresh && !candidatePaging) {
      violations.push(`${tag} disabled={${expression}}`)
    }
  }
  return violations
}

function blockerContract() {
  const stageGateSource = readSource('./stageGate.ts')
  const unionSource = stageGateSource.slice(
    stageGateSource.indexOf('export type GateBlocker ='),
    stageGateSource.indexOf('const gateBlockerText'),
  )
  const blockers = [...unionSource.matchAll(/'([^']+)'/g)].map((match) => match[1])
  const mapSource = stageGateSource.slice(
    stageGateSource.indexOf('const gateBlockerText'),
    stageGateSource.indexOf('\n}', stageGateSource.indexOf('const gateBlockerText')),
  )
  const texts = new Map(
    [...mapSource.matchAll(/'?([A-Za-z][\w-]*)'?:\s*'([^']*)'/g)].map((match) => [match[1], match[2]]),
  )
  return { blockers, texts, stageGateTestSource: readSource('./stageGate.test.ts') }
}

describe('stage gate usage contract', () => {
  it('detects inline authorization combinations instead of silently passing', () => {
    const source = `
      <Button disabled={busy || running} />
      <Button disabled={!state?.loaded} />
      <Button disabled={busy || running || attempted} />
    `
    expect(inlineAuthorizationViolations(source)).toEqual([
      'Button disabled={busy || running}',
      'Button disabled={!state?.loaded}',
      'Button disabled={busy || running || attempted}',
    ])
    expect(inlineAuthorizationViolations(`
      <Input disabled={!gate.available} />
      <Button disabled={!previewGate.allowed} />
      <Button disabled={busy} 只读刷新 />
      <Button disabled={offset === 0} 上一页候选 />
    `)).toEqual([])
  })

  it.each(components)('%s imports evaluateStageGate', (component) => {
    const source = readSource(`../components/${component}.tsx`)
    expect(source).toMatch(/import\s*\{[^}]*\bevaluateStageGate\b[^}]*\}\s*from\s*['"]@\/lib\/stageGate['"]/)
  })

  it.each(components)('%s uses no inline authorization disabled props', (component) => {
    const source = readSource(`../components/${component}.tsx`)
    expect(inlineAuthorizationViolations(source)).toEqual([])
  })

  it('keeps every GateBlocker and its text covered by stageGate tests', () => {
    const { blockers, texts, stageGateTestSource } = blockerContract()
    expect(blockers.length).toBeGreaterThan(0)
    expect([...texts.keys()].sort()).toEqual([...blockers].sort())
    for (const blocker of blockers) {
      const text = texts.get(blocker)
      expect(stageGateTestSource, `missing blocker ${blocker}`).toContain(`'${blocker}'`)
      expect(stageGateTestSource, `missing blocker text ${blocker}`).toContain(text)
    }
  })
})
