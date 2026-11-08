import { expect, test, type Page } from '@playwright/test'

const PASSWORD = 'password123'

async function login(page: Page, username: string): Promise<void> {
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(PASSWORD)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('button', { name: '登出' })).toBeVisible()
}

test('A 登出后 B 登录看不到 A 的会话', async ({ page }) => {
  await page.goto('/')
  await login(page, 'alice')

  await page.getByRole('button', { name: '新建会话' }).click()
  const sessions = page.getByRole('navigation', { name: '会话列表' })
  await expect(sessions).toContainText('会话记录 1')

  const aliceSessions = await (await page.request.get('/api/sessions')).json()
  expect(aliceSessions.sessions).toHaveLength(1)

  await page.getByRole('button', { name: '登出' }).click()
  await expect(page.getByRole('button', { name: '登录' })).toBeVisible()
  await login(page, 'bob')

  await expect(sessions).toContainText('暂无会话')
  const bobSessions = await (await page.request.get('/api/sessions')).json()
  expect(bobSessions.sessions).toHaveLength(0)
})
