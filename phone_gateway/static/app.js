"use strict";
const $ = (selector) => document.querySelector(selector);
const descriptions = {
  camera: ["▣", "Imagens JPEG da câmera frontal ou traseira."],
  accelerometer: ["↗", "Aceleração nos eixos X, Y e Z, sem gravidade."],
  gyroscope: ["◎", "Velocidade angular nos três eixos do celular."],
  orientation: ["◈", "Ângulos de orientação e referência absoluta ou relativa."],
  gps: ["⌖", "Latitude, longitude e precisão da localização."],
  battery: ["▰", "Nível da bateria e estado de carregamento."],
};
const state = {phase: "idle", generation: 0, socket: null, cards: {}, catalog: {},
  pending: new Set(), lastSent: {}, counts: {}, total: 0, startedAt: 0,
  stream: null, cameraTimer: null, cameraBusy: false, watchId: null, battery: null,
  batteryHandler: null, wakeLock: null, noDataTimer: null, config: null};
const STORAGE_KEY = "phone-gateway-settings-v1";

function log(message, kind = "") {
  $(".log-empty")?.remove();
  const row = document.createElement("p");
  const clock = document.createElement("time");
  clock.textContent = new Date().toLocaleTimeString("pt-BR");
  const text = document.createElement("span");
  text.textContent = message;
  text.className = kind;
  row.append(clock, text);
  $("#log").append(row);
  while ($("#log").children.length > 60) $("#log").firstChild.remove();
  $("#log").scrollTop = $("#log").scrollHeight;
}
function notice(message) {
  $("#notice").textContent = message;
  $("#notice").hidden = !message;
}
function sensorStatus(sensor, message, kind = "") {
  const element = state.cards[sensor]?.querySelector(".sensor-state");
  if (element) {element.textContent = message; element.className = `sensor-state ${kind}`;}
}
function readSettings() {
  try {return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");} catch {return {};}
}
function persistSettings() {
  const sensors = {};
  for (const [name, card] of Object.entries(state.cards)) {
    sensors[name] = {enabled: card.querySelector(".enabled").checked,
      topic: card.querySelector(".topic").value, rate_hz: Number(card.querySelector(".rate").value)};
  }
  // Credenciais AMQP e token não são persistidos.
  try {localStorage.setItem(STORAGE_KEY, JSON.stringify({sensors,
    exchange: $("#exchange").value, device_id: $("#device-id").value,
    camera: {facing: $("#camera-facing").value, width: $("#camera-width").value,
      quality: $("#camera-quality").value}}));} catch { /* Storage pode estar desabilitado. */ }
}
function updateSelection() {
  let active = 0;
  for (const card of Object.values(state.cards)) {
    const enabled = card.querySelector(".enabled").checked;
    card.classList.toggle("selected", enabled);
    card.querySelectorAll(".topic,.rate,select").forEach((input) => {
      input.disabled = state.phase !== "idle" || !enabled;
    });
    if (enabled) active++;
  }
  $("#sensor-selection").textContent = `${active} ${active === 1 ? "sensor selecionado" : "sensores selecionados"}`;
}
function phase(value, label) {
  state.phase = value;
  $("#connection-state").textContent = label;
  $("#connection-dot").className = `dot ${value === "streaming" ? "active" : value === "connecting" ? "connecting" : ""}`;
  $("#start").disabled = value !== "idle";
  $("#stop").disabled = value === "idle";
  $("#gateway-form").querySelectorAll("input,select").forEach((input) => {input.disabled = value !== "idle";});
  updateSelection();
}
function configFromForm() {
  const sensors = {};
  for (const [name, card] of Object.entries(state.cards)) {
    sensors[name] = {enabled: card.querySelector(".enabled").checked,
      topic: card.querySelector(".topic").value.trim(), rate_hz: Number(card.querySelector(".rate").value)};
  }
  return {broker_uri: $("#broker").value.trim(), exchange: $("#exchange").value.trim(),
    device_id: $("#device-id").value.trim(), sensors};
}
function current(generation) {return generation === state.generation && state.phase !== "idle";}
function active(sensor) {return state.phase === "streaming" && state.config?.sensors[sensor]?.enabled;}
function canSend(sensor) {
  if (!active(sensor) || state.socket?.readyState !== WebSocket.OPEN || state.pending.has(sensor)) return false;
  if (document.hidden || state.socket.bufferedAmount > 256 * 1024) return false;
  return performance.now() - (state.lastSent[sensor] ?? -Infinity) >= 1000 / state.config.sensors[sensor].rate_hz;
}
function markSent(sensor) {state.pending.add(sensor); state.lastSent[sensor] = performance.now();}
function sendSample(sensor, data, timestamp = Date.now()) {
  if (!canSend(sensor)) return;
  state.socket.send(JSON.stringify({sensor, timestamp_ms: Math.round(timestamp), data}));
  markSent(sensor);
}
function reading(sensor, text) {
  state.cards[sensor].querySelector(".sensor-value").textContent = text;
  sensorStatus(sensor, "Recebendo leituras", "live");
}
const valid = (values) => values.every((value) => typeof value === "number" && Number.isFinite(value));
function onMotion(event) {
  if (active("accelerometer")) {
    const v = event.acceleration;
    if (v && valid([v.x, v.y, v.z])) {
      const data = {x: v.x, y: v.y, z: v.z};
      sendSample("accelerometer", data);
      reading("accelerometer", `x ${v.x.toFixed(2)} · y ${v.y.toFixed(2)} · z ${v.z.toFixed(2)} m/s²`);
    }
  }
  if (active("gyroscope")) {
    const v = event.rotationRate;
    if (v && valid([v.alpha, v.beta, v.gamma])) {
      sendSample("gyroscope", {alpha: v.alpha, beta: v.beta, gamma: v.gamma});
      const rad = Math.PI / 180;
      reading("gyroscope", `x ${(v.beta * rad).toFixed(2)} · y ${(v.gamma * rad).toFixed(2)} · z ${(v.alpha * rad).toFixed(2)} rad/s`);
    }
  }
}
function onOrientation(event) {
  if (!active("orientation") || !valid([event.alpha, event.beta, event.gamma])) return;
  sendSample("orientation", {alpha: event.alpha, beta: event.beta, gamma: event.gamma, absolute: !!event.absolute});
  const rad = Math.PI / 180;
  reading("orientation", `α ${(event.alpha * rad).toFixed(2)} · β ${(event.beta * rad).toFixed(2)} · γ ${(event.gamma * rad).toFixed(2)} rad`);
}
function permissionPromise(name, enabled) {
  if (!enabled) return Promise.resolve("unused");
  if (!window[name]) return Promise.resolve("unsupported");
  try {
    return typeof window[name].requestPermission === "function"
      ? window[name].requestPermission().catch(() => "denied") : Promise.resolve("granted");
  } catch {return Promise.resolve("denied");}
}
function connect(config, generation) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    state.socket = socket;
    let ready = false;
    const timeout = setTimeout(() => {reject(new Error("A conexão demorou demais. Confira o gateway e o broker.")); socket.close();}, 20000);
    socket.onopen = () => {
      if (!current(generation)) {socket.close(); return;}
      socket.send(JSON.stringify({type: "configure", token: $("#token").value, config}));
    };
    socket.onmessage = (event) => {
      if (!current(generation)) return;
      const message = JSON.parse(event.data);
      if (message.type === "ready") {ready = true; clearTimeout(timeout); resolve();}
      else if (message.type === "published") {
        state.pending.delete(message.sensor);
        state.counts[message.sensor] = message.count;
        state.total++;
        $("#total-count").textContent = state.total.toLocaleString("pt-BR");
        state.cards[message.sensor].querySelector(".sensor-count").textContent = `${message.count} msgs`;
        sensorStatus(message.sensor, "Publicando", "live");
        if (message.count === 1) log(`${state.catalog[message.sensor].label} → ${message.topic}`, "success");
      } else if (message.type === "skipped") state.pending.delete(message.sensor);
      else if (message.type === "error") {
        state.pending.delete(message.sensor);
        log(message.message, "error");
        if (!ready) {clearTimeout(timeout); reject(new Error(message.message));}
        else if (message.sensor) sensorStatus(message.sensor, "Erro de publicação", "error");
        notice(message.message);
      }
    };
    socket.onerror = () => {if (!ready) reject(new Error("Não foi possível abrir a conexão com o gateway."));};
    socket.onclose = () => {
      clearTimeout(timeout);
      if (!ready) reject(new Error("Conexão encerrada antes de iniciar a publicação."));
      if (current(generation)) {
        const reason = "Conexão encerrada. Verifique o broker e inicie novamente.";
        stop(reason);
        notice(reason);
      }
    };
  });
}
function unavailable(sensor, reason) {
  sensorStatus(sensor, reason, "error");
  log(`${state.catalog[sensor].label}: ${reason}.`, "error");
}
async function setupCamera(generation) {
  if (!active("camera")) return;
  if (!navigator.mediaDevices?.getUserMedia) {unavailable("camera", "Câmera indisponível neste navegador"); return;}
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      facingMode: {ideal: $("#camera-facing").value}, width: {ideal: Number($("#camera-width").value)},
      frameRate: {ideal: 30, max: 30}}});
    if (!current(generation)) {stream.getTracks().forEach((track) => track.stop()); return;}
    state.stream = stream;
    const card = state.cards.camera;
    const video = card.querySelector("video");
    video.srcObject = stream;
    card.querySelector(".preview").hidden = false;
    await video.play();
    if (!current(generation)) return;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const quality = Number($("#camera-quality").value);
    const width = Number($("#camera-width").value);
    let cameraStopped = false;
    async function capture() {
      if (!current(generation) || state.cameraBusy || !canSend("camera") || video.readyState < 2) return;
      state.cameraBusy = true;
      try {
        const scale = Math.min(1, width / video.videoWidth, 720 / video.videoHeight);
        canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
        canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const timestamp = Date.now();
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
        if (!blob || !current(generation) || !canSend("camera")) return;
        const jpeg = await blob.arrayBuffer();
        if (!current(generation) || !canSend("camera")) return;
        const payload = new Uint8Array(8 + jpeg.byteLength);
        new DataView(payload.buffer).setBigUint64(0, BigInt(timestamp), false);
        payload.set(new Uint8Array(jpeg), 8);
        state.socket.send(payload);
        markSent("camera");
        reading("camera", `${canvas.width} × ${canvas.height} · ${(blob.size / 1024).toFixed(1)} KB / quadro`);
      } catch {
        if (current(generation)) {
          cameraStopped = true;
          unavailable("camera", "Falha ao capturar imagem");
          clearTimeout(state.cameraTimer);
        }
      } finally {if (current(generation)) state.cameraBusy = false;}
    }
    async function captureLoop() {
      if (!current(generation) || cameraStopped) return;
      await capture();
      if (!current(generation) || cameraStopped) return;
      const interval = 1000 / state.config.sensors.camera.rate_hz;
      const elapsed = performance.now() - (state.lastSent.camera ?? -Infinity);
      state.cameraTimer = setTimeout(captureLoop, Math.ceil(Math.max(4, interval - elapsed)));
    }
    stream.getVideoTracks()[0].addEventListener("ended", () => {
      if (current(generation)) {
        cameraStopped = true;
        clearTimeout(state.cameraTimer);
        unavailable("camera", "Câmera interrompida");
      }
    });
    state.cameraTimer = setTimeout(captureLoop, 0);
    sensorStatus("camera", "Câmera pronta", "live");
  } catch {
    if (current(generation)) {
      state.stream?.getTracks().forEach((track) => track.stop());
      state.stream = null;
      unavailable("camera", "Sem acesso à câmera");
    }
  }
}
function setupGPS(generation) {
  if (!active("gps")) return;
  if (!navigator.geolocation) {unavailable("gps", "Localização indisponível"); return;}
  state.watchId = navigator.geolocation.watchPosition((position) => {
    if (!current(generation)) return;
    const c = position.coords;
    const data = {latitude: c.latitude, longitude: c.longitude, accuracy: c.accuracy,
      altitude: c.altitude, altitudeAccuracy: c.altitudeAccuracy, heading: c.heading, speed: c.speed};
    sendSample("gps", data, position.timestamp);
    reading("gps", `${c.latitude.toFixed(6)}, ${c.longitude.toFixed(6)} · ±${c.accuracy.toFixed(0)} m`);
  }, (error) => {
    if (!current(generation)) return;
    unavailable("gps", {1: "Permissão negada", 2: "Localização indisponível", 3: "Localização demorou demais"}[error.code] || "Erro na localização");
  }, {enableHighAccuracy: true, maximumAge: 0, timeout: 15000});
}
async function setupBattery(generation) {
  if (!active("battery")) return;
  if (!navigator.getBattery) {unavailable("battery", "Bateria indisponível neste navegador"); return;}
  try {
    const battery = await navigator.getBattery();
    if (!current(generation)) return;
    state.battery = battery;
    const update = () => {
      if (!current(generation)) return;
      sendSample("battery", {level: battery.level, charging: battery.charging,
        dischargingTime: Number.isFinite(battery.dischargingTime) ? battery.dischargingTime : null});
      reading("battery", `${Math.round(battery.level * 100)}% · ${battery.charging ? "carregando" : "descarregando"}`);
    };
    state.batteryHandler = update;
    ["levelchange", "chargingchange", "dischargingtimechange"].forEach((event) => battery.addEventListener(event, update));
    update();
    state.batteryTimer = setInterval(update, 1000 / state.config.sensors.battery.rate_hz);
  } catch {if (current(generation)) unavailable("battery", "Sem acesso à bateria");}
}
function stop(message = "Sessão encerrada") {
  state.generation++;
  state.socket?.close(); state.socket = null;
  clearTimeout(state.cameraTimer); clearInterval(state.batteryTimer); clearTimeout(state.noDataTimer);
  state.stream?.getTracks().forEach((track) => track.stop()); state.stream = null;
  state.cameraBusy = false;
  const video = state.cards.camera?.querySelector("video");
  if (video) {video.srcObject = null; state.cards.camera.querySelector(".preview").hidden = true;}
  window.removeEventListener("devicemotion", onMotion);
  window.removeEventListener("deviceorientation", onOrientation);
  if (state.watchId !== null) navigator.geolocation.clearWatch(state.watchId);
  state.watchId = null;
  if (state.battery) ["levelchange", "chargingchange", "dischargingtimechange"].forEach((event) => {
    state.battery.removeEventListener(event, state.batteryHandler);
  });
  state.battery = null; state.batteryHandler = null;
  state.wakeLock?.release().catch(() => {}); state.wakeLock = null;
  state.pending.clear();
  for (const name of Object.keys(state.cards)) {
    if (state.config?.sensors[name]?.enabled) sensorStatus(name, "Parado");
  }
  phase("idle", message);
  log(message);
}
async function start(event) {
  event.preventDefault();
  if (state.phase !== "idle") return;
  if (!window.isSecureContext) {
    notice("Abra o gateway por HTTPS com um certificado confiável pelo celular para acessar os sensores."); return;
  }
  const config = configFromForm();
  const selected = Object.values(config.sensors).filter((sensor) => sensor.enabled);
  if (!selected.length) {notice("Selecione pelo menos um sensor."); return;}
  if (new Set(selected.map((sensor) => sensor.topic)).size !== selected.length) {
    notice("Defina um tópico diferente para cada sensor selecionado."); return;
  }
  persistSettings();
  notice("");
  state.config = config; state.generation++;
  const generation = state.generation;
  state.total = 0; state.counts = {}; state.pending.clear(); state.lastSent = {};
  $("#total-count").textContent = "0"; $("#elapsed").textContent = "00:00";
  for (const [name, card] of Object.entries(state.cards)) {
    card.querySelector(".sensor-count").textContent = "0 msgs";
    card.querySelector(".sensor-value").textContent = "—";
    sensorStatus(name, config.sensors[name].enabled ? "Solicitando acesso…" : "Desativado");
  }
  phase("connecting", "Conectando ao broker…");
  // Ambos os pedidos são iniciados no clique, antes de qualquer await (Safari/iOS).
  const motionPermission = permissionPromise("DeviceMotionEvent", config.sensors.accelerometer.enabled || config.sensors.gyroscope.enabled);
  const orientationPermission = permissionPromise("DeviceOrientationEvent", config.sensors.orientation.enabled);
  try {
    const [motion, orientation] = await Promise.all([motionPermission, orientationPermission]);
    if (!current(generation)) return;
    await connect(config, generation);
    if (!current(generation)) return;
    state.startedAt = Date.now();
    phase("streaming", "Conectado · publicando");
    log("Conexão com o broker estabelecida.", "success");
    if (motion === "granted") window.addEventListener("devicemotion", onMotion);
    else for (const name of ["accelerometer", "gyroscope"]) {
      if (active(name)) unavailable(name, motion === "denied" ? "Permissão negada" : "Sensor indisponível");
    }
    if (orientation === "granted") window.addEventListener("deviceorientation", onOrientation);
    else if (active("orientation")) unavailable("orientation", orientation === "denied" ? "Permissão negada" : "Sensor indisponível");
    setupGPS(generation);
    void setupCamera(generation);
    void setupBattery(generation);
    if (navigator.wakeLock) navigator.wakeLock.request("screen").then((lock) => {
      if (!current(generation)) {lock.release().catch(() => {}); return;}
      state.wakeLock = lock;
    }).catch(() => {});
    state.noDataTimer = setTimeout(() => {
      for (const name of ["accelerometer", "gyroscope", "orientation"]) {
        if (active(name) && !state.counts[name] && !state.cards[name].querySelector(".sensor-state").classList.contains("error")) {
          unavailable(name, "Sem leituras; confira suporte e permissões");
        }
      }
    }, 8000);
  } catch (error) {
    if (!current(generation)) return;
    stop("Não foi possível iniciar");
    notice(error.message);
    log(error.message, "error");
  }
}
function cameraOptions(card, saved) {
  const options = document.createElement("div"); options.className = "sensor-options";
  options.innerHTML = `<label>Câmera<select id="camera-facing"><option value="environment">Traseira</option><option value="user">Frontal</option></select></label>
    <label>Largura máx.<select id="camera-width"><option value="640">640 px</option><option value="1280">1280 px</option></select></label>
    <label>Qualidade<select id="camera-quality"><option value="0.6">60%</option><option value="0.8">80%</option><option value="0.95">95%</option></select></label>`;
  card.querySelector(".sensor-options").after(options);
  $("#camera-facing").value = saved?.facing || "environment";
  $("#camera-width").value = saved?.width || "640";
  $("#camera-quality").value = saved?.quality || "0.8";
}
async function init() {
  try {
    const response = await fetch("/api/info");
    if (!response.ok) throw new Error("Falha ao carregar configuração.");
    const info = await response.json(); state.catalog = info.sensors;
    const saved = readSettings();
    $("#exchange").value = saved.exchange || "is";
    $("#device-id").value = saved.device_id || "phone";
    $("#broker").required = !info.broker_configured;
    if (info.broker_configured) {
      $("#broker").placeholder = "Usar broker configurado no servidor";
      $("#broker-help").textContent = "Deixe vazio para usar a configuração do servidor, ou informe outro broker.";
    }
    $("#token-field").hidden = !info.token_required;
    $("#token").required = info.token_required;
    for (const [name, sensor] of Object.entries(info.sensors)) {
      const card = $("#sensor-template").content.firstElementChild.cloneNode(true);
      state.cards[name] = card;
      card.querySelector("h3").textContent = sensor.label;
      card.querySelector(".unit").textContent = sensor.unit;
      card.querySelector(".sensor-icon").textContent = descriptions[name][0];
      card.querySelector(".sensor-description").textContent = descriptions[name][1];
      const stored = saved.sensors?.[name];
      card.querySelector(".enabled").checked = stored?.enabled ?? name === "camera";
      card.querySelector(".enabled").setAttribute("aria-label", `Ativar ${sensor.label}`);
      card.querySelector(".topic").value = stored?.topic || sensor.topic;
      card.querySelector(".rate").value = stored?.rate_hz || sensor.rate;
      card.querySelector(".rate").max = sensor.max_rate;
      if (name === "camera") card.querySelector(".rate-label").textContent = "Taxa de frames (FPS)";
      $("#sensors").append(card);
      if (name === "camera") cameraOptions(card, saved.camera);
    }
    updateSelection();
    $("#start").disabled = false;
    $("#gateway-form").addEventListener("change", () => {updateSelection(); persistSettings();});
    $("#gateway-form").addEventListener("submit", start);
    $("#stop").addEventListener("click", () => stop());
    if (!window.isSecureContext) notice("Este endereço usa HTTP. Para acessar os sensores pelo celular, abra a versão HTTPS do gateway.");
  } catch {notice("Não foi possível carregar o gateway. Recarregue a página e confira o servidor.");}
}
$("#clear-log").addEventListener("click", () => {$("#log").replaceChildren();});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && state.phase !== "idle") {
    stop("Sessão parada ao sair da página");
    notice("A coleta foi parada porque a página ficou em segundo plano. Inicie novamente para continuar.");
  }
});
window.addEventListener("pagehide", () => {if (state.phase !== "idle") stop();});
setInterval(() => {
  if (state.phase !== "streaming") return;
  const seconds = Math.floor((Date.now() - state.startedAt) / 1000);
  $("#elapsed").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}, 1000);
void init();
