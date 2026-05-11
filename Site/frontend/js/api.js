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
          // FastAPI emballe les HTTPException dans {detail: ...}. Le détail
          // peut être :
          //  - une string : message simple
          //  - un dict : {error: "...", message: "..."}
          //  - une liste : erreurs de validation pydantic
          //  - autre : payload custom
          let msg = 'HTTP ' + r.status;
          if (typeof d === 'string') {
            msg = d;
          } else if (d) {
            const detail = d.detail;
            if (typeof detail === 'string') {
              msg = detail;
            } else if (detail && typeof detail === 'object') {
              msg = detail.message || detail.error
                || (Array.isArray(detail) && detail[0] && (detail[0].msg || detail[0].message))
                || JSON.stringify(detail).slice(0, 200);
            } else {
              msg = d.message || d.error || msg;
            }
            // Ajoute le code HTTP en suffixe pour debug
            msg = msg + ' [HTTP ' + r.status + ']';
          }
          const err = new Error(msg);
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
