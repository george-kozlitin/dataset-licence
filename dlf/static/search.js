// Search: a dark key that opens into a field; matches drop down under it, the typed part in ink, the rest muted.
// IDs come from /data/ids.json; rows also in /data/index.json get a subline. A full owner/name ID outside
// ids.json is looked up in /data/d/<id>.json, and failing that the live check (live.js) is offered.
(function () {
  var box = document.getElementById("sbox");
  if (!box) return;
  var area = box.parentNode, input = box.querySelector("input"), toggle = box.querySelector(".s-toggle");
  var list = box.querySelector(".s-list"), liveBox = document.getElementById("live");
  var ids = null, info = null, loading = false, failed = false, active = -1, timer = 0, seq = 0;
  var FULL = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
  var LABELS = { permissive: "Permissive or share-alike", restrictive: "Use restrictions", conflict: "Possible licence conflict", unclear: "Unclear" };
  var TONES = { permissive: "ok", restrictive: "warn", conflict: "bad", unclear: "unk" };

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function url(id) { return "/d/" + id.split("/").map(encodeURIComponent).join("/") + "/"; }
  function json(u) { return fetch(u).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }

  function load() {
    if (ids || loading) return;
    loading = true; failed = false;
    json("/data/ids.json").then(function (d) {
      ids = d; render();
      // The subline is optional: the search works without it.
      json("/data/index.json").then(function (rows) {
        info = {};
        rows.forEach(function (r) { info[r[0]] = r; });
        render();
      }).catch(function () {});
    }).catch(function () { loading = false; failed = true; render(); });
  }
  function open() {
    if (box.classList.contains("open")) return;
    box.classList.add("open"); area.classList.add("open");
    toggle.setAttribute("aria-label", "Close search");
    load(); render();
    setTimeout(function () { input.focus({ preventScroll: true }); }, 180);
  }
  // Closing keeps the query and its matches: reopening (here or after visiting a page) shows them again.
  function close() {
    box.classList.remove("open", "has"); area.classList.remove("open");
    toggle.setAttribute("aria-label", "Open search");
    input.blur();
  }
  function save() { try { sessionStorage.setItem("dl-q", input.value); } catch (e) {} }

  function row(id, r, q) {
    var li = el("li"), a = el("a"), t = el("span", "s-id"), at = q ? id.toLowerCase().indexOf(q) : -1;
    a.href = url(id);
    if (at < 0) t.appendChild(el("b", null, id));
    else {
      t.appendChild(el("span", "s-dim", id.slice(0, at)));
      t.appendChild(el("b", null, id.slice(at, at + q.length)));
      t.appendChild(el("span", "s-dim", id.slice(at + q.length)));
    }
    a.appendChild(t);
    if (r) {
      var sub = el("span", "s-sub");
      sub.appendChild(el("i", "hx " + (TONES[r[1]] || "unk")));
      sub.appendChild(document.createTextNode((LABELS[r[1]] || LABELS.unclear) + (r[2] ? ", " + r[2] : "") + ", " + Number(r[4]).toLocaleString("en") + " downloads in 30 days"));
      a.appendChild(sub);
    }
    li.appendChild(a);
    return li;
  }
  function live(raw) {
    var b = liveBox && window.dlLiveButton && window.dlLiveButton(raw, liveBox);
    if (!b) return;
    // The card renders below the search; close the drop-down so it does not cover it.
    b.addEventListener("click", function (e) { e.stopPropagation(); close(); liveBox.scrollIntoView({ block: "nearest", behavior: "smooth" }); });
    var li = el("li", "live-row");
    li.appendChild(b);
    list.appendChild(li);
    box.classList.add("has");
  }
  // A full owner/name ID outside ids.json: its facts exist for every dataset in the build.
  function lookup(raw, n, mine) {
    json("/data/d/" + raw.split("/").map(encodeURIComponent).join("/") + ".json").catch(function () { return null; }).then(function (f) {
      if (mine !== seq) return;
      if (f && f.id) {
        if (!n) list.textContent = "";
        list.insertBefore(row(f.id, null, ""), list.firstChild);
        box.classList.add("has");
      } else live(raw);
    });
  }

  function render() {
    var raw = input.value.trim(), q = raw.toLowerCase(), mine = ++seq;
    clearTimeout(timer);
    list.textContent = ""; active = -1;
    box.classList.remove("has");
    if (!q || !box.classList.contains("open")) return;
    if (failed) {
      list.appendChild(el("li", "s-empty", "Could not load the dataset search index. Reload the page to try again."));
      box.classList.add("has");
      return;
    }
    if (!ids) return;
    var starts = [], inside = [], exact = false;
    for (var i = 0; i < ids.length; i++) {
      var id = ids[i], low = id.toLowerCase(), at = low.indexOf(q);
      if (at < 0) continue;
      if (low === q) { exact = true; starts.unshift(id); }
      else if (at === 0 || low.split("/").pop().indexOf(q) === 0) { if (starts.length < 9) starts.push(id); }
      else if (inside.length < 8) inside.push(id);
    }
    var hits = starts.concat(inside).slice(0, 8);
    hits.forEach(function (id) { list.appendChild(row(id, info && info[id], q)); });
    if (!hits.length) list.appendChild(el("li", "s-empty", "No match among the 50,000 most downloaded datasets. Type a full ID, owner/name, to look up any other dataset."));
    box.classList.add("has");
    if (exact) return;
    if (FULL.test(raw)) timer = setTimeout(function () { lookup(raw, hits.length, mine); }, 400);
    else if (!hits.length) live(raw);
  }
  function move(d) {
    var items = list.querySelectorAll("a");
    if (!items.length) return;
    active = (active + d + items.length) % items.length;
    items.forEach(function (a, i) { a.classList.toggle("on", i === active); });
    items[active].scrollIntoView({ block: "nearest" });
  }

  try { input.value = sessionStorage.getItem("dl-q") || ""; } catch (e) {}
  toggle.addEventListener("click", function (e) { e.stopPropagation(); box.classList.contains("open") ? close() : open(); });
  area.addEventListener("click", open);
  area.addEventListener("mouseenter", function () { if (matchMedia("(hover: hover)").matches) open(); });
  input.addEventListener("focus", open);
  input.addEventListener("input", function () { save(); render(); });
  document.addEventListener("pointerdown", function (e) { if (box.classList.contains("open") && !area.contains(e.target)) close(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Escape") close();
    else if (e.key === "Enter") { var a = list.querySelectorAll("a")[Math.max(active, 0)]; if (a) location.href = a.href; }
  });
  document.addEventListener("keydown", function (e) {
    var t = e.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
    if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); open(); }
  });
  // /#q (the not-found page links there) opens the search.
  if (location.hash === "#q") open();
})();
