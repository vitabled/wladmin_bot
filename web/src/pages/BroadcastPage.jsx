import { useEffect, useState } from 'react';
import { api } from '../api';

export default function BroadcastPage({ chatId, chats }) {
  const [topics, setTopics] = useState([]);
  const [checked, setChecked] = useState({});
  const [allOn, setAllOn] = useState(false);
  const [text, setText] = useState('');
  const [results, setResults] = useState(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);

  // Load topics whenever the global chat changes.
  useEffect(() => {
    if (!chatId) {
      setTopics([]);
      setChecked({});
      setAllOn(false);
      setResults(null);
      return;
    }
    let alive = true;
    api
      .get(`/api/chats/${chatId}`)
      .then((data) => {
        if (!alive) return;
        setTopics(data.topics || []);
        setChecked({});
        setAllOn(false);
        setResults(null);
      })
      .catch((err) => {
        if (alive) setError(err.detail || 'Ошибка загрузки веток');
      });
    return () => {
      alive = false;
    };
  }, [chatId]);

  function toggleAll() {
    const next = !allOn;
    const c = {};
    topics.forEach((t) => {
      c[t.thread_id] = next;
    });
    setChecked(c);
    setAllOn(next);
  }

  function toggleOne(threadId) {
    const next = { ...checked, [threadId]: !checked[threadId] };
    setChecked(next);
    setAllOn(topics.length > 0 && topics.every((t) => next[t.thread_id]));
  }

  const selected = topics.filter((t) => checked[t.thread_id]);
  const canSend = selected.length > 0 && text.trim().length > 0 && !sending;

  async function send() {
    setSending(true);
    setResults(null);
    setError(null);
    try {
      const res = await api.post('/api/broadcast', {
        chat_id: Number(chatId),
        thread_ids: selected.map((t) => t.thread_id),
        text,
      });
      setResults(res.results);
    } catch (err) {
      setError(err.detail || 'Ошибка отправки');
    } finally {
      setSending(false);
    }
  }

  return (
    <div>
      <h2>Рассылки</h2>

      {!chatId && (
        <section className="card">
          <p className="muted">Выберите чат вверху, чтобы начать рассылку по веткам.</p>
        </section>
      )}

      {chatId && (
        <section className="card">
          <div className="topic-header">
            <h3>Ветки</h3>
            <label className="toggle-row">
              <span>Выбрать все</span>
              <span className="switch">
                <input type="checkbox" checked={allOn} onChange={toggleAll} />
                <span className="track" />
              </span>
            </label>
          </div>
          {topics.length === 0 ? (
            <p className="muted">Веток в этом чате нет.</p>
          ) : (
            <div className="topic-list">
              {topics.map((t) => (
                <label key={t.thread_id} className="toggle-row">
                  <span className="topic-label">
                    Ветка #{t.thread_id} · {t.message_count} сообщ.
                  </span>
                  <input
                    type="checkbox"
                    checked={!!checked[t.thread_id]}
                    onChange={() => toggleOne(t.thread_id)}
                  />
                </label>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="card">
        <label className="field">
          <span>Текст рассылки</span>
          <textarea
            rows="5"
            placeholder="Текст сообщения…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <div className="broadcast-actions">
          <button type="button" className="btn" disabled={!canSend} onClick={send}>
            {sending ? 'Отправка…' : 'Отправить'}
          </button>
          {!canSend && !sending && (
            <p className="muted hint">
              {selected.length === 0
                ? 'Выберите хотя бы одну ветку.'
                : 'Введите текст рассылки.'}
            </p>
          )}
        </div>
      </section>

      {error && <p className="form-error">{error}</p>}

      {results && (
        <section className="card">
          <h3>Результат</h3>
          {results.map((r) => (
            <div key={r.thread_id} className={`result ${r.ok ? 'result-ok' : 'result-fail'}`}>
              <span className="result-icon">{r.ok ? '✅' : '❌'}</span>
              <span className="result-text">Ветка #{r.thread_id}</span>
              {!r.ok && <span className="result-error">{r.error}</span>}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
