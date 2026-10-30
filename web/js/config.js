/* Where the PDF -> Word conversion service lives.

   Priority:
     1. ?api=https://... query parameter (handy for testing)
     2. localStorage "ps_api" (set from the settings box in the UI)
     3. window.PDF_STUDIO_API (build time default, empty = same origin)
     4. same origin (works when the Python server also serves the page)
*/
(function () {
  const PS = (window.PS = window.PS || {});

  const params = new URLSearchParams(location.search);
  const fromQuery = params.get("api");
  if (fromQuery) localStorage.setItem("ps_api", fromQuery.trim());

  const stored = (localStorage.getItem("ps_api") || "").trim();
  PS.apiBase = stored || (window.PDF_STUDIO_API || "").trim();

  PS.apiUrl = function (path) {
    if (!PS.apiBase) return path;
    return PS.apiBase.replace(/\/+$/, "") + path;
  };

  PS.setApiBase = function (url) {
    PS.apiBase = (url || "").trim();
    if (PS.apiBase) localStorage.setItem("ps_api", PS.apiBase);
    else localStorage.removeItem("ps_api");
  };

  PS.isHosted = function () {
    return /\.github\.io$/.test(location.hostname) || location.protocol === "file:";
  };
})();
