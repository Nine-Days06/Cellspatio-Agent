import type { MessageResults } from '@/lib/types'
import { QcReview } from '@/components/QcReview'

/** 本地 QC 证据与关联阈值选择；不提供实际过滤操作。 */
export function QcEvidence({ results, sessionId, onRecorded }: { results: MessageResults; sessionId?: string; onRecorded?: () => void }) {
  const qc = results.qc
  return (
    <section aria-label="过滤前 QC 证据" className="mt-3 space-y-3 border-t border-border pt-3 text-sm">
      <h3 className="font-semibold">{qc ? '过滤前 QC · 等待科学审阅' : 'QC 未完成 · 本地证据已保留'}</h3>
      <p className="text-muted-foreground">此阶段未执行过滤、聚类或注释。</p>
      {qc && <>
        <p>全部 RNA barcode：{qc.cells.toLocaleString()} · RNA features：{qc.features.toLocaleString()}</p>
        {qc.warnings.map((warning) => <p key={warning} role="note" className="text-amber-700 dark:text-amber-400">{warning}</p>)}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">过滤前指标汇总</caption>
            <thead><tr>{['指标', '有效数量', '最小', '中位数', '最大'].map((label) => <th key={label} scope="col" className="px-2 py-1">{label}</th>)}</tr></thead>
            <tbody>{Object.entries(qc.metrics).map(([name, metric]) => <tr key={name}>
              <th scope="row" className="px-2 py-1 font-medium">{name === 'percent_mt' ? 'percent.mt' : name}</th>
              {[metric.available, metric.min, metric.median, metric.max].map((value, index) => <td key={index} className="px-2 py-1">{value === null ? 'NA' : value.toLocaleString(undefined, { maximumFractionDigits: 3 })}</td>)}
            </tr>)}</tbody>
          </table>
        </div>
        <p className="text-xs text-muted-foreground">QC {qc.tool_version} · {qc.versions.join(' · ')}</p>
      </>}
      {results.qc_artifacts && <details>
        <summary className="cursor-pointer">本地证据文件（非下载链接）</summary>
        <ul className="mt-2 space-y-1 text-xs">{Object.entries(results.qc_artifacts).map(([name, path]) => <li key={name}>
          <span className="font-medium">{name}</span><code className="block break-all text-muted-foreground">{path}</code>
        </li>)}</ul>
      </details>}
      {results.qc_run_id && sessionId ? <QcReview key={`${sessionId}:${results.qc_run_id}`} sessionId={sessionId} runId={results.qc_run_id} onRecorded={onRecorded} /> : qc && <p className="text-xs text-muted-foreground">只读证据；交互选择需要重新运行 QC 并建立会话关联。</p>}
    </section>
  )
}
