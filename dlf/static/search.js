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
    var n = 0;
    for (var i = 0; i < data.length && n < 20; i++) {
      if (data[i][0].toLowerCase().indexOf(s) < 0) continue;
      var li = document.createElement("li"), a = document.createElement("a");
      a.href = url(data[i][0]);
      a.textContent = data[i][0];
      li.appendChild(a);
      out.appendChild(li);
      n++;
    }
    if (!n) {
      var e = document.createElement("li");
      e.textContent = "No dataset id contains that text.";
      out.appendChild(e);
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
      e.textContent = "Search could not load. Reload the page to try again.";
      out.appendChild(e);
    });
  });
})();
