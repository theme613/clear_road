// Clear Road front end: search, prediction-first panel, time machine, map lines. Plain JS, no build.
const $ = (id) => document.getElementById(id);
const KL_CENTER = [3.1, 101.67];
const MODE_INFO = {
  car: { icon: "🚗", label: "Car", verdict: "Drive", color: "#1a73e8" },
  train: { icon: "🚆", label: "Train", verdict: "Take the train", color: "#1e8e3e" },
  bus: { icon: "🚌", label: "Bus", verdict: "Take the bus", color: "#8e24aa" },
};
const GREY = "#9aa0a6";
const BUS_COLOR = "#8e24aa";
const PLAY_MS = 650;

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
  playing: false,
};
let map = null;
let routeLayer = null;
let markerLayer = null;
let trafficLayer = null;      // coloured car line used by the time machine
let trafficSegs = [];         // [{poly, w}]
let routeBounds = null;
let fitZoom = null;
let playTimer = null;

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

function trafficColor(localMult) {
  const table = (state.plan && state.plan.traffic_colors) || [[1.25, "#1e9e4a"], [1.6, "#f29900"], [2.0, "#e8453c"], [99, "#a50e0e"]];
  return table.find(([limit]) => localMult < limit)[1];
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
  $("error").textContent = text ? `⚠ ${text}` : "";
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

// ---------- time machine: recolour the car route for any predicted slot ----------
function showTrafficLayer() {
  if (!map) return;
  hideTrafficLayer();
  const car = option("car");
  trafficLayer = L.layerGroup().addTo(map);
  line(car.coords, { color: "#174ea6", weight: 10, opacity: 0.85 }).addTo(trafficLayer);
  trafficSegs = car.segments.map((s) => ({ w: s.w, poly: line(s.coords, { color: s.color, weight: 6 }).addTo(trafficLayer) }));
}

function hideTrafficLayer() {
  if (trafficLayer && map) map.removeLayer(trafficLayer);
  trafficLayer = null;
  trafficSegs = [];
}

function recolorCar(mult) {
  trafficSegs.forEach((s) => s.poly.setStyle({ color: trafficColor(1 + (mult - 1) * s.w) }));
}

function showSlot(i) {
  const s = state.plan.slots[i];
  document.querySelectorAll(".col").forEach((c, j) => c.classList.toggle("playing", j === i));
  recolorCar(s.mult);
  const badge = $("mapBadge");
  badge.innerHTML = "";
  badge.append(el("span", "bt", s.time));
  const carSpan = el("span", "bc", `🚗 ${fmtMin(s.car)}`);
  badge.append(carSpan);
  if (s.train != null) badge.append(el("span", "bc", `🚆 ${fmtMin(s.train)}`));
  if (s.rain >= 0.5) badge.append(el("span", "bc", `🌧 ${s.rain} mm`));
  const win = s.train != null && s.train < s.car ? "🚆 train wins" : "🚗 car wins";
  badge.append(el("span", "bw", win));
  badge.classList.remove("hidden");
}

function stopPlay() {
  clearInterval(playTimer);
  playTimer = null;
  state.playing = false;
  $("play").textContent = "▶ Play";
  $("play").classList.remove("on");
  $("mapBadge").classList.add("hidden");
  document.querySelectorAll(".col").forEach((c) => c.classList.remove("playing"));
  hideTrafficLayer();
}

function togglePlay() {
  if (!state.plan) return;
  if (state.playing) { stopPlay(); return; }
  state.playing = true;
  $("play").textContent = "⏸ Pause";
  $("play").classList.add("on");
  showTrafficLayer();
  let i = 0;
  showSlot(i);
  playTimer = setInterval(() => {
    i += 1;
    if (i >= state.plan.slots.length) { stopPlay(); return; }
    showSlot(i);
  }, PLAY_MS);
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
      const btn = el("button");
      btn.type = "button";
      const ic = el("span", "ico", it.special ? "◎" : (/station/i.test(it.name) ? "🚉" : "📍"));
      const txt = el("span");
      txt.appendChild(el("div", "nm", it.special ? "Your location" : it.name));
      if (!it.special && it.address) txt.appendChild(el("div", "ad", it.address));
      btn.append(ic, txt);
      btn.addEventListener("mousedown", (e) => { e.preventDefault(); pickIndex(i); });
      list.appendChild(btn);
    });
    list.classList.remove("hidden");
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
    list.append(el("div", "status", "Searching…"));
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
      buttons.forEach((b, i) => b.classList.toggle("active", i === active));
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
  const box = $("hero");
  box.innerHTML = "";
  box.className = `hero hero-${rec.mode}`;

  const top = el("div", "hero-top");
  top.append(el("span", "hero-tag", "✨ Predicted for " + new Date(`${p.date}T00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + ` · ${p.time}`));
  box.append(top);

  box.append(el("div", "hero-verdict", `${MODE_INFO[rec.mode].icon} ${MODE_INFO[rec.mode].verdict}`));
  const when = el("div", "hero-when");
  when.append("Leave ", el("b", null, p.time), " → arrive ", el("b", null, arrive(p.time, rec.minutes)));
  when.append(el("span", "hero-mins", ` · ${fmtMin(rec.minutes)}`));
  box.append(when);

  let reason = "";
  if (rec.mode !== "car") {
    reason = `Driving arrives ${arrive(p.time, car.minutes)}: ${Math.round(p.saved_minutes)} min later` +
      (car.delay_minutes >= 3 ? ` (${car.traffic.toLowerCase()} +${Math.round(car.delay_minutes)} min)` : "");
    box.append(el("div", "hero-save", `Saves ${Math.round(p.saved_minutes)} min vs driving`));
  } else if (train) {
    reason = `Train arrives ${arrive(p.time, train.minutes)}: ${Math.round(train.minutes - car.minutes)} min later. ${car.traffic} on the road.`;
  } else {
    reason = "No train line near this trip.";
  }
  box.append(el("div", "hero-reason", reason));

  const w = p.weather;
  const facts = el("div", "hero-facts");
  const temp = w.temp_c != null ? `${Math.round(w.temp_c)}° · ` : "";
  const src = { "open-meteo": "forecast", override: "what-if", fallback: "no forecast" }[w.source] || w.source;
  facts.append(el("span", null, `${w.rain_mm >= 0.5 ? "🌧" : "⛅"} ${temp}${w.rain_mm} mm rain (${src})`));
  const dot = el("span", "dot");
  dot.style.background = trafficColor(car.multiplier);
  const tr = el("span");
  tr.append(dot, `${car.traffic} on the road`);
  facts.append(tr);
  if (p.sources.route === "estimate") facts.append(el("span", null, "road route estimated (offline)"));
  box.append(facts);

  const best = p.best_departure;
  if (best.time !== p.time && best.saves >= 5) {
    const tip = el("button", "hero-tip");
    tip.type = "button";
    tip.append("💡 Leave at ", el("b", null, best.time), ` by ${best.mode}: ${fmtMin(best.minutes)} (${Math.round(best.saves)} min less)`);
    tip.addEventListener("click", () => { $("time").value = best.time; plan(); });
    box.append(tip);
  }
}

function renderCompare() {
  const p = state.plan;
  const box = $("compare");
  box.innerHTML = "";
  const max = Math.max(...p.options.map((o) => o.minutes));
  ["car", "train", "bus"].forEach((mode) => {
    const opt = option(mode);
    const row = el("button", "row");
    row.type = "button";
    if (!opt) { row.classList.add("na"); row.disabled = true; }
    if (mode === state.selected) row.classList.add("sel");
    if (mode === "bus") row.classList.add("muted");
    row.append(el("span", "row-ic", MODE_INFO[mode].icon));
    const mid = el("span", "row-mid");
    const name = el("span", "row-name", MODE_INFO[mode].label);
    if (opt && mode === p.recommended) name.append(el("span", "pick", "AI PICK"));
    if (mode === "bus") name.append(el("span", "est", "estimate"));
    mid.append(name);
    const track = el("span", "row-track");
    const bar = el("span", "row-bar");
    bar.style.width = opt ? `${Math.max(8, (opt.minutes / max) * 100)}%` : "0";
    bar.style.background = MODE_INFO[mode].color;
    track.append(bar);
    mid.append(track);
    row.append(mid);
    const right = el("span", "row-right");
    right.append(el("span", "row-min", opt ? `${mode === "bus" ? "~" : ""}${fmtMin(opt.minutes)}` : "n/a"));
    if (opt) right.append(el("span", "row-arr", `arrive ${arrive(p.time, opt.minutes)}`));
    row.append(right);
    if (opt) row.addEventListener("click", () => selectMode(mode));
    box.append(row);
  });
}

function renderTimeMachine() {
  const p = state.plan;
  const strip = $("strip");
  strip.innerHTML = "";
  const all = p.slots.flatMap((s) => (s.train != null ? [s.car, s.train] : [s.car]));
  const max = Math.max(...all);
  const lo = Math.min(...all) * 0.6;  // start bars above zero so the rush-hour hump stands out
  const pct = (v) => `${Math.max(6, ((v - lo) / (max - lo)) * 100)}%`;
  p.slots.forEach((s) => {
    const col = el("button", "col");
    col.type = "button";
    col.title = `${s.time}: car ${fmtMin(s.car)}${s.train != null ? `, train ${fmtMin(s.train)}` : ""}`;
    if (s.time === p.time) col.classList.add("chosen");
    const bars = el("span", "bars");
    const c = el("span", "b-car");
    c.style.height = pct(s.car);
    bars.append(c);
    if (s.train != null) {
      const t = el("span", "b-train");
      t.style.height = pct(s.train);
      bars.append(t);
    }
    col.append(bars);
    const trainWins = s.train != null && s.train < s.car;
    col.append(el("span", "win", trainWins ? "🚆" : "🚗"));
    col.append(el("span", "tl", s.time.endsWith(":00") || s.time.endsWith(":30") || s.time === p.time ? s.time : ""));
    if (s.time === p.best_departure.time) col.append(el("span", "star", "★"));
    col.addEventListener("click", () => { stopPlay(); $("time").value = s.time; plan(); });
    strip.append(col);
  });
}

function stepRow(icon, title, desc, mins, barColor, walk) {
  const li = el("li", "step");
  const rail = el("div", "rail");
  rail.append(el("div", "ic", icon));
  const bar = el("div", walk ? "bar walk" : "bar");
  if (!walk) bar.style.background = barColor || "transparent";
  rail.append(bar);
  const body = el("div");
  if (title instanceof Node) body.append(title); else body.append(el("div", "tt", title));
  if (desc) body.append(el("div", "dd", desc));
  li.append(rail, body, el("div", "mm", mins));
  return li;
}

function renderDetail() {
  const p = state.plan;
  const opt = option(state.selected);
  const box = $("detail");
  box.innerHTML = "";
  $("detailTitle").textContent = `Route details · ${MODE_INFO[opt.mode].icon} ${MODE_INFO[opt.mode].label}`;

  if (opt.mode === "car") {
    box.append(el("p", "note", `${opt.km} km. ${fmtMin(opt.freeflow_minutes)} on empty roads; our model predicts about ${fmtMin(opt.delay_minutes)} extra at ${p.time} (${opt.traffic.toLowerCase()}${p.weather.rain_mm >= 1 ? ", rain" : ""}).`));
    const lg = el("div", "legend");
    [["#1e9e4a", "Light"], ["#f29900", "Slow"], ["#e8453c", "Heavy"], ["#a50e0e", "Very heavy"]].forEach(([c, t]) => {
      const s = el("span"); const i = el("i"); i.style.background = c; s.append(i, t); lg.append(s);
    });
    box.append(lg);
    return;
  }
  if (opt.mode === "bus") {
    box.append(el("p", "note", `Rough estimate: road time with bus stops, about ${opt.wait_minutes} min waiting, and 10 min walking. Real bus routes are not included yet.`));
    return;
  }
  const ul = el("ul", "steps");
  opt.legs.forEach((leg, i) => {
    if (leg.type === "walk" || leg.type === "feeder") {
      const where = i === 0 ? `${leg.station} station` : (state.to.name || "destination");
      const verb = leg.type === "walk" ? "Walk" : "Grab / drive";
      const dist = leg.km >= 0.05 ? `${leg.km} km` : null;
      ul.append(stepRow(leg.type === "walk" ? "🚶" : "🚕", `${verb} to ${where}`, dist, fmtMin(leg.minutes), null, leg.type === "walk"));
    } else if (leg.type === "transfer") {
      ul.append(stepRow("🔁", `Change at ${leg.station}`, "Walk to the other platform", fmtMin(leg.minutes), null, true));
    } else if (leg.type === "ride") {
      const t = el("div", "tt");
      const badge = el("span", "badge", leg.short);
      badge.style.background = leg.color;
      if (leg.color.toUpperCase() === "#FFCD00") badge.style.color = "#202124";
      t.append(badge, leg.line_name);
      const desc = `${leg.from} → ${leg.to} · ${leg.stops} stop${leg.stops > 1 ? "s" : ""} · trains every ~${Math.round(leg.wait * 2)} min`;
      ul.append(stepRow("🚆", t, desc, fmtMin(leg.minutes + leg.wait), leg.color, false));
    }
  });
  box.append(ul);
  if (opt.feeder) box.append(el("p", "note", "The nearest station is far, so the trip includes a short Grab or car ride."));
  if (p.weather.rain_mm >= 5 && opt.walk_minutes > 3) box.append(el("p", "note", "☔ Heavy rain: bring an umbrella for the walking parts."));
}

function renderAll() {
  renderHero();
  renderCompare();
  renderTimeMachine();
  renderDetail();
  try { drawMap(); } catch (e) { console.error("map failed", e); }
}

function selectMode(mode) {
  state.selected = mode;
  renderCompare();
  renderDetail();
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
  stopPlay();
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
  initMap();
  $("date").value = localDate(1);  // tomorrow
  $("fromInput").value = state.from.name;
  $("toInput").value = state.to.name;
  setupSearch("from");
  setupSearch("to");
  $("go").addEventListener("click", plan);
  $("date").addEventListener("change", plan);
  $("time").addEventListener("change", plan);
  $("play").addEventListener("click", togglePlay);
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
