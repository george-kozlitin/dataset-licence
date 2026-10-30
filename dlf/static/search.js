(function () {
  var q = document.getElementById("q"), out = document.getElementById("results"), data = null, loading = false;
  var FULL = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/, timer = 0, seq = 0;
  if (!q || !out) return;
  function url(id) {
    return "/d/" + id.split("/").map(encodeURIComponent).join("/") + "/";
  }
  function row(id) {
    var li = document.createElement("li"), a = document.createElement("a");
    a.href = url(id);
    a.textContent = id;
    li.appendChild(a);
    return li;
  }
  function live(raw) {
    var box = document.getElementById("live"), b = box && window.dlLiveButton && window.dlLiveButton(raw, box);
    if (b) {
      var li = document.createElement("li");
      li.className = "live-row";
      li.appendChild(b);
      out.appendChild(li);
    }
  }
  // A full owner/name ID outside ids.json: ask for its facts, which exist for every dataset on the site.
  function lookup(raw, n, mine) {
    fetch("/data/d/" + raw.split("/").map(encodeURIComponent).join("/") + ".json").then(function (r) {
      return r.ok ? r.json() : null;
    }).catch(function () { return null; }).then(function (f) {
      if (mine !== seq) return;
      if (f && f.id) {
        if (!n) out.textContent = "";
        out.insertBefore(row(f.id), out.firstChild);
      } else live(raw);
    });
  }
  function show() {
    var raw = q.value.trim(), s = raw.toLowerCase(), mine = ++seq;
    clearTimeout(timer);
    out.textContent = "";
    if (!s || !data) return;
    var n = 0, exact = false;
    for (var i = 0; i < data.length; i++) {
      var id = data[i], low = id.toLowerCase();
      if (low === s) exact = true;
      if (n >= 20 || low.indexOf(s) < 0) continue;
      out.appendChild(row(id));
      n++;
    }
    if (!n) {
      var e = document.createElement("li");
      e.textContent = "No matching dataset IDs in this site's index.";
      out.appendChild(e);
    }
    if (exact) return;
    if (FULL.test(raw)) timer = setTimeout(function () { lookup(raw, n, mine); }, 400);
    else live(raw);
  }
  q.addEventListener("input", function () {
    if (data) return show();
    if (loading) return;
    loading = true;
    fetch("/data/ids.json").then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (d) {
      data = d;
      show();
    }).catch(function () {
      loading = false;
      out.textContent = "";
      var e = document.createElement("li");
      e.textContent = "Could not load the dataset search index. Reload the page to try again.";
      out.appendChild(e);
    });
  });
})();
