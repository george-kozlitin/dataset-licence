// Motion: the lineage answers the pointer, counts run up once, the cut follows the pointer a little.
// prefers-reduced-motion: nothing moves, everything is shown in its final state.
(function () {
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var hover = window.matchMedia("(hover: hover)").matches;
  // The "js" class is set in <head>; it gates the hidden start states, so without JavaScript everything shows.

  // Lineage: focus one cell, its edge carries a pulse to the dataset, everything else steps back.
  function focus(k) {
    document.querySelectorAll(".lineage").forEach(function (svg) {
      svg.classList.toggle("focus", k != null);
      svg.querySelectorAll("[data-k]").forEach(function (el) { el.classList.toggle("on", el.getAttribute("data-k") === k); });
    });
    document.querySelectorAll(".facts [data-k], .srcs [data-k]").forEach(function (el) { el.classList.toggle("on", el.getAttribute("data-k") === k); });
  }
  document.querySelectorAll(".lineage .node, .facts [data-k], .srcs [data-k]").forEach(function (el) {
    el.addEventListener("mouseenter", function () { focus(el.getAttribute("data-k")); });
    el.addEventListener("mouseleave", function () { focus(null); });
    el.addEventListener("focusin", function () { focus(el.getAttribute("data-k")); });
    el.addEventListener("focusout", function () { focus(null); });
  });
  // Touch has no hover: the first tap on a cell shows what it links to, the second opens it.
  // Touch fires mouseenter before click, so remember whether the cell was already shown when the finger landed.
  document.querySelectorAll(".lineage .node").forEach(function (n) {
    var shown = false, touched = false;
    n.addEventListener("touchstart", function () { touched = true; shown = n.classList.contains("on"); }, { passive: true });
    n.addEventListener("click", function (e) {
      if (touched && !shown) { e.preventDefault(); focus(n.getAttribute("data-k")); }
      touched = false;
    });
  });
  document.querySelectorAll(".lineage .core").forEach(function (core) {
    var svg = core.ownerSVGElement;
    core.addEventListener("mouseenter", function () { svg.classList.add("all"); });
    core.addEventListener("mouseleave", function () { svg.classList.remove("all"); });
  });

  // Start the lineage sequence when the board comes into view.
  var boards = document.querySelectorAll(".board");
  if ("IntersectionObserver" in window && !reduce) {
    var bo = new IntersectionObserver(function (es) {
      es.forEach(function (en) {
        if (!en.isIntersecting) return;
        var b = en.target;
        b.classList.add("in"); bo.unobserve(b);
        // Once drawn, hand the cells back to plain styles so hover dimming can apply.
        setTimeout(function () { b.classList.add("done"); }, 1800);
      });
    }, { threshold: 0.25 });
    boards.forEach(function (b) { bo.observe(b); });
  } else boards.forEach(function (b) { b.classList.add("in", "done"); });

  // Counts run up once when they come into view.
  function count(el) {
    var to = +el.getAttribute("data-n"), t0 = null, dur = 1100 + Math.min(900, to / 20);
    function step(t) {
      if (t0 == null) t0 = t;
      var k = Math.min(1, (t - t0) / dur), v = Math.round(to * (1 - Math.pow(1 - k, 4)));
      el.textContent = v.toLocaleString("en");
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  var nums = document.querySelectorAll("[data-n]");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (en) { if (en.isIntersecting) { count(en.target); io.unobserve(en.target); } });
    }, { threshold: 0.6 });
    nums.forEach(function (n) { n.textContent = "0"; io.observe(n); });
  }

  // The cut: the cells behind the paper shift against the pointer, as if seen through a hole.
  var cut = document.querySelector(".cut"), cells = cut && cut.querySelector(".cells");
  if (cells && hover && !reduce) {
    var tx = 0, ty = 0, x = 0, y = 0, raf = 0;
    function tick() {
      x += (tx - x) * 0.08; y += (ty - y) * 0.08;
      cells.setAttribute("transform", "translate(" + x.toFixed(2) + " " + y.toFixed(2) + ")");
      raf = Math.abs(tx - x) + Math.abs(ty - y) > 0.05 ? requestAnimationFrame(tick) : 0;
    }
    window.addEventListener("pointermove", function (e) {
      var r = cut.getBoundingClientRect();
      tx = -((e.clientX - (r.left + r.width / 2)) / window.innerWidth) * 26;
      ty = -((e.clientY - (r.top + r.height / 2)) / window.innerHeight) * 18;
      if (!raf) raf = requestAnimationFrame(tick);
    }, { passive: true });
  }

  // Search key: a quiet ping until it has been opened once in this tab.
  var area = document.getElementById("sarea");
  if (area) {
    var used = false;
    try { used = sessionStorage.getItem("dl-used") === "1"; } catch (e) {}
    if (!used && !reduce) area.classList.add("ping");
    area.addEventListener("click", function () { area.classList.remove("ping"); try { sessionStorage.setItem("dl-used", "1"); } catch (e) {} });
    area.addEventListener("mouseenter", function () { area.classList.remove("ping"); try { sessionStorage.setItem("dl-used", "1"); } catch (e) {} });
  }
})();
