import { useEffect, useState } from 'react';
import { api } from '../api';
import StatsView from './StatsView';

// (field, label) pairs in the same order as bot/constants.TOGGLES.
const TOGGLES = [
  ['welcome_enabled', 'Приветствие'],
  ['captcha_enabled', 'Капча'],
  ['filter_links', 'Фильтр ссылок'],
  ['filter_forwards', 'Фильтр пересылок'],
  ['filter_stopwords', 'Фильтр стоп-слов'],
  ['antiflood_enabled', 'Антифлуд'],
  ['newbie_media_enabled', 'Медиа новичков'],
  ['triggers_enabled', 'Триггеры'],
  ['stats_enabled', 'Статистика'],
];

export default function AdminPage({ chats, chatId, onChatChange }) {
  const [error, setError] = useState(null);

  if (chatId) {
    return (
      <ChatDetail
        key={chatId}
        chatId={chatId}
        onBack={() => onChatChange('')}
      />
    );
  }

  if (error) return <p className="form-error">{error}</p>;
  if (!chats) return <div className="loading">Загрузка…</div>;

  return (
    <div>
      <h2>Группы</h2>
      {chats.length === 0 && <div className="card empty">Нет доступных групп.</div>}
      <div className="chat-list">
        {chats.map((c) => (
          <button
            key={c.chat_id}
            type="button"
            className="card chat-card"
            onClick={() => onChatChange(c.chat_id)}
          >
            <span className="chat-title">{c.title || c.chat_id}</span>
            <span className="chat-meta">
              id {c.chat_id} · сообщений: {c.total_messages} · забанено: {c.banned}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function ChatDetail({ chatId, onBack }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [settings, setSettings] = useState(null);
  const [slow, setSlow] = useState({ enabled: false, regular_seconds: '', wl_seconds: '' });
  // null = «Все ветки» (whole chat), [] would be ambiguous — use null; non-empty = selected thread ids.
  const [topicSelection, setTopicSelection] = useState(null);
  const [savingSlow, setSavingSlow] = useState(false);
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    let alive = true;
    api
      .get(`/api/chats/${chatId}`)
      .then((data) => {
        if (!alive) return;
        setDetail(data);
        setSettings(data.settings);
        setSlow({
          enabled: data.slow_mode.enabled,
          regular_seconds: String(data.slow_mode.regular_seconds),
          wl_seconds: String(data.slow_mode.wl_seconds),
        });
        const stored = data.slow_mode.topic_ids;
        setTopicSelection(stored && stored.length ? stored : null);
      })
      .catch((err) => {
        if (alive) setError(err.detail || 'Ошибка загрузки');
      });
    return () => {
      alive = false;
    };
  }, [chatId]);

  async function toggle(field) {
    const prev = settings[field];
    // Optimistic update; revert on failure.
    setSettings({ ...settings, [field]: !prev });
    setMsg(null);
    try {
      const res = await api.post(`/api/chats/${chatId}/toggle`, { field });
      setSettings((s) => ({ ...s, [field]: res.value }));
    } catch (err) {
      setSettings((s) => ({ ...s, [field]: prev }));
      setMsg({ kind: 'error', text: `Не удалось переключить: ${err.detail || 'ошибка'}` });
    }
  }

  async function saveSlowMode() {
    setSavingSlow(true);
    setMsg(null);
    try {
      await api.post(`/api/chats/${chatId}/slow-mode`, {
        enabled: slow.enabled,
        regular_seconds: parseInt(slow.regular_seconds, 10) || 0,
        wl_seconds: parseInt(slow.wl_seconds, 10) || 0,
        topic_ids: topicSelection && topicSelection.length ? topicSelection : [],
      });
      setMsg({ kind: 'ok', text: 'Сохранено' });
    } catch (err) {
      setMsg({ kind: 'error', text: `Ошибка: ${err.detail || 'не удалось сохранить'}` });
    } finally {
      setSavingSlow(false);
    }
  }

  function toggleTopic(threadId) {
    setTopicSelection((prev) => {
      if (prev === null) return [threadId];
      if (prev.includes(threadId)) {
        const next = prev.filter((id) => id !== threadId);
        return next.length ? next : null; // nothing picked → whole chat
      }
      return [...prev, threadId];
    });
  }

  if (error) return <p className="form-error">{error}</p>;
  if (!detail) return <div className="loading">Загрузка…</div>;

  return (
    <div>
      <button type="button" className="btn btn-ghost back" onClick={onBack}>
        ← Назад
      </button>
      <h2>{detail.title || detail.chat_id}</h2>
      <p className="muted">id {detail.chat_id}</p>

      {msg && <p className={msg.kind === 'ok' ? 'form-ok' : 'form-error'}>{msg.text}</p>}

      <section className="card">
        <h3>Настройки</h3>
        <div className="toggle-list">
          {TOGGLES.map(([field, label]) => (
            <label key={field} className="toggle-row">
              <span>{label}</span>
              <span className="switch">
                <input
                  type="checkbox"
                  checked={!!settings[field]}
                  onChange={() => toggle(field)}
                />
                <span className="track" />
              </span>
            </label>
          ))}
        </div>
      </section>

      <section className="card">
        <h3>Медленный режим</h3>
        <p className="muted hint">Админы без ограничений</p>
        <div className="slow-form">
          <label className="toggle-row">
            <span>Включён</span>
            <span className="switch">
              <input
                type="checkbox"
                checked={slow.enabled}
                onChange={(e) => setSlow({ ...slow, enabled: e.target.checked })}
              />
              <span className="track" />
            </span>
          </label>
          <label className="field">
            <span>Обычные (сек)</span>
            <input
              type="number"
              min="0"
              value={slow.regular_seconds}
              onChange={(e) => setSlow({ ...slow, regular_seconds: e.target.value })}
            />
          </label>
          <label className="field">
            <span>WL (сек)</span>
            <input
              type="number"
              min="0"
              value={slow.wl_seconds}
              onChange={(e) => setSlow({ ...slow, wl_seconds: e.target.value })}
            />
          </label>
          {detail.topics.length > 0 && (
            <div className="slow-topics">
              <div className="slow-topics-title">Ветки действия правила</div>
              <label className="toggle-row">
                <span>Все ветки</span>
                <span className="switch">
                  <input
                    type="checkbox"
                    checked={topicSelection === null}
                    onChange={() => setTopicSelection(null)}
                  />
                  <span className="track" />
                </span>
              </label>
              <div className="slow-topics-list">
                {detail.topics.map((t) => (
                  <label key={t.thread_id} className="slow-topic-row">
                    <input
                      type="checkbox"
                      checked={topicSelection !== null && topicSelection.includes(t.thread_id)}
                      onChange={() => toggleTopic(t.thread_id)}
                    />
                    <span>
                      #{t.thread_id} · {t.message_count} сообщ.
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <button
            type="button"
            className="btn"
            disabled={savingSlow}
            onClick={saveSlowMode}
          >
            {savingSlow ? 'Сохранение…' : 'Сохранить'}
          </button>
        </div>
      </section>

      <section className="card">
        <h3>Статистика</h3>
        <StatsView chatId={chatId} />
      </section>

      <section className="card">
        <h3>Ветки</h3>
        {detail.topics.length === 0 ? (
          <p className="muted">Веток пока нет.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>thread_id</th>
                <th>Сообщений</th>
                <th>Последняя активность</th>
              </tr>
            </thead>
            <tbody>
              {detail.topics.map((t) => (
                <tr key={t.thread_id}>
                  <td>{t.thread_id}</td>
                  <td>{t.message_count}</td>
                  <td>{fmtDate(t.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
}
