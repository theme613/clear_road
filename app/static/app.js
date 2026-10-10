// Clear Road front end: search, prediction panel, map lines. Plain JS, no build.
// Icons: Lucide (<i data-lucide="name">), turned into SVG by lucide.createIcons().
const $ = (id) => document.getElementById(id);
const KL_CENTER = [3.1, 101.67];
// Tailwind classes per mode (full class names so Tailwind can find them)
const MODE_INFO = {
  car: {
    icon: "car", label: "Car", verdict: "Drive",
    tint: "bg-blue-50 border-blue-200", edge: "border-blue-200", ink: "text-blue-950", accent: "text-blue-600",
    solid: "bg-blue-600", soft: "bg-blue-50 text-blue-600", sel: "bg-blue-50 shadow-[inset_4px_0_0_var(--color-blue-600)]",
  },
  train: {
    icon: "train-front", label: "Train", verdict: "Take the train",
    tint: "bg-green-50 border-green-200", edge: "border-green-200", ink: "text-green-950", accent: "text-green-600",
    solid: "bg-green-600", soft: "bg-green-50 text-green-600", sel: "bg-green-50 shadow-[inset_4px_0_0_var(--color-green-600)]",
  },
  bus: {
    icon: "bus", label: "Bus", verdict: "Take the bus",
    tint: "bg-purple-50 border-purple-200", edge: "border-purple-200", ink: "text-purple-950", accent: "text-purple-600",
    solid: "bg-purple-600", soft: "bg-purple-50 text-purple-600", sel: "bg-purple-50 shadow-[inset_4px_0_0_var(--color-purple-600)]",
  },
};
const GREY = "#9aa0a6";
const BUS_COLOR = "#9333ea";

const PLACES = {
  puchong: { name: "IOI Puchong Jaya LRT station", lat: 3.048101, lon: 101.62095 },
  klcc: { name: "KLCC (Petronas Twin Towers)", lat: 3.1579, lon: 101.7116 },
};

const state = {
  from: PLACES.puchong,
  to: PLACES.klcc,
  plan: null,
  selected: null,
  rainMoved: false,
};
let map = null;
let routeLayer = null;
let markerLayer = null;
let routeBounds = null;
let fitZoom = null;

// ---------- helpers ----------
function fmtMin(m) {
  if (m == null) return "–";
  const r = Math.round(m);
  return r < 60 ? `${r} min` : `${Math.floor(r / 60)} h ${r % 60} min`;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// <i data-lucide="name"> placeholder; drawIcons() swaps it for the SVG (classes are kept)
function icon(name, cls = "size-4 shrink-0") {
  const i = document.createElement("i");
  i.setAttribute("data-lucide", name);
  i.className = cls;
  return i;
}

function drawIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function isoDate(d) {
  const x = new Date(d);
  x.setMinutes(x.getMinutes() - x.getTimezoneOffset());
  return x.toISOString().slice(0, 10);
}

function localDate(offsetDays) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return isoDate(d);
}

// Next date (after today) whose weekday is in the list (0 = Sunday ... 6 = Saturday)
function nextDay(weekdays) {
  const d = new Date();
  do { d.setDate(d.getDate() + 1); } while (!weekdays.includes(d.getDay()));
  return isoDate(d);
}

// "08:00" + 51 min -> "08:51" (adds "+1" if it passes midnight)
function arrive(time, minutes) {
  const [h, m] = time.split(":").map(Number);
  const total = h * 60 + m + Math.round(minutes);
  const day = Math.floor(total / 1440);
  const t = total % 1440;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}${day ? " +1" : ""}`;
}

function option(mode) {
  return state.plan ? state.plan.options.find((o) => o.mode === mode) : null;
}

function trafficDotColor(mult) {
  return mult < 1.3 ? "#16a34a" : mult < 1.7 ? "#f59e0b" : "#dc2626";
}

async function api(url, options) {
  const resp = await fetch(url, options);
  let body = null;
  try { body = await resp.json(); } catch (e) { /* not JSON */ }
  if (!resp.ok) throw new Error((body && body.error) || `Request failed (${resp.status})`);
  return body;
}

function setStatus(text) {
  $("status").textContent = text || "";
  $("status").classList.toggle("hidden", !text);
}

function setError(text) {
  $("error").textContent = text || "";
  $("error").classList.toggle("hidden", !text);
}

// ---------- map ----------
function initMap() {
  if (typeof L === "undefined") {
    $("map").classList.add("hidden");
    $("mapOff").classList.remove("hidden");
    return;
  }
  map = L.map("map", { zoomControl: false, attributionControl: true }).setView(KL_CENTER, 11);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  addBaseMap();
  routeLayer = L.layerGroup().addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  map.on("moveend", checkBackButton);
}

function addOsmTiles() {
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
}

// Free vector map from OpenFreeMap (no key). Falls back to plain OSM tiles.
function addBaseMap() {
  if (!L.maplibreGL || typeof maplibregl === "undefined") {
    addOsmTiles();
    return;
  }
  try {
    const gl = L.maplibreGL({
      style: "https://tiles.openfreemap.org/styles/liberty",
      attribution: '<a href="https://openfreemap.org">OpenFreeMap</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    let switched = false;
    gl.getMaplibreMap().on("error", (e) => {
      if (switched || !e || !e.error || !/style/i.test(String(e.error.message || ""))) return;
      switched = true;
      map.removeLayer(gl);
      addOsmTiles();
    });
  } catch (e) {
    console.error("vector map failed, using OSM tiles", e);
    addOsmTiles();
  }
}

function line(coords, opts) {
  return L.polyline(coords, { lineCap: "round", lineJoin: "round", ...opts });
}

function drawOption(opt, selected) {
  const group = L.featureGroup();
  if (opt.mode === "car") {
    line(opt.coords, { color: selected ? "#174ea6" : "#80868b", weight: selected ? 10 : 8, opacity: selected ? 0.9 : 0.6 }).addTo(group);
    if (selected) {
      opt.segments.forEach((s) => line(s.coords, { color: s.color, weight: 6 }).addTo(group));
    } else {
      line(opt.coords, { color: "#bdc1c6", weight: 5 }).addTo(group);
    }
  } else if (opt.mode === "train") {
    opt.legs.forEach((leg) => {
      if (leg.type === "ride") {
        line(leg.coords, { color: "#fff", weight: selected ? 11 : 9, opacity: selected ? 1 : 0.7 }).addTo(group);
        line(leg.coords, { color: selected ? leg.color : GREY, weight: selected ? 7 : 5, opacity: selected ? 1 : 0.8 }).addTo(group);
      } else if (leg.type === "walk" || leg.type === "transfer") {
        line(leg.coords, { color: selected ? "#5f6368" : GREY, weight: 5, dashArray: "1 9" }).addTo(group);
      } else if (leg.type === "feeder") {
        line(leg.coords, { color: selected ? "#3c4043" : GREY, weight: 4, dashArray: "8 8" }).addTo(group);
      }
    });
    if (selected) {
      opt.legs.filter((l) => l.type === "ride").forEach((leg) => {
        [leg.coords[0], leg.coords[leg.coords.length - 1]].forEach((pt, i) => {
          L.circleMarker(pt, { radius: 6, color: leg.color, weight: 3, fillColor: "#fff", fillOpacity: 1 })
            .bindTooltip(i === 0 ? leg.from : leg.to, { className: "map-label", direction: "top", offset: [0, -6] })
            .addTo(group);
        });
      });
    }
  } else if (opt.mode === "bus") {
    line(opt.coords, { color: selected ? BUS_COLOR : GREY, weight: selected ? 6 : 4, opacity: selected ? 0.9 : 0.5, dashArray: "10 8" }).addTo(group);
  }
  group.on("click", () => selectMode(opt.mode));
  return group;
}

function fitRoute() {
  if (!map || !routeBounds) return;
  const phone = window.innerWidth <= 760;
  const top = $("searchCard").getBoundingClientRect().bottom + 16;
  const pad = phone
    ? { paddingTopLeft: [24, top], paddingBottomRight: [24, $("results").offsetHeight + 16] }
    : { paddingTopLeft: [460, 70], paddingBottomRight: [60, 40] };
  fitZoom = map.getBoundsZoom(routeBounds, false, L.point(pad.paddingTopLeft).add(pad.paddingBottomRight));
  map.fitBounds(routeBounds, pad);
  $("backToRoute").classList.add("hidden");
}

// Show "Back to my route" when the user has panned or zoomed away from the route
function checkBackButton() {
  if (!routeBounds || fitZoom == null) return;
  const view = map.getBounds();
  const away = !view.contains(routeBounds.pad(-0.25)) || Math.abs(map.getZoom() - fitZoom) >= 2;
  $("backToRoute").classList.toggle("hidden", !away);
}

function drawMap(refit) {
  if (!map || !state.plan) return;
  routeLayer.clearLayers();
  markerLayer.clearLayers();
  const opts = state.plan.options;
  opts.filter((o) => o.mode !== state.selected).forEach((o) => drawOption(o, false).addTo(routeLayer));
  const sel = opts.find((o) => o.mode === state.selected);
  const selLayer = drawOption(sel, true).addTo(routeLayer);

  const a = L.marker([state.from.lat, state.from.lon], { icon: L.divIcon({ className: "", html: '<div class="pin-a"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }) });
  const b = L.marker([state.to.lat, state.to.lon], { icon: L.divIcon({ className: "", html: '<div class="pin-b"></div>', iconSize: [26, 26], iconAnchor: [13, 26] }) });
  a.bindTooltip(state.from.name, { className: "map-label", direction: "left", offset: [-10, 0] }).addTo(markerLayer);
  b.bindTooltip(state.to.name, { className: "map-label", direction: "right", offset: [12, -14], permanent: true }).addTo(markerLayer);

  routeBounds = selLayer.getBounds().extend([state.from.lat, state.from.lon]).extend([state.to.lat, state.to.lon]);
  opts.forEach((o) => { if (o.coords) o.coords.forEach((c) => routeBounds.extend(c)); });
  if (refit !== false) fitRoute();
}

// ---------- search / autocomplete ----------
function setupSearch(which) {
  const input = $(`${which}Input`);
  const list = $(`${which}List`);
  let timer = null;
  let items = [];
  let active = -1;

  function close() { list.classList.add("hidden"); active = -1; }

  function choose(p) {
    state[which] = p;
    input.value = p.name;
    close();
    if (state.from && state.to) plan();
  }

  function render(results, withLocation) {
    list.innerHTML = "";
    items = [];
    if (withLocation && navigator.geolocation) items.push({ special: "location" });
    results.forEach((r) => items.push(r));
    if (!items.length) { close(); return; }
    items.forEach((it, i) => {
      const btn = el("button", "flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left hover:bg-zinc-100");
      btn.type = "button";
      const ic = el("span", "pt-0.5 text-zinc-500");
      ic.append(icon(it.special ? "locate" : (/station/i.test(it.name) ? "train-front" : "map-pin")));
      const txt = el("span");
      txt.appendChild(el("div", "text-sm font-medium", it.special ? "Your location" : it.name));
      if (!it.special && it.address) txt.appendChild(el("div", "text-xs text-zinc-500", it.address));
      btn.append(ic, txt);
      btn.addEventListener("mousedown", (e) => { e.preventDefault(); pickIndex(i); });
      list.appendChild(btn);
    });
    list.classList.remove("hidden");
    drawIcons();
  }

  function pickIndex(i) {
    const it = items[i];
    if (!it) return;
    if (it.special === "location") {
      close();
      input.value = "Finding your location…";
      navigator.geolocation.getCurrentPosition(
        (pos) => choose({ name: "Your location", lat: pos.coords.latitude, lon: pos.coords.longitude }),
        () => { input.value = state[which] ? state[which].name : ""; setError("Could not get your location."); },
        { timeout: 10000 },
      );
      return;
    }
    choose(it);
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { render([], which === "from"); return; }
    list.innerHTML = "";
    list.append(el("div", "px-3 py-2.5 text-zinc-500", "Searching…"));
    list.classList.remove("hidden");
    timer = setTimeout(async () => {
      try {
        const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
        if (input.value.trim() === q) render(r.results, false);
      } catch (e) {
        close();
      }
    }, 300);
  });
  input.addEventListener("focus", () => { input.select(); if (which === "from") render([], true); });
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (e) => {
    const buttons = list.querySelectorAll("button");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = Math.max(0, Math.min(buttons.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
      buttons.forEach((b, i) => b.classList.toggle("bg-zinc-100", i === active));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!list.classList.contains("hidden") && items.length) pickIndex(active >= 0 ? active : 0);
    } else if (e.key === "Escape") {
      close();
    }
  });
}

// ---------- results panel ----------
function renderHero() {
  const p = state.plan;
  const rec = option(p.recommended);
  const car = option("car");
  const train = option("train");
  const info = MODE_INFO[rec.mode];
  const box = $("hero");
  box.innerHTML = "";
  box.className = `m-3 rounded-xl border p-4 max-md:mx-2.5 max-md:mt-1 ${info.tint}`;

  const day = new Date(`${p.date}T00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  const label = el("div", `inline-flex items-center gap-1.5 rounded-full border bg-white px-2.5 py-0.5 text-xs font-semibold ${info.edge} ${info.accent}`);
  label.append(icon("sparkles", "size-3.5"), `AI prediction · ${day}, ${p.time}`);
  box.append(label);

  const verdict = el("div", `mt-3 mb-1.5 flex items-center gap-3 text-2xl font-bold tracking-tight max-md:text-xl ${info.ink}`);
  const mark = el("span", `grid size-10 place-items-center rounded-[10px] text-white ${info.solid}`);
  mark.append(icon(info.icon, "size-5"));
  verdict.append(mark, el("span", null, info.verdict));
  box.append(verdict);

  const when = el("div", `text-[15px] ${info.ink}`);
  when.append("Leave ", el("b", "font-semibold", p.time), " → Arrive ", el("b", "font-semibold", arrive(p.time, rec.minutes)));
  when.append(el("span", "text-zinc-500", ` · ${fmtMin(rec.minutes)}`));
  box.append(when);

  if (rec.mode !== "car" && p.saved_minutes >= 1) {
    box.append(el("span", "mt-2.5 inline-block rounded-full border border-green-200 bg-white px-2.5 py-0.5 text-xs font-semibold text-green-700",
      `Saves ${Math.round(p.saved_minutes)} min vs driving`));
  }

  let reason;
  if (rec.mode !== "car") {
    reason = `Driving would arrive ${arrive(p.time, car.minutes)}` +
      (car.delay_minutes >= 3 ? ` because of ${car.traffic.toLowerCase()} (+${Math.round(car.delay_minutes)} min).` : ".");
  } else if (train) {
    reason = `The train would arrive ${arrive(p.time, train.minutes)}, ${Math.round(train.minutes - car.minutes)} min later.`;
  } else {
    reason = "No train line near this trip.";
  }
  box.append(el("p", "mt-2.5 leading-relaxed text-zinc-600", reason));

  const w = p.weather;
  const facts = el("div", "mt-2.5 flex flex-wrap gap-x-3.5 gap-y-1.5 text-[12.5px] text-zinc-600");
  const wx = el("span", "inline-flex items-center gap-1.5");
  const temp = w.temp_c != null ? `${Math.round(w.temp_c)}°C · ` : "";
  const src = { "open-meteo": "forecast", override: "what-if", fallback: "no forecast" }[w.source] || w.source;
  wx.append(icon(w.rain_mm >= 0.5 ? "cloud-rain" : "cloud-sun", "size-3.5"), `${temp}${w.rain_mm} mm rain (${src})`);
  facts.append(wx);
  const tr = el("span", "inline-flex items-center gap-1.5");
  const dot = el("span", "size-2 rounded-full");
  dot.style.background = trafficDotColor(car.multiplier);
  tr.append(dot, `${car.traffic} on the road`);
  facts.append(tr);
  if (p.sources.route === "estimate") facts.append(el("span", null, "Road route estimated (offline)"));
  box.append(facts);

  const best = p.best_departure;
  if (best.time !== p.time && best.saves >= 5) {
    const tip = el("button", `mt-3 flex w-full items-center gap-2 rounded-lg border bg-white px-2.5 py-2 text-left text-[13px] hover:bg-zinc-50 ${info.edge}`);
    tip.type = "button";
    tip.append(icon("clock"), el("span", null, `Leave at ${best.time} by ${best.mode} instead: ${fmtMin(best.minutes)} (${Math.round(best.saves)} min less)`));
    tip.addEventListener("click", () => { $("time").value = best.time; plan(); });
    box.append(tip);
  }
}

// One-line description under each mode name
function rowSubtitle(opt) {
  if (opt.mode === "car") return `${opt.km} km · ${opt.traffic}`;
  if (opt.mode === "train") {
    const changes = opt.lines.length - 1;
    return `${opt.lines.join(" → ")} · ${changes ? `${changes} change${changes > 1 ? "s" : ""}` : "direct"}`;
  }
  return "Estimate · road + stops";
}

function renderCompare() {
  const p = state.plan;
  const box = $("compare");
  box.innerHTML = "";
  box.append(el("div", "mx-0.5 mt-1 mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500", "Compare options"));
  const list = el("div", "divide-y divide-zinc-200 overflow-hidden rounded-xl border border-zinc-200");
  const fastest = Math.min(...p.options.map((o) => o.minutes));
  ["car", "train", "bus"].forEach((mode) => {
    const opt = option(mode);
    const info = MODE_INFO[mode];
    const selected = mode === state.selected;
    const row = el("button", "grid w-full grid-cols-[36px_1fr_auto_18px] items-center gap-3 px-3.5 py-3 text-left transition-colors " +
      (selected ? info.sel : "bg-white hover:bg-zinc-50") + (opt ? " cursor-pointer" : " cursor-default opacity-45"));
    row.type = "button";
    row.disabled = !opt;

    const ic = el("span", `grid size-9 place-items-center rounded-[10px] ${info.soft}`);
    ic.append(icon(info.icon, "size-[18px]"));
    row.append(ic);

    const mid = el("span", "flex min-w-0 flex-col gap-0.5");
    const name = el("span", `flex items-center gap-2 text-[15px] font-semibold ${selected ? info.accent : ""}`, info.label);
    if (opt && mode === p.recommended) name.append(el("span", "rounded-full bg-green-600 px-2 py-px text-[11px] font-semibold text-white", "Best"));
    mid.append(name);
    mid.append(el("span", "truncate text-[12.5px] text-zinc-500", opt ? rowSubtitle(opt) : "Not available for this trip"));
    row.append(mid);

    const right = el("span", "flex flex-col items-end");
    right.append(el("span", "text-base font-bold", opt ? `${mode === "bus" ? "~" : ""}${fmtMin(opt.minutes)}` : "–"));
    if (opt) {
      const diff = Math.round(opt.minutes - fastest);
      right.append(diff >= 1
        ? el("span", "text-xs text-amber-700", `+${diff} min · ${arrive(p.time, opt.minutes)}`)
        : el("span", "text-xs text-zinc-500", `Arrive ${arrive(p.time, opt.minutes)}`));
    }
    row.append(right);

    const check = el("span", `${info.accent} ${selected ? "" : "invisible"}`);
    check.append(icon("check"));
    row.append(check);
    if (opt) row.addEventListener("click", () => selectMode(mode));
    list.append(row);
  });
  box.append(list);
}

function stepRow(iconName, title, desc, mins, barColor, walk) {
  const li = el("li", "grid grid-cols-[32px_1fr_auto] gap-2.5 py-2");
  const rail = el("div", "flex flex-col items-center");
  const ic = el("div", "grid size-7 place-items-center rounded-full bg-zinc-100 text-zinc-700");
  ic.append(icon(iconName, "size-[15px]"));
  rail.append(ic);
  const bar = el("div", walk ? "walk-bar mt-1 min-h-[18px] flex-1" : "mt-1 min-h-[18px] w-1 flex-1 rounded-sm");
  if (!walk) bar.style.background = barColor || "transparent";
  rail.append(bar);
  const body = el("div");
  if (title instanceof Node) body.append(title); else body.append(el("div", "font-medium", title));
  if (desc) body.append(el("div", "mt-0.5 text-[12.5px] text-zinc-500", desc));
  li.append(rail, body, el("div", "whitespace-nowrap text-[13px] text-zinc-500", mins));
  return li;
}

function note(text) {
  return el("p", "my-1.5 text-[13px] leading-relaxed text-zinc-500", text);
}

function renderDetail() {
  const p = state.plan;
  const opt = option(state.selected);
  const box = $("detail");
  box.innerHTML = "";
  $("detailTitle").textContent = `Route details · ${MODE_INFO[opt.mode].label}`;

  if (opt.mode === "car") {
    box.append(note(`${opt.km} km. ${fmtMin(opt.freeflow_minutes)} on empty roads; our model predicts about ${fmtMin(opt.delay_minutes)} extra at ${p.time} (${opt.traffic.toLowerCase()}${p.weather.rain_mm >= 1 ? ", rain" : ""}).`));
    const lg = el("div", "mt-1.5 flex flex-wrap gap-3 text-xs text-zinc-500");
    [["#1e9e4a", "Light"], ["#f29900", "Slow"], ["#e8453c", "Heavy"], ["#a50e0e", "Very heavy"]].forEach(([c, t]) => {
      const s = el("span", "inline-flex items-center gap-1");
      const i = el("i", "inline-block h-1 w-4 rounded-sm");
      i.style.background = c;
      s.append(i, t);
      lg.append(s);
    });
    box.append(lg);
    return;
  }
  if (opt.mode === "bus") {
    box.append(note(`Rough estimate: road time with bus stops, about ${opt.wait_minutes} min waiting, and 10 min walking. Real bus routes are not included yet.`));
    return;
  }
  const ul = el("ul", "mt-1");
  opt.legs.forEach((leg, i) => {
    if (leg.type === "walk" || leg.type === "feeder") {
      const where = i === 0 ? `${leg.station} station` : (state.to.name || "destination");
      const verb = leg.type === "walk" ? "Walk" : "Grab / drive";
      const dist = leg.km >= 0.05 ? `${leg.km} km` : null;
      ul.append(stepRow(leg.type === "walk" ? "footprints" : "car-taxi-front", `${verb} to ${where}`, dist, fmtMin(leg.minutes), null, leg.type === "walk"));
    } else if (leg.type === "transfer") {
      ul.append(stepRow("arrow-left-right", `Change at ${leg.station}`, "Walk to the other platform", fmtMin(leg.minutes), null, true));
    } else if (leg.type === "ride") {
      const t = el("div", "font-medium");
      const badge = el("span", "mr-1.5 inline-block rounded px-1.5 py-px text-[11px] font-semibold text-white", leg.short);
      badge.style.background = leg.color;
      if (leg.color.toUpperCase() === "#FFCD00") badge.style.color = "#18181b";
      t.append(badge, leg.line_name);
      const desc = `${leg.from} → ${leg.to} · ${leg.stops} stop${leg.stops > 1 ? "s" : ""} · trains every ~${Math.round(leg.wait * 2)} min`;
      ul.append(stepRow("train-front", t, desc, fmtMin(leg.minutes + leg.wait), leg.color, false));
    }
  });
  box.append(ul);
  if (opt.feeder) box.append(note("The nearest station is far, so the trip includes a short Grab or car ride."));
  if (p.weather.rain_mm >= 5 && opt.walk_minutes > 3) box.append(note("Heavy rain: bring an umbrella for the walking parts."));
}

function renderAll() {
  renderHero();
  renderCompare();
  renderDetail();
  drawIcons();
  try { drawMap(); } catch (e) { console.error("map failed", e); }
}

function selectMode(mode) {
  state.selected = mode;
  renderCompare();
  renderDetail();
  drawIcons();
  try { drawMap(false); } catch (e) { console.error("map failed", e); }
}

// ---------- planning ----------
let planSeq = 0;  // only the newest request may update the page

async function plan() {
  if (!state.from || !state.to) {
    setError("Choose a start and a destination.");
    return;
  }
  const seq = ++planSeq;
  $("results").classList.remove("hidden");
  $("results").classList.remove("collapsed");
  setError("");
  setStatus("Predicting your trip…");
  $("go").disabled = true;
  try {
    const body = {
      from: state.from, to: state.to,
      date: $("date").value || null, time: $("time").value || "08:00",
    };
    if (state.rainMoved) body.rain_override_mm = Number($("rain").value);
    const p = await api("/api/plan", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (seq !== planSeq) return;  // a newer request was started meanwhile
    state.plan = p;
    $("time").value = p.time;
    state.selected = p.recommended;
    $("out").classList.remove("hidden");
    document.body.classList.add("has-results");
    renderAll();
  } catch (e) {
    if (seq === planSeq) setError(e.message);
  } finally {
    if (seq === planSeq) {
      setStatus("");
      $("go").disabled = false;
    }
  }
}

function setRain(mm) {
  if (mm == null) {
    state.rainMoved = false;
    $("rain").value = 0;
    $("rainLabel").textContent = "live forecast";
    $("rainReset").classList.add("hidden");
  } else {
    state.rainMoved = true;
    $("rain").value = mm;
    $("rainLabel").textContent = `${mm} mm`;
    $("rainReset").classList.remove("hidden");
  }
}

// One-tap demo scenarios for judges
const DEMOS = {
  rush: () => ({ from: PLACES.puchong, to: PLACES.klcc, date: nextDay([1, 2, 3, 4, 5]), time: "08:00", rain: null }),
  rain: () => ({ from: PLACES.klcc, to: PLACES.puchong, date: nextDay([1, 2, 3, 4, 5]), time: "18:00", rain: 15 }),
  sunday: () => ({ from: PLACES.puchong, to: PLACES.klcc, date: nextDay([0]), time: "11:00", rain: null }),
};

function runDemo(name) {
  const d = DEMOS[name]();
  state.from = d.from;
  state.to = d.to;
  $("fromInput").value = d.from.name;
  $("toInput").value = d.to.name;
  $("date").value = d.date;
  $("time").value = d.time;
  setRain(d.rain);
  plan();
}

function init() {
  drawIcons();
  initMap();
  $("date").value = localDate(1);  // tomorrow
  $("fromInput").value = state.from.name;
  $("toInput").value = state.to.name;
  setupSearch("from");
  setupSearch("to");
  $("go").addEventListener("click", plan);
  $("date").addEventListener("change", plan);
  $("time").addEventListener("change", plan);
  $("backToRoute").addEventListener("click", fitRoute);
  document.querySelectorAll(".demo").forEach((b) => b.addEventListener("click", () => runDemo(b.dataset.demo)));
  $("swap").addEventListener("click", () => {
    [state.from, state.to] = [state.to, state.from];
    $("fromInput").value = state.from ? state.from.name : "";
    $("toInput").value = state.to ? state.to.name : "";
    plan();
  });
  $("rain").addEventListener("input", () => setRain(Number($("rain").value)));
  $("rain").addEventListener("change", plan);
  $("rainReset").addEventListener("click", () => { setRain(null); plan(); });
  $("sheetHandle").addEventListener("click", () => $("results").classList.toggle("collapsed"));
  plan();
}

init();
