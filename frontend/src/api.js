export function createApi(key) {
  return async (path, method = 'GET', body) => {
    const response = await fetch(path, {
      method,
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.error?.message || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  };
}

export function parseAccountFile(text, name) {
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const end = lines.indexOf('[');
  const header = lines.slice(0, end >= 0 ? end : 20);
  return {
    name: name.replace(/\.txt$/i, ''), sessionKey: lines[0] || '',
    proxy: header.find(line => /^(?:\w+:\/\/|\d+\.\d+\.\d+\.\d+:\d+)/.test(line)) || '',
    user_agent: header.find(line => line.startsWith('Mozilla/')) || '',
    os_label: header.find(line => /^(?:macOS|Windows|Linux|Android)\S*/i.test(line)) || '',
    proxy_username: '', proxy_password: '',
  };
}
