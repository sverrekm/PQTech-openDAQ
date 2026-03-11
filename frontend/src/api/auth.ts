import { apiGet, apiPost } from './client'

export interface AuthStatus { innlogga: boolean; brukarnavn?: string }
export interface LoginResult { suksess: boolean; melding?: string }

export const sjekkAuth = () => apiGet<AuthStatus>('/api/auth/status')
export const loggInn = (brukarnavn: string, passord: string) =>
  apiPost<LoginResult>('/api/auth/login', { brukarnavn, passord })
export const loggUt = () => apiPost<LoginResult>('/api/auth/logout')
export const endrePassord = (gammalt: string, nytt: string) =>
  apiPost<LoginResult>('/api/auth/endre-passord', { gammalt, nytt })
