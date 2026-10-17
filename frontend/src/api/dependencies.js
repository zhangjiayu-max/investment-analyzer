import api from './http'

export function getTTFundDependencyStatus() {
  return api.get('/config/ttfund/status')
}

export function startTTFundLogin() {
  return api.post('/config/ttfund/login', {}, { timeout: 150000 })
}
