/** 跨用户切换：清空除当前用户信息外的所有查询与对话状态。 */
import type { QueryClient } from '@tanstack/react-query'
import { useChatStore } from '@/store/chat'

/** 登录/登出后复位前端缓存，避免下一个用户看到上一个用户的数据。 */
export async function resetUserState(queryClient: QueryClient): Promise<void> {
  queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' })
  useChatStore.getState().reset()
  await queryClient.invalidateQueries({ queryKey: ['me'] })
}
