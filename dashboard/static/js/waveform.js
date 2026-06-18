/**
 * Waveform — signal-style wave visualization driven by real system metrics
 *
 * Renders a high-density oscillation with noise-modulated amplitude envelope.
 * When fed real metrics via feedMetrics(), the envelope responds to actual
 * system activity. Without metrics, noise alone provides organic variation.
 *
 * Usage:
 *   const wf = new Waveform(canvas, { color: '190, 232, 46' });
 *   wf.setIntensity(0.7);                  // master gain: 0 = flatline, 1 = max
 *   wf.feedMetrics({ activity: 0.8 });     // push real metric sample (0-1)
 *   wf.feedPressure({ load: 54, cpu: 62, mem: 71 }); // host pressure (0-100 each)
 *   wf.destroy();
 *
 * Pressure mode renders threshold lines (green cruise / amber cap / red danger)
 * across the canvas and tints the wave by the binding constraint (load/cpu/mem).
 */

// Default thresholds (0-100). Callers can override per-call via feedPressure opts.
const WAVEFORM_DEFAULT_THRESHOLDS = {
    green: 88,  // comfortable CPU cruising ceiling
    red: 95,    // hard CPU brake
    memGreen: 85,
    memRed: 93,
    loadAdvisory: 300,
};

// Tint palette keyed by binding constraint.
const WAVEFORM_CONSTRAINT_TINT = {
    load: '190, 232, 46',    // cyan-green (default)
    cpu: '255, 184, 46',     // amber
    mem: '255, 82, 82',      // red
    idle: '120, 140, 160',   // muted grey
};

class Waveform {
    constructor(canvas, options = {}) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.baseColor = options.color || '190, 232, 46';
        this.color = this.baseColor;

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

        // Pressure state (populated by feedPressure)
        this.pressure = null;
        this.thresholds = { ...WAVEFORM_DEFAULT_THRESHOLDS };
        this.showThresholds = false;

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

    /**
     * Feed real host pressure — load avg %, cpu %, mem % (all 0-100).
     * Drives the wave amplitude by the *binding constraint* (max component),
     * tints the wave by which component is binding, and enables threshold
     * line rendering so users can see the safe/danger zones.
     *
     * Pass null or {} to clear pressure mode (reverts to idle grey).
     */
    feedPressure(sample, opts = {}) {
        if (!sample || typeof sample !== 'object') {
            this.pressure = null;
            this.color = this.baseColor;
            this.showThresholds = false;
            return;
        }
        const load = Number.isFinite(sample.load) ? sample.load : null;
        const cpu = Number.isFinite(sample.cpu) ? sample.cpu : null;
        const mem = Number.isFinite(sample.mem) ? sample.mem : null;
        if (load === null && cpu === null && mem === null) {
            this.pressure = null;
            this.color = this.baseColor;
            this.showThresholds = false;
            return;
        }

        // Memory is evaluated on its own scale, so normalize it against its
        // own red threshold before comparing with CPU/load (whose red is 95).
        const memRed = this.thresholds.memRed || 93;
        const cpuRed = this.thresholds.red || 95;
        const loadAdvisory = this.thresholds.loadAdvisory || 300;
        const loadScore = load !== null ? load / loadAdvisory : null;
        const cpuScore = cpu !== null ? cpu / cpuRed : null;
        const memScore = mem !== null ? mem / memRed : null;

        let binding = 'idle';
        let bindingScore = -Infinity;
        if (cpuScore !== null && cpuScore > bindingScore) {
            binding = 'cpu';
            bindingScore = cpuScore;
        }
        if (memScore !== null && memScore > bindingScore) {
            binding = 'mem';
            bindingScore = memScore;
        }
        if (loadScore !== null && loadScore > bindingScore) {
            binding = 'load';
            bindingScore = loadScore;
        }

        const bindingActual = binding === 'load' ? load : binding === 'cpu' ? cpu : binding === 'mem' ? mem : 0;

        this.pressure = {
            load, cpu, mem,
            binding,
            bindingValue: bindingActual,
            // Amplitude follows whichever signal is closest to its own caution ceiling.
            amplitude: Math.max(0, Math.min(1, bindingScore > 0 ? bindingScore : 0)),
        };

        // Buffer is still used for shape variation, feed the binding as activity.
        this.buffer[this.bufferHead] = this.pressure.amplitude;
        this.bufferHead = (this.bufferHead + 1) % this.bufferSize;

        // Tint by binding constraint.
        this.color = WAVEFORM_CONSTRAINT_TINT[binding] || this.baseColor;
        // Master gain follows the pressure too so idle hosts flatline visually.
        this.targetIntensity = Math.max(0.08, this.pressure.amplitude);
        this.showThresholds = opts.showThresholds !== false;
    }

    setPressureThresholds(overrides = {}) {
        this.thresholds = { ...this.thresholds, ...overrides };
    }

    clearPressure() {
        this.pressure = null;
        this.color = this.baseColor;
        this.showThresholds = false;
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

    /* ── Thresholds ── */

    _renderThresholds(ctx, w, h, midY, maxAmp) {
        // Map a 0-100 pressure value to a y-offset above/below midY. We mirror
        // the wave (goes up *and* down from center) so a 50% value draws lines
        // at ±50% of maxAmp. A value of 100 covers the full amplitude band.
        const redPct = this.thresholds.red || 95;
        const greenPct = this.thresholds.green || 88;
        const pctToOffset = (pct) => (Math.max(0, Math.min(100, pct)) / 100) * maxAmp;
        const redOff = pctToOffset(redPct);
        const greenOff = pctToOffset(greenPct);

        // Green safe zone: mirrored band from midY ± greenOff
        ctx.fillStyle = 'rgba(64, 220, 120, 0.06)';
        ctx.fillRect(0, midY - greenOff, w, greenOff * 2);

        // Red danger zone: top strip above +redOff and bottom strip below -redOff
        ctx.fillStyle = 'rgba(255, 82, 82, 0.08)';
        ctx.fillRect(0, 0, w, Math.max(0, midY - redOff));
        ctx.fillRect(0, midY + redOff, w, Math.max(0, h - (midY + redOff)));

        // Threshold lines (dashed, muted)
        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;

        // Green line (top + bottom mirrored)
        ctx.strokeStyle = 'rgba(120, 220, 140, 0.55)';
        ctx.beginPath();
        ctx.moveTo(0, midY - greenOff);
        ctx.lineTo(w, midY - greenOff);
        ctx.moveTo(0, midY + greenOff);
        ctx.lineTo(w, midY + greenOff);
        ctx.stroke();

        // Red line (top + bottom mirrored)
        ctx.strokeStyle = 'rgba(255, 120, 120, 0.7)';
        ctx.beginPath();
        ctx.moveTo(0, midY - redOff);
        ctx.lineTo(w, midY - redOff);
        ctx.moveTo(0, midY + redOff);
        ctx.lineTo(w, midY + redOff);
        ctx.stroke();
        ctx.restore();

        // Baseline (midline)
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, midY);
        ctx.lineTo(w, midY);
        ctx.stroke();

        // Threshold labels (right edge)
        ctx.save();
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = 'rgba(120, 220, 140, 0.7)';
        ctx.fillText(`${greenPct}%`, w - 6, midY - greenOff - 2);
        ctx.fillStyle = 'rgba(255, 120, 120, 0.8)';
        ctx.fillText(`${redPct}%`, w - 6, midY - redOff - 2);
        ctx.restore();
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

        // ── Threshold zones + lines (pressure mode only) ──
        if (this.showThresholds) {
            this._renderThresholds(ctx, w, h, midY, maxAmp);
        }

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
