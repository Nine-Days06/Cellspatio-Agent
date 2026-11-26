import { useEffect, useRef, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { MOTION_MS, prefersReducedMotion } from '@/lib/motion'

/** 页头主题开关；初始主题由 head 脚本在首屏绘制前恢复。 */
export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  const label = dark ? '切换到浅色模式' : '切换到深色模式'
  const themeTimer = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (themeTimer.current !== null) {
        window.clearTimeout(themeTimer.current)
        document.documentElement.classList.remove('theme-transition')
      }
    },
    [],
  )

  function toggleTheme(): void {
    // 临时启用底色过渡；动画结束后移除，首屏与后续渲染保持无过渡。
    if (!prefersReducedMotion()) {
      document.documentElement.classList.add('theme-transition')
      if (themeTimer.current !== null) window.clearTimeout(themeTimer.current)
      themeTimer.current = window.setTimeout(() => {
        themeTimer.current = null
        document.documentElement.classList.remove('theme-transition')
      }, MOTION_MS.page)
    }
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
      <span key={dark ? 'dark' : 'light'} className="motion-icon inline-flex" aria-hidden="true">
        {dark ? <Sun /> : <Moon />}
      </span>
    </Button>
  )
}
