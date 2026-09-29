/*
  UI strings built by JavaScript, localized by the server. base.html renders them once per page
  (localization.js_messages(), gettext in the current UI language) into
  <script type="application/json" id="pef-i18n">; scripts read them with PEF_I18N.t(key).
  No translation logic lives in JavaScript: an unknown key returns the given fallback.
*/
(function () {
    "use strict";

    var messages = {};
    try {
        var source = document.getElementById("pef-i18n");
        messages = source ? JSON.parse(source.textContent) : {};
    } catch (error) {
        messages = {};
    }

    window.PEF_I18N = {
        locale: document.documentElement.getAttribute("lang") || "en",
        t: function (key, fallback) {
            return Object.prototype.hasOwnProperty.call(messages, key) ? messages[key] : (fallback || key);
        },
    };
})();
