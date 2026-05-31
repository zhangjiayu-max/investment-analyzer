import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  use: {
    baseURL: 'http://localhost:8000',
    headless: true,
  },
  webServer: {
    command: 'cd ../backend && python3 -m uvicorn app:app --port 8000',
    port: 8000,
    reuseExistingServer: true,
    timeout: 15000,
  },
})
