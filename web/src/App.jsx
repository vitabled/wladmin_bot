import { useEffect, useState } from 'react';
import { api } from './api';
import Login from './Login';
import AdminPage from './pages/AdminPage';
import RatingsPage from './pages/RatingsPage';
import BroadcastPage from './pages/BroadcastPage';

const TABS = [
  { id: 'admin', label: 'Администрирование', icon: '⚙' },
  { id: 'ratings', label: 'Рейтинги', icon: '🛡' },
  { id: 'broadcast', label: 'Рассылки', icon: '📣' },
];

function readInitData(source) {
  const p = new URLSearchParams(source);
  return p.get('tgWebAppData') || null;
}

function getInitData() {
  const h = window.location.hash;
  if (h && h.length > 1) {
    const v = readInitData(h.slice(1));
    if (v) return v;
  }
  return readInitData(window.location.search.slice(1));
}

export default function App() {
  const [phase, setPhase] = useState('loading'); // loading | login | main
  const [me, setMe] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      // Telegram Mini App flow: when the SPA is opened via the bot's WebApp
      // button, Telegram injects initData into the URL. If we have it, exchange
      // it for a session (no login form), then reload so /api/me succeeds.
      const initData = getInitData();
      if (initData) {
        try {
          const body = new URLSearchParams();
          body.append('initData', initData);
          await fetch('/auth/webapp', {
            method: 'POST',
            body,
            credentials: 'same-origin',
          });
          // Clear the fragment so it can't be replayed, then boot as usual.
          window.history.replaceState(null, '', window.location.pathname);
        } catch (e) {
          /* fall through to the normal 401 → login path */
        }
      }
      try {
        const data = await api.get('/api/me');
        if (!alive) return;
        setMe(data);
        setPhase('main');
      } catch (err) {
        if (!alive) return;
        // No session (401) — or a network hiccup — both land on the login
        // screen, which loads the public /api/login-config for the widget.
        setPhase('login');
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (phase === 'loading') {
    return (
      <div className="screen-center">
        <p>Загрузка…</p>
      </div>
    );
  }

  if (phase === 'login') {
    return <Login />;
  }

  return <Main me={me} />;
}

function Main({ me }) {
  const [tab, setTab] = useState('admin');
  const [chats, setChats] = useState([]);
  const [chatId, setChatId] = useState('');

  // Global chat selector: loaded once, visible on every tab.
  useEffect(() => {
    let alive = true;
    api
      .get('/api/chats')
      .then((data) => {
        if (alive) setChats(data || []);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          WL Admin<span className="brand-dot">.</span>
        </span>
        <span className="topbar-spacer" />
        <span className="user">{me && (me.name || me.user_id)}</span>
        <a className="logout" href="/logout">
          Выйти
        </a>
      </header>
      <div className="chat-bar">
        <label className="field chat-global">
          <span className="chat-global-label">Чат</span>
          <select value={chatId} onChange={(e) => setChatId(e.target.value)}>
            <option value="">— выберите чат —</option>
            {chats.map((c) => (
              <option key={c.chat_id} value={c.chat_id}>
                {c.title || c.chat_id}
              </option>
            ))}
          </select>
        </label>
      </div>
      <nav className="tabbar">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`tab${tab === t.id ? ' active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            <span className="tab-icon">{t.icon}</span>
            <span className="tab-label">{t.label}</span>
          </button>
        ))}
      </nav>
      <main className="content">
        {tab === 'admin' && (
          <AdminPage chats={chats} chatId={chatId} onChatChange={setChatId} />
        )}
        {tab === 'ratings' && <RatingsPage chatId={chatId} />}
        {tab === 'broadcast' && <BroadcastPage chatId={chatId} chats={chats} />}
      </main>
    </div>
  );
}
