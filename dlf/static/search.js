(function () {
  var q = document.getElementById("q"), out = document.getElementById("results"), data = null, loading = false;
  if (!q || !out) return;
  function url(id) {
    return "/d/" + id.split("/").map(encodeURIComponent).join("/") + "/";
  }
  function show() {
    var s = q.value.trim().toLowerCase();
    out.textContent = "";
    if (!s || !data) return;
    var n = 0, exact = false;
    for (var i = 0; i < data.length; i++) {
      if (data[i][0].toLowerCase() === s) exact = true;
      if (n >= 20 || data[i][0].toLowerCase().indexOf(s) < 0) continue;
      var li = document.createElement("li"), a = document.createElement("a");
      a.href = url(data[i][0]);
      a.textContent = data[i][0];
      li.appendChild(a);
      out.appendChild(li);
      n++;
    }
    if (!n) {
      var e = document.createElement("li");
      e.textContent = "No matching dataset IDs in this site's index.";
      out.appendChild(e);
    }
    var box = document.getElementById("live"), b = !exact && box && window.dlLiveButton && window.dlLiveButton(q.value.trim(), box);
    if (b) {
      var li2 = document.createElement("li");
      li2.className = "live-row";
      li2.appendChild(b);
      out.appendChild(li2);
    }
  }
  q.addEventListener("input", function () {
    if (data) return show();
    if (loading) return;
    loading = true;
    fetch("/data/index.json").then(function (r) {
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
