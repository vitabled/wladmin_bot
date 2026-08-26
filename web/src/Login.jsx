import { useEffect, useState } from 'react';
import { api } from './api';

// Login screen: centered card + a custom "Log in with Telegram" pill button.
// Uses Telegram's OAuth-style direct flow (no widget iframe → no white frame):
//   https://oauth.telegram.org/auth?bot_id=...&origin=...&embed=0&request_access=write&return_to=/auth/telegram
// After the user authorizes, oauth.telegram.org redirects to our origin +
// return_to with signed fields (GET params) — the server verifies them in
// /auth/telegram, sets the session cookie and redirects to "/".
// The popup is opened so the SPA tab keeps its state.

const BOT_ID = 8588028918; // @whitemarketadminbot (numeric id, public info)

function telegramAuthUrl() {
  const origin = encodeURIComponent(window.location.origin);
  const ret = encodeURIComponent('/auth/telegram');
  return (
    'https://oauth.telegram.org/auth?bot_id=' +
    BOT_ID +
    '&origin=' +
    origin +
    '&embed=0&request_access=write&return_to=' +
    ret
  );
}

export default function Login() {
  const [failed, setFailed] = useState(false);

  // If Telegram ever returns the result as a hash fragment (#tgAuthResult=...),
  // forward it to /auth/telegram as form data (the server also accepts POST).
  useEffect(() => {
    try {
      const h = window.location.hash;
      if (h && h.indexOf('tgAuthResult=') === 1) {
        const raw = decodeURIComponent(h.slice('tgAuthResult='.length + 1));
        const data = JSON.parse(raw);
        if (data && data.id) {
          const body = new URLSearchParams();
          Object.keys(data).forEach((k) => body.append(k, String(data[k])));
          fetch('/auth/telegram', { method: 'POST', body, credentials: 'same-origin' }).then(() => {
            window.location.replace('/');
          });
        }
      }
    } catch (e) {
      /* not a tgAuthResult hash — ignore */
    }
  }, []);

  const openLogin = (e) => {
    e.preventDefault();
    const win = window.open(telegramAuthUrl(), '_blank', 'width=520,height=640');
    if (!win) {
      // Popup blocked — navigate the current tab instead.
      window.location.href = telegramAuthUrl();
    }
  };

  return (
    <div className="login-screen">
      <div className="login-card">
        <h1 className="login-heading">Панель администратора</h1>
        <p className="login-subline">Управление ботом</p>
        {failed && <p className="form-error">Не удалось загрузить конфигурацию входа.</p>}
        <a className="login-telegram-btn" href={telegramAuthUrl()} onClick={openLogin}>
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path
              fill="currentColor"
              d="M21.9 4.3c.3-1.2-.9-2.2-2-1.7L2.4 9.7c-1.2.5-1.1 2.2.1 2.6l4.6 1.4 1.7 5.3c.3 1 1.6 1.3 2.4.6l2.6-2.4 4.5 3.3c.9.7 2.2.2 2.4-.9l3-14.3zM8.4 12.9l8.7-5.4c.4-.2.8.3.4.6l-6.9 6.3c-.3.3-.5.7-.6 1.1l-.4 2.2c0 .3-.5.4-.6.1l-1.1-3.4c-.1-.5 0-1 .5-1.5z"
            />
          </svg>
          Log in with Telegram
        </a>
      </div>
    </div>
  );
}
