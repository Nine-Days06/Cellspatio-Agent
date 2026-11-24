/** 与 index.css 的 --motion-* 令牌同值；CSS 变量无法在 JS 里直接取数值。 */
export const MOTION_MS = { fast: 120, hint: 140, enter: 160, base: 180, page: 200, slow: 240 } as const

/** CSS 的全局 reduced-motion 规则管不到 JS 传的滚动参数，必须显式判断。 */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
}
