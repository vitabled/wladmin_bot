import { useEffect, useState } from 'react';
import { api } from '../api';

// Statistics card + top-10 table for ONE chat, fed by GET /api/chats/{id}/stats.
// Extracted from the old standalone StatsPage so the same view can live inside
// the admin chat detail (Статистика lives under Администрирование now).
export default function StatsView({ chatId }) {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!chatId) {
      setStats(null);
      return;
    }
    let alive = true;
    setStats(null);
    setError(null);
    api
      .get(`/api/chats/${chatId}/stats`)
      .then((data) => {
        if (alive) setStats(data);
      })
      .catch((err) => {
        if (alive) setError(err.detail || 'Ошибка загрузки статистики');
      });
    return () => {
      alive = false;
    };
  }, [chatId]);

  if (error) return <p className="form-error">{error}</p>;
  if (!stats) return <div className="loading">Загрузка…</div>;

  return (
    <>
      <div className="chips">
        <div className="chip">
          <span className="chip-value">{stats.total}</span>
          <span className="chip-label">Сообщений всего</span>
        </div>
        <div className="chip">
          <span className="chip-value">{stats.users}</span>
          <span className="chip-label">Пользователей</span>
        </div>
        <div className="chip">
          <span className="chip-value">{stats.banned}</span>
          <span className="chip-label">Забанено</span>
        </div>
        <div className="chip">
          <span className="chip-value">{stats.warns}</span>
          <span className="chip-label">Предупреждений</span>
        </div>
      </div>

      <div className="card">
        <h3>Топ-10</h3>
        {stats.top.length === 0 ? (
          <p className="muted">Нет данных.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Место</th>
                <th>Имя</th>
                <th>Сообщений</th>
              </tr>
            </thead>
            <tbody>
              {stats.top.map((u, i) => (
                <tr key={u.user_id}>
                  <td>{i + 1}</td>
                  <td>{u.name}</td>
                  <td>{u.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
