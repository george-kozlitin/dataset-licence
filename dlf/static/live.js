// Live check of a dataset that has no page on this site: asks the Worker, which reads the Hub now.
(function () {
  var API = "https://mcp.datalicense.uk/v1/check?id=";
  var ID_RE = /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)?$/;
  var LABELS = {
    permissive: "Permissive or share-alike",
    restrictive: "Use restrictions",
    conflict: "Possible licence conflict",
    unclear: "Unclear"
  };
  var TONES = { permissive: "ok", restrictive: "warn", conflict: "bad", unclear: "unk" };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function evidence(url) {
    if (!url) return null;
    var m = url.match(/\/(blob|tree)\/([^/]+)\/?(.*)$/), name;
    if (m) name = (m[1] === "tree" ? "file list" : decodeURIComponent(m[3].split("/").pop())) + (/^[0-9a-f]{40}$/.test(m[2]) ? " at " + m[2].slice(0, 7) : "");
    else if (url.indexOf("/api/") > 0) name = "Hub API record" + ((m = url.match(/\/revision\/([0-9a-f]{40})$/)) ? " at " + m[1].slice(0, 7) : "");
    else name = "Hub page";
    var a = el("a", "ev", "Evidence: " + name);
    a.href = url;
    return a;
  }
  function fact(list, label, value, ev) {
    var li = el("li"), v = el("span");
    li.appendChild(el("span", null, label));
    v.appendChild(typeof value === "string" ? document.createTextNode(value) : value);
    var a = evidence(ev);
    if (a) v.appendChild(a);
    li.appendChild(v);
    list.appendChild(li);
  }
  function when(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var hm = ("0" + d.getUTCHours()).slice(-2) + ":" + ("0" + d.getUTCMinutes()).slice(-2);
    return d.getUTCDate() + " " + months[d.getUTCMonth()] + " " + d.getUTCFullYear() + ", " + hm + " UTC";
  }
  function render(box, body) {
    var f = body.facts, card = el("div", "live-card");
    var head = el("div", "live-head");
    head.appendChild(el("h2", "mono", f.id));
    var plate = el("span", "plate " + (TONES[f.signal] || "unk"));
    plate.appendChild(el("i", "hx " + (TONES[f.signal] || "unk")));
    plate.appendChild(document.createTextNode(LABELS[f.signal] || LABELS.unclear));
    head.appendChild(plate);
    card.appendChild(head);
    card.appendChild(el("p", "reason", f.reason));
    var list = el("ul", "facts");
    fact(list, "Declared licence",
      f.stated_licence ? f.stated_licence + ", from the " + f.stated_from : "Not declared in the tag or the card",
      f.stated_evidence);
    fact(list, "Licence files",
      f.licence_files.length
        ? f.licence_files.join(", ") + (f.file_licence ? ", detected as " + f.file_licence : ", licence not identified")
        : "No licence file at the top level of the repository",
      f.file_evidence);
    if (!f.sources.length) fact(list, "Declared sources", "None listed in the dataset card metadata", null);
    f.sources.forEach(function (s) {
      fact(list, "Source " + s.id, s.found ? (s.licence || "no licence in metadata") : "not found on the Hub", s.evidence);
    });
    if (f.sources_omitted) fact(list, "Sources not checked", f.sources_omitted + " more listed in the dataset card metadata", null);
    card.appendChild(list);
    var note = el("p", "hint", "Checked live on the Hugging Face Hub, " + when(body.checked_at) +
      ". This dataset has no page on this site yet. ");
    var hf = el("a", null, "View on Hugging Face");
    hf.href = "https://huggingface.co/datasets/" + f.id.split("/").map(encodeURIComponent).join("/");
    note.appendChild(hf);
    card.appendChild(note);
    card.appendChild(el("p", "hint", body.disclaimer));
    box.textContent = "";
    box.appendChild(card);
  }

  function check(id, box) {
    box.textContent = "";
    box.appendChild(el("p", "hint", "Checking " + id + " on the Hugging Face Hub..."));
    fetch(API + encodeURIComponent(id)).then(function (r) {
      return r.json().then(function (b) { return { ok: r.ok, body: b }; });
    }).then(function (res) {
      if (!res.ok) throw new Error(res.body && res.body.message);
      render(box, res.body);
    }).catch(function (e) {
      box.textContent = "";
      box.appendChild(el("p", "hint", (e && e.message) || "The live check failed. Try again later."));
    });
  }

  // A button that runs the check for one ID into box.
  window.dlLiveButton = function (id, box) {
    if (!ID_RE.test(id) || id.split("/").some(function (p) { return /^\.+$/.test(p); })) return null;
    var b = el("button", "live-btn", "Check " + id + " on the Hugging Face Hub");
    b.type = "button";
    b.addEventListener("click", function () { check(id, box); });
    return b;
  };

  // On the not-found page for /d/<id>/, offer the check for that ID.
  var slot = document.getElementById("live-404");
  var m = location.pathname.match(/^\/d\/(.+?)\/?$/);
  if (slot && m) {
    var id = decodeURIComponent(m[1]), box = document.getElementById("live");
    var b = window.dlLiveButton(id, box);
    if (b) {
      slot.appendChild(el("p", null, id + " has no page on this site yet. You can check its licence facts on the Hub now."));
      slot.appendChild(b);
    }
  }
})();
