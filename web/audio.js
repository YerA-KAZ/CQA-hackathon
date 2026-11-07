export function addRecording(container, conversation, make) {
  const panel = make("div", null, container);
  panel.className = "recording";
  make("h3", "Запись разговора", panel);
  const message = make("p", "Загружаем звуковую волну…", panel);
  const canvas = make("canvas", null, panel);
  canvas.setAttribute("aria-label", "Звуковая волна записи");
  const seek = make("input", null, panel);
  seek.type = "range";
  seek.min = 0;
  seek.step = 0.1;
  seek.value = 0;
  seek.disabled = true;
  seek.setAttribute("aria-label", "Позиция в записи");
  const player = make("audio", null, panel);
  player.controls = true;
  player.preload = "metadata";

  player.addEventListener("error", () => { message.textContent = "Не удалось открыть запись звонка."; });

  let peaks = [];
  function draw() {
    const width = canvas.width = canvas.clientWidth || 640;
    const height = canvas.height = 86;
    const ctx = canvas.getContext("2d");
    const progress = player.duration ? player.currentTime / player.duration : 0;
    const step = width / peaks.length;
    for (const [index, peak] of peaks.entries()) {
      const half = Math.max(1, peak * height * 0.45);
      ctx.fillStyle = index / peaks.length < progress ? "#087b55" : "#87b8a7";
      ctx.fillRect(index * step, height / 2 - half, Math.max(1, step - 1), 2 * half);
    }
  }
  player.addEventListener("loadedmetadata", () => {
    seek.disabled = !Number.isFinite(player.duration);
    if (!seek.disabled) seek.max = player.duration;
  });
  player.addEventListener("timeupdate", () => { seek.value = player.currentTime; draw(); });
  seek.addEventListener("input", () => { player.currentTime = Number(seek.value); });
  canvas.addEventListener("click", event => {
    const bounds = canvas.getBoundingClientRect();
    if (!seek.disabled) player.currentTime = (event.clientX - bounds.left) / bounds.width * player.duration;
  });

  async function loadWaveform() {
    let response = await fetch(conversation.audio_url);
    if (response.ok && response.headers.get("content-type")?.includes("text/html")) {
      const page = document.createElement("template");
      page.innerHTML = await response.text();
      const media = page.content.querySelector("audio[src], audio source[src], source[type^='audio/']");
      const link = [...page.content.querySelectorAll("a[href]")].find(anchor =>
        /\.(mp3|wav|ogg|opus|m4a|aac|flac)(?:[?#]|$)/i.test(anchor.getAttribute("href"))
      );
      const target = media?.getAttribute("src") || link?.getAttribute("href");
      const url = target && new URL(target, response.url);
      if (!url || url.protocol !== "https:" || url.username || url.password || url.href === response.url) {
        throw new Error("Источник вернул HTML-страницу без прямой ссылки на аудио.");
      }
      player.src = url.href;
      response = await fetch(url);
      if (response.headers.get("content-type")?.includes("text/html")) {
        player.removeAttribute("src");
        throw new Error("Ссылка на запись снова вернула HTML-страницу.");
      }
    } else {
      player.src = conversation.audio_url;
    }
    if (!response.ok) throw new Error("Audio unavailable");
    if (!panel.isConnected) return;
    const context = new AudioContext();
    try {
      const audio = await context.decodeAudioData(await response.arrayBuffer());
      const channels = Array.from({length: audio.numberOfChannels}, (_, i) => audio.getChannelData(i));
      const columns = 260;
      peaks = Array.from({length: columns}, (_, index) => {
        const start = Math.floor(index * audio.length / columns);
        const end = Math.floor((index + 1) * audio.length / columns);
        let peak = 0;
        for (const channel of channels) {
          for (let sample = start; sample < end; sample++) peak = Math.max(peak, Math.abs(channel[sample]));
        }
        return peak;
      });
      message.textContent = "Звуковая волна · нажмите, чтобы перейти к фрагменту";
      draw();
    } finally {
      await context.close();
    }
  }
  loadWaveform().catch(error => {
    canvas.hidden = true;
    seek.hidden = true;
    if (!player.getAttribute("src")) {
      if (error.message.includes("HTML")) {
        player.hidden = true;
        message.textContent = error.message;
        return;
      }
      player.src = conversation.audio_url;
    }
    if (!player.error) message.textContent = "Звуковая волна недоступна. Для перехода по записи используйте плеер.";
  });
  return player;
}
