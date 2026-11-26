import { useRoute } from './lib/navigation'
import { ChatPage } from './pages/ChatPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'

export default function App() {
  const route = useRoute()
  const page = route === '/login' ? <LoginPage /> : route === '/workspace' ? <ChatPage /> : <HomePage />
  // 以路径为 key：只播进入动画，不做退出动画，避免延迟真实导航。
  return (
    <div key={route} className="motion-page">
      {page}
    </div>
  )
}
