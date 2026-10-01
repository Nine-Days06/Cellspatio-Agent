import { useEffect, useState } from 'react'
import { Database, FileCode2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import type {
  ConfirmRequestBody,
  DataCandidate,
  DataCardPayload,
  ScriptCardPayload,
} from '@/lib/types'
import { useChatStore } from '@/store/chat'

const STATUS_TEXT: Record<string, string> = {
  confirmed: '已确认执行',
  cancelled: '已取消',
  expired: '已过期（会话已恢复，需重新发起）',
}

export interface ConfirmCardProps {
  scriptCard: ScriptCardPayload | null
  dataCard: DataCardPayload | null
  busy: boolean
  /** POST /api/confirm 失败的后端中文 detail（400/404/409/502）；非空时卡片恢复可点 */
  confirmError: string | null
  /** body 已带 session_id（组件从会话 store 取），ChatPage 直接 confirmAction(body) */
  onConfirm: (body: ConfirmRequestBody) => void
  /** 重新生成：ChatPage 用 pending_script.user_request 再发一次 /api/chat（不是 confirm 动作） */
  onRegen: () => void
}

/**
 * HITL 确认卡片状态机（约束 2/3）：
 * - 初始（pending）：动作按钮可用；busy（会话锁被工具执行持有）时全部 disabled，
 *   避免此刻发 confirm 撞 409「该会话已有任务在进行」。
 * - 已提交（点击后本地 submitting 立即置位）：全部按钮 disabled，防重复提交。
 * - 失败（confirmError 到达，含 409）：卡片保留、原样展示后端 detail、恢复可点。
 * - 成功：T8 通过 store 回写 status（脚本卡显示「已确认执行/已取消」）或清除卡片（数据卡）。
 * 文案不承诺瞬时取消：停止类提示由 T8 输入区负责。
 */
export function ConfirmCard({
  scriptCard,
  dataCard,
  busy,
  confirmError,
  onConfirm,
  onRegen,
}: ConfirmCardProps) {
  const sessionId = useChatStore((state) => state.sessionId)
  const [submitting, setSubmitting] = useState(false)

  // 失败到达（ChatPage 契约：发起新确认前先把 confirmError 置 null）→ 解除已提交态，恢复可点
  useEffect(() => {
    if (confirmError !== null) setSubmitting(false)
  }, [confirmError])

  const locked = busy || submitting
  const session_id = sessionId ?? ''

  if (scriptCard) {
    const isPending = scriptCard.status === 'pending'
    const submit = (action: ConfirmRequestBody['action']) => {
      if (locked) return
      setSubmitting(true)
      onConfirm({ session_id, action })
    }
    const lines = scriptCard.script.split('\n')
    return (
      <Card data-testid="script-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            <FileCode2 className="size-4" aria-hidden="true" />
            分析脚本确认
            {scriptCard.analysis_type && (
              <span className="ml-1 rounded bg-muted px-1.5 py-0.5 text-xs font-normal text-muted-foreground">
                {scriptCard.analysis_type}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">请求：{scriptCard.user_request}</p>
          {/* 脚本来自 LLM/用户数据：逐行纯文本渲染，绝不 dangerouslySetInnerHTML */}
          <pre className="max-h-64 overflow-auto rounded-lg border border-border bg-muted/40 p-3 text-xs">
            {lines.map((line, index) => (
              <span key={index}>
                {line}
                {index < lines.length - 1 ? '\n' : ''}
              </span>
            ))}
          </pre>
          {confirmError && <p className="text-xs text-destructive">{confirmError}</p>}
          {!isPending && (
            <p className="text-xs text-muted-foreground">
              {STATUS_TEXT[scriptCard.status] ?? scriptCard.status}
            </p>
          )}
          {isPending && submitting && (
            <p className="text-xs text-muted-foreground">已提交，等待后端响应…</p>
          )}
        </CardContent>
        {isPending && (
          <CardFooter className="gap-2">
            <Button
              aria-label="执行脚本"
              disabled={locked}
              onClick={() => submit('script_confirm')}
            >
              执行脚本
            </Button>
            <Button
              aria-label="取消执行"
              variant="outline"
              disabled={locked}
              onClick={() => submit('script_cancel')}
            >
              取消执行
            </Button>
            <Button
              aria-label="重新生成"
              variant="ghost"
              disabled={locked}
              onClick={onRegen}
            >
              <RefreshCw className="size-4" aria-hidden="true" />
              重新生成
            </Button>
          </CardFooter>
        )}
      </Card>
    )
  }

  if (dataCard) {
    const submitCandidate = (candidate: DataCandidate) => {
      if (locked) return
      setSubmitting(true)
      onConfirm({
        session_id,
        action: 'data_confirm',
        source: candidate.source,
        asset_id: candidate.asset_id,
        query: dataCard.query,
      })
    }
    return (
      <Card data-testid="data-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            <Database className="size-4" aria-hidden="true" />
            选择要下载的数据集
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-muted-foreground">
            {dataCard.message}（检索词：{dataCard.query}）
          </p>
          <ul className="flex flex-col gap-2">
            {dataCard.candidates.map((candidate) => (
              <li
                key={`${candidate.source}:${candidate.asset_id}`}
                className="rounded-lg border border-border p-2.5"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{candidate.title}</p>
                    <p className="text-xs text-muted-foreground">
                      {candidate.source} · {candidate.asset_id} · {candidate.asset_type}
                    </p>
                    {candidate.description && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {candidate.description}
                      </p>
                    )}
                    <p className="mt-1 text-xs text-muted-foreground">理由：{candidate.reason}</p>
                  </div>
                  <Button
                    aria-label="使用此数据集"
                    size="sm"
                    disabled={locked}
                    onClick={() => submitCandidate(candidate)}
                  >
                    使用此数据集
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          {confirmError && <p className="text-xs text-destructive">{confirmError}</p>}
          {submitting && (
            <p className="text-xs text-muted-foreground">已提交，等待后端响应…</p>
          )}
        </CardContent>
      </Card>
    )
  }

  return null
}
