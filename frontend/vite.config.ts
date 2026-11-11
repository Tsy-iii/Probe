import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

function developmentPort(environment: Record<string, string>, name: string, fallback: number): number {
  const value = environment[name] ?? String(fallback)
  if (!/^[0-9]{1,5}$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error(`${name} must be an integer port between 1 and 65535`)
  }
  return Number(value)
}

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, '.', 'PROBE_DEV_')
  const platformPort = developmentPort(environment, 'PROBE_DEV_PLATFORM_PORT', 8001)
  const frontendPort = developmentPort(environment, 'PROBE_DEV_FRONTEND_PORT', 5174)
  if (platformPort === frontendPort || [platformPort, frontendPort].includes(5173)) {
    throw new Error('Development ports must be distinct; port 5173 is reserved for Discovery')
  }
  const proxy = { '/api': `http://127.0.0.1:${platformPort}` }
  return {
    plugins: [react()],
    cacheDir: '.local/vite-cache',
    server: { host: '127.0.0.1', port: frontendPort, strictPort: true, proxy },
    preview: { proxy },
  }
})
