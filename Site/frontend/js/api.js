// VoiceBridge — api.js
// Wrapper fetch unifié. Toutes les requêtes envoient le cookie de session
// (``credentials: 'same-origin'``) et reçoivent du JSON.

(function () {
  function request(method, path, body) {
    const opts = {
      method: method,
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    };
    if (body !== undefined) {
      if (body instanceof FormData) {
        opts.body = body;
      } else {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
      }
    }
    return fetch(path, opts).then(function (r) {
      const ct = r.headers.get('content-type') || '';
      const data = ct.indexOf('application/json') >= 0 ? r.json() : r.text();
      if (!r.ok) {
        return Promise.resolve(data).then(function (d) {
          const err = new Error(typeof d === 'string' ? d : (d.message || d.error || ('HTTP ' + r.status)));
          err.status = r.status;
          err.payload = d;
          throw err;
        });
      }
      return data;
    });
  }

  window.VB = window.VB || {};
  window.VB.api = {
    get:  function (p)    { return request('GET',  p); },
    post: function (p, b) { return request('POST', p, b === undefined ? null : b); },
    put:  function (p, b) { return request('PUT',  p, b === undefined ? null : b); },
    del:  function (p)    { return request('DELETE', p); },
  };
})();
