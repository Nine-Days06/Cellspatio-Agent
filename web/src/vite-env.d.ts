/// <reference types="vite/client" />

// plotly.js-dist-min 不带类型：给最小声明，避免 any 泄漏进业务代码
declare module 'plotly.js-dist-min' {
  interface PlotlyModule {
    newPlot(
      root: HTMLElement,
      data: unknown[],
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ): Promise<HTMLElement>
    react(
      root: HTMLElement,
      data: unknown[],
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ): Promise<HTMLElement>
  }
  const Plotly: PlotlyModule
  export default Plotly
}