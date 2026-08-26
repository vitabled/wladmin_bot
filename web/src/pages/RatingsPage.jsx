import { useEffect, useState } from 'react';
import { api } from '../api';

function sourceBadge(source) {
  if (source === 'scam') return <span className="badge badge-scam">СКАМ</span>;
  if (source === 'verified') return <span className="badge badge-verified">ВЕР</span>;
  return <span className="badge badge-other">иное</span>;
}

export default function RatingsPage({ chatId }) {
  const [target, setTarget] = useState('');
  const [verdict, setVerdict] = useState(null);
  const [checking, setChecking] = useState(false);
  const [list, setList] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    api
      .get('/api/rating/list')
      .then((data) => {
        if (alive) setList(data);
      })
      .catch((err) => {
        if (alive) setError(err.detail || 'Ошибка загрузки списка');
      });
    return () => {
      alive = false;
    };
  }, []);

  async function check() {
    if (!target.trim()) return;
    setChecking(true);
    setVerdict(null);
    setError(null);
    try {
      const params = new URLSearchParams({ target: target.trim() });
      if (chatId) params.set('chat_id', chatId);
      const data = await api.get(`/api/rating?${params.toString()}`);
      setVerdict(data);
    } catch (err) {
      setError(err.detail || 'Ошибка проверки');
    } finally {
      setChecking(false);
    }
  }

  async function addToWl() {
    if (!verdict) return;
    try {
      await api.post('/api/rating/wl', { user_id: verdict.target_id });
      await reloadList();
      setError(null);
    } catch (err) {
      setError(err.detail || 'Не удалось добавить в белый список');
    }
  }

  async function removeFromWl() {
    if (!verdict) return;
    try {
      await api.del('/api/rating/wl', { user_id: verdict.target_id });
      await reloadList();
      setError(null);
    } catch (err) {
      setError(err.detail || 'Не удалось убрать из списка');
    }
  }

  async function removeRow(userId) {
    try {
      await api.del('/api/rating/wl', { user_id: userId });
      await reloadList();
    } catch (err) {
      setError(err.detail || 'Не удалось удалить запись');
    }
  }

  async function reloadList() {
    const data = await api.get('/api/rating/list');
    setList(data);
  }

  return (
    <div>
      <h2>Рейтинги</h2>

      <section className="card">
        <h3>Проверить продавца</h3>
        <div className="form">
          <label className="field">
            <span>Цель (id или @username)</span>
            <input
              type="text"
              placeholder="@username или 123456789"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
          </label>
          {chatId && (
            <p className="muted hint">Проверка будет учитывать выбранный чат (id {chatId}).</p>
          )}
          <button type="button" className="btn" disabled={checking || !target.trim()} onClick={check}>
            {checking ? 'Проверка…' : 'Проверить'}
          </button>
        </div>

        {error && <p className="form-error">{error}</p>}

        {verdict && (
          <div className="verdict">
            <p className="verdict-name">
              <strong>{verdict.target_name}</strong> · id {verdict.target_id}
            </p>
            <p className="verdict-body">{verdict.body}</p>
            <div className="row-btns">
              <button type="button" className="btn" onClick={addToWl}>
                В белый список
              </button>
              <button type="button" className="btn btn-outline" onClick={removeFromWl}>
                Убрать из WL
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="card">
        <h3>Список скама/WL</h3>
        {list.length === 0 ? (
          <p className="muted">Список пуст.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Имя</th>
                <th>Источник</th>
                <th>Причина</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((row) => (
                <tr key={row.user_id}>
                  <td>{row.user_id}</td>
                  <td>{row.name}</td>
                  <td>{sourceBadge(row.source)}</td>
                  <td className="cell-reason">{row.reason || '—'}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => removeRow(row.user_id)}
                    >
                      DEL
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
