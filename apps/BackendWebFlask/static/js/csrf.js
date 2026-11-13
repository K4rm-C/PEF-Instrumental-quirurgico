/* MS-04 (P7): CSRF token for the portal's own state-changing requests.
   The server renders the token in <meta name="csrf-token"> and in every POST form (csrf_token field).
   This script adds it to same-origin fetch/XMLHttpRequest calls (X-CSRF-Token header) and to POST forms
   built in the browser. Cross-origin calls (AuthService) never receive it. */
(function () {
    "use strict";
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (!meta || !meta.content) return;
    var token = meta.content;
    var SAFE = /^(GET|HEAD|OPTIONS|TRACE)$/i;

    function sameOrigin(url) {
        try { return new URL(url, window.location.href).origin === window.location.origin; }
        catch (e) { return false; }
    }

    if (window.fetch) {
        var nativeFetch = window.fetch;
        window.fetch = function (input, init) {
            init = init || {};
            var isRequest = typeof Request !== "undefined" && input instanceof Request;
            var method = init.method || (isRequest ? input.method : "GET");
            var url = isRequest ? input.url : String(input);
            if (!SAFE.test(method) && sameOrigin(url)) {
                var headers = new Headers(init.headers || (isRequest ? input.headers : undefined));
                if (!headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", token);
                init = Object.assign({}, init, {headers: headers});
            }
            return nativeFetch.call(this, input, init);
        };
    }

    var nativeOpen = XMLHttpRequest.prototype.open, nativeSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
        this._pefCsrf = !SAFE.test(method) && sameOrigin(url);
        return nativeOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
        if (this._pefCsrf) this.setRequestHeader("X-CSRF-Token", token);
        return nativeSend.apply(this, arguments);
    };

    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement) || (form.getAttribute("method") || "").toLowerCase() !== "post") return;
        if (!sameOrigin(form.action || window.location.href) || form.querySelector('input[name="csrf_token"]')) return;
        var input = document.createElement("input");
        input.type = "hidden"; input.name = "csrf_token"; input.value = token;
        form.appendChild(input);
    }, true);
})();
