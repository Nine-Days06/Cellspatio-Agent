import { useSyncExternalStore } from 'react'

export type Route = '/' | '/login' | '/workspace'

const listeners = new Set<() => void>()

function currentPath(): string {
  return window.location.pathname.replace(/\/+$/, '') || '/'
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  window.addEventListener('popstate', listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('popstate', listener)
  }
}

function snapshot(): string {
  return currentPath()
}

export function navigate(path: Route): void {
  if (currentPath() !== path) window.history.pushState({}, '', path)
  for (const listener of listeners) listener()
}

export function useRoute(): string {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}
