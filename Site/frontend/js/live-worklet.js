// VoiceBridge — AudioWorkletProcessor : capture PCM 16 kHz int16 mono.
//
// Chargé via audioContext.audioWorklet.addModule('/js/live-worklet.js').
// Reçoit du float32 mono à AudioContext.sampleRate (typiquement 48 kHz),
// downsample en 16 kHz via décimation linéaire (assez bon pour la voix),
// accumule en chunks de ~100 ms et poste vers le main thread.
//
// CSP : ce fichier est servi par le même domaine, donc script-src 'self' OK.

const TARGET_RATE = 16000;
const TARGET_CHUNK_SAMPLES = 1600; // 100 ms à 16 kHz

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.inputRate = sampleRate; // global injecté par AudioWorkletGlobalScope
    this.ratio = this.inputRate / TARGET_RATE;
    this.acc = []; // accumulateur float32 (target rate)
    this.lastSample = 0; // pour interpolation linéaire
    this.fracIndex = 0; // position fractionnaire dans le buffer source
  }

  // Resample float32 input (inputRate) → float32 (16 kHz) par interpolation linéaire.
  // ``input`` est un Float32Array (un canal mono).
  resampleAndCollect(input) {
    const ratio = this.ratio;
    let i = this.fracIndex;
    const out = [];
    while (i < input.length) {
      const idx = Math.floor(i);
      const frac = i - idx;
      const a = idx > 0 ? input[idx - 1] : this.lastSample;
      const b = input[idx];
      out.push(a + (b - a) * frac);
      i += ratio;
    }
    this.fracIndex = i - input.length;
    this.lastSample = input[input.length - 1] || 0;
    if (out.length > 0) this.acc.push(...out);
  }

  // Convertit le buffer accumulé en int16 par chunks de TARGET_CHUNK_SAMPLES
  // et poste chaque chunk au main thread.
  flushChunks() {
    while (this.acc.length >= TARGET_CHUNK_SAMPLES) {
      const slice = this.acc.splice(0, TARGET_CHUNK_SAMPLES);
      const i16 = new Int16Array(TARGET_CHUNK_SAMPLES);
      for (let j = 0; j < TARGET_CHUNK_SAMPLES; j++) {
        const v = Math.max(-1, Math.min(1, slice[j]));
        i16[j] = v < 0 ? v * 0x8000 : v * 0x7FFF;
      }
      // ``transfer`` évite la copie : le buffer passe au main thread "à coût zéro".
      this.port.postMessage(i16.buffer, [i16.buffer]);
    }
  }

  process(inputs /* , outputs, parameters */) {
    const channels = inputs[0];
    if (!channels || channels.length === 0) return true; // pas d'entrée
    // Mono : si stéréo, on prend le 1er canal (mic est mono normalement)
    const input = channels[0];
    if (input && input.length > 0) {
      this.resampleAndCollect(input);
      this.flushChunks();
    }
    return true; // continuer
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor);
