import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: {
    baseURL: 'http://127.0.0.1:8620',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: '.venv\\Scripts\\python.exe web/e2e/serve.py',
    cwd: '..',
    url: 'http://127.0.0.1:8620',
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
