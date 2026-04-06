/**
 * Waveform — signal-style wave visualization driven by real system metrics
 *
 * Renders a high-density oscillation with noise-modulated amplitude envelope.
 * When fed real metrics via feedMetrics(), the envelope responds to actual
 * system activity. Without metrics, noise alone provides organic variation.
 *
 * Usage:
 *   const wf = new Waveform(canvas, { color: '190, 232, 46' });
 *   wf.setIntensity(0.7);              // master gain: 0 = flatline, 1 = max
 *   wf.feedMetrics({ activity: 0.8 }); // push real metric sample (0-1)
 *   wf.setProgress(0.35);              // scanline position (0-1), null hides
 *   wf.destroy();
 */

class Waveform {
    constructor(canvas, options = {}) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.color = options.color || '190, 232, 46';

        this.intensity = 0.05;
        this.targetIntensity = 0.05;

        this.phase = 0;
        this.time = 0;
        this.animId = null;
        this.w = 0;
        this.h = 0;

        // Rolling sample buffer — fed by real metrics each poll
        this.bufferSize = 128;
        this.buffer = new Float32Array(this.bufferSize);
        this.bufferHead = 0;

        // Value-noise permutation table
        this._perm = new Uint8Array(512);
        this._initNoise();

        this._resizeBound = () => this._resize();
        window.addEventListener('resize', this._resizeBound);
        this._resize();
        this._animate();
    }

    /* ── Noise ── */

    _initNoise() {
        const p = new Uint8Array(256);
        for (let i = 0; i < 256; i++) p[i] = i;
        let s = 42;
        for (let i = 255; i > 0; i--) {
            s = (s * 16807) % 2147483647;
            const j = s % (i + 1);
            [p[i], p[j]] = [p[j], p[i]];
        }
        for (let i = 0; i < 512; i++) this._perm[i] = p[i & 255];
    }

    _noise(x) {
        const xi = Math.floor(x) & 255;
        const xf = x - Math.floor(x);
        const u = xf * xf * (3 - 2 * xf); // smoothstep
        return (this._perm[xi] + u * (this._perm[xi + 1] - this._perm[xi])) / 255;
    }

    /* ── Resize ── */

    _resize() {
        const parent = this.canvas.parentElement;
        if (!parent) return;
        const rect = parent.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        this.canvas.width = rect.width * dpr;
        this.canvas.height = rect.height * dpr;
        this.canvas.style.width = rect.width + 'px';
        this.canvas.style.height = rect.height + 'px';
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.w = rect.width;
        this.h = rect.height;
    }

    /* ── Public API ── */

    setIntensity(val) {
        this.targetIntensity = Math.max(0.02, Math.min(1, val));
    }

    feedMetrics(delta) {
        const val = Math.max(0, Math.min(1, delta.activity || 0));
        this.buffer[this.bufferHead] = val;
        this.bufferHead = (this.bufferHead + 1) % this.bufferSize;
    }

    /* ── Buffer interpolation ── */

    _sampleBuffer(t) {
        // t: 0-1 across canvas width → interpolated buffer value
        const idx = t * (this.bufferSize - 1);
        const i0 = Math.floor(idx);
        const i1 = Math.min(i0 + 1, this.bufferSize - 1);
        const f = idx - i0;
        const v0 = this.buffer[(this.bufferHead + i0) % this.bufferSize];
        const v1 = this.buffer[(this.bufferHead + i1) % this.bufferSize];
        return v0 + f * (v1 - v0);
    }

    /* ── Render loop ── */

    _animate() {
        this.intensity += (this.targetIntensity - this.intensity) * 0.03;
        this.phase += 0.018;
        this.time += 0.004;

        const { ctx, w, h, intensity } = this;
        if (w === 0 || h === 0) {
            this.animId = requestAnimationFrame(() => this._animate());
            return;
        }

        ctx.clearRect(0, 0, w, h);

        const midY = h * 0.5;
        const maxAmp = h * 0.43;

        // ── Build points: carrier × noise envelope × buffer × intensity ──
        const points = new Float32Array(w + 1);

        for (let x = 0; x <= w; x++) {
            const t = x / w;

            // Multi-octave noise envelope (broad bursts + local detail)
            const n1 = this._noise(t * 4.5 + this.time * 0.3);   // broad modulation
            const n2 = this._noise(t * 11 + this.time * 0.5 + 47);  // medium detail
            const n3 = this._noise(t * 25 + this.time * 0.8 + 113); // fine variation
            const noiseEnv = n1 * 0.5 + n2 * 0.3 + n3 * 0.2;

            // Buffer-driven modulation
            const bufVal = this._sampleBuffer(t);

            // Combined envelope: noise shapes bursts, buffer + intensity scale
            // Floor 0.15 keeps idle shimmer; buffer can push to full 1.0
            const env = noiseEnv * (0.15 + bufVal * 0.85) * intensity * 2.5;

            // High-frequency carrier (compound sine for complex waveform shape)
            const f = x * 0.45 + this.phase * 3.5;
            const raw = Math.sin(f)
                      + Math.sin(f * 1.53 + 0.7) * 0.35
                      + Math.sin(f * 0.67 + 2.3) * 0.25;
            const carrier = raw / 1.6; // normalize to [-1, 1]

            const displacement = carrier * Math.min(env, 1.0) * maxAmp;
            points[x] = midY + displacement;
        }

        // ── Echo layer (subtle, slightly expanded) ──
        ctx.beginPath();
        ctx.strokeStyle = `rgba(${this.color}, ${Math.min(intensity * 0.12, 0.1)})`;
        ctx.lineWidth = 2.8;
        for (let x = 0; x <= w; x += 2) {
            const y = midY + (points[x] - midY) * 1.15;
            if (x === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // ── Glow layer ──
        if (intensity > 0.08) {
            ctx.save();
            ctx.shadowColor = `rgba(${this.color}, ${Math.min(intensity * 0.3, 0.25)})`;
            ctx.shadowBlur = 14;
            ctx.beginPath();
            ctx.strokeStyle = `rgba(${this.color}, ${Math.min(intensity * 0.2, 0.18)})`;
            ctx.lineWidth = 2.5;
            for (let x = 0; x <= w; x += 2) {
                if (x === 0) ctx.moveTo(x, points[x]);
                else ctx.lineTo(x, points[x]);
            }
            ctx.stroke();
            ctx.restore();
        }

        // ── Main signal line ──
        ctx.beginPath();
        ctx.strokeStyle = `rgba(${this.color}, ${Math.min(0.35 + intensity * 0.55, 0.85)})`;
        ctx.lineWidth = 1.2;
        for (let x = 0; x <= w; x++) {
            if (x === 0) ctx.moveTo(x, points[x]);
            else ctx.lineTo(x, points[x]);
        }
        ctx.stroke();

        this.animId = requestAnimationFrame(() => this._animate());
    }

    destroy() {
        if (this.animId) cancelAnimationFrame(this.animId);
        window.removeEventListener('resize', this._resizeBound);
    }
}
