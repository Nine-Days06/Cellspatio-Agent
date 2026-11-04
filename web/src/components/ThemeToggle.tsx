import { useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'

/** 页头主题开关；初始主题由 head 脚本在首屏绘制前恢复。 */
export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  const label = dark ? '切换到浅色模式' : '切换到深色模式'

  function toggleTheme(): void {
    const nextDark = !dark
    document.documentElement.classList.toggle('dark', nextDark)
    setDark(nextDark)
    try {
      localStorage.setItem('cellspatio-theme', nextDark ? 'dark' : 'light')
    } catch {
      // 浏览器禁用存储时，仍允许切换当前页面。
    }
  }

  return (
    <Button variant="ghost" size="icon" className="min-h-11 min-w-11" aria-label={label} title={label} onClick={toggleTheme}>
      {dark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
    </Button>
  )
}
