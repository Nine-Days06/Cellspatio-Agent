import { useRoute } from './lib/navigation'
import { ChatPage } from './pages/ChatPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'

export default function App() {
  const route = useRoute()
  if (route === '/login') return <LoginPage />
  if (route === '/workspace') return <ChatPage />
  return <HomePage />
}
