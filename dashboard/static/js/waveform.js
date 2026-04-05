/**
 * Waveform — animated wave visualization for system activity
 *
 * Usage:
 *   const wf = new Waveform(document.getElementById('canvas-id'));
 *   wf.setIntensity(0.7);  // 0 = flatline, 1 = max activity
 *   wf.destroy();           // cleanup
 */

class Waveform {
    constructor(canvas, options = {}) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.color = options.color || '190, 232, 46';
        this.intensity = 0.08;
        this.targetIntensity = 0.08;
        this.phase = Math.random() * Math.PI * 2; // random start
        this.animId = null;
        this.w = 0;
        this.h = 0;

        this._resizeBound = () => this._resize();
        window.addEventListener('resize', this._resizeBound);
        this._resize();
        this._animate();
    }

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

    setIntensity(val) {
        this.targetIntensity = Math.max(0.04, Math.min(1, val));
    }

    _animate() {
        // Smooth easing toward target
        this.intensity += (this.targetIntensity - this.intensity) * 0.025;
        this.phase += 0.014;

        const { ctx, w, h, intensity } = this;
        if (w === 0 || h === 0) {
            this.animId = requestAnimationFrame(() => this._animate());
            return;
        }

        ctx.clearRect(0, 0, w, h);

        // Wave layers — each with different frequency, amplitude, speed
        const layers = [
            { freq: 0.005,  amp: 0.32, speed: 1.0,  off: 0,    alpha: 0.55, lw: 1.5 },
            { freq: 0.008,  amp: 0.24, speed: 1.5,  off: 2.1,  alpha: 0.35, lw: 1.2 },
            { freq: 0.003,  amp: 0.42, speed: 0.6,  off: 4.2,  alpha: 0.18, lw: 2.0 },
            { freq: 0.012,  amp: 0.14, speed: 2.2,  off: 1.5,  alpha: 0.22, lw: 0.8 },
            { freq: 0.018,  amp: 0.08, speed: 2.8,  off: 3.0,  alpha: 0.12, lw: 0.6 },
        ];

        // Draw each wave layer
        layers.forEach(layer => {
            const alpha = layer.alpha * intensity * 3;
            if (alpha < 0.008) return;

            ctx.beginPath();
            ctx.strokeStyle = `rgba(${this.color}, ${Math.min(alpha, 0.75)})`;
            ctx.lineWidth = layer.lw;

            const step = 2;
            for (let x = 0; x <= w; x += step) {
                // Sine envelope for edge fade
                const edge = Math.sin((x / w) * Math.PI);
                // Compound wave with slight noise
                const wave1 = Math.sin(x * layer.freq + this.phase * layer.speed + layer.off);
                const wave2 = Math.sin(x * layer.freq * 1.7 + this.phase * layer.speed * 0.8 + layer.off + 1.0) * 0.3;
                const y = h / 2 + (wave1 + wave2) * layer.amp * h * intensity * edge;

                if (x === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
        });

        // Glow pass on primary wave when activity is high
        if (intensity > 0.12) {
            const glowAlpha = (intensity - 0.12) * 0.6;
            ctx.save();
            ctx.shadowColor = `rgba(${this.color}, 0.4)`;
            ctx.shadowBlur = 18;
            ctx.beginPath();
            ctx.strokeStyle = `rgba(${this.color}, ${Math.min(glowAlpha, 0.2)})`;
            ctx.lineWidth = 3;

            for (let x = 0; x <= w; x += 3) {
                const edge = Math.sin((x / w) * Math.PI);
                const y = h / 2 +
                    Math.sin(x * 0.005 + this.phase + 0) *
                    0.32 * h * intensity * edge;
                if (x === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
            ctx.restore();
        }

        // Center baseline — very subtle reference line
        ctx.beginPath();
        ctx.strokeStyle = `rgba(${this.color}, ${0.04 + intensity * 0.03})`;
        ctx.lineWidth = 0.5;
        ctx.moveTo(0, h / 2);
        ctx.lineTo(w, h / 2);
        ctx.stroke();

        this.animId = requestAnimationFrame(() => this._animate());
    }

    destroy() {
        if (this.animId) cancelAnimationFrame(this.animId);
        window.removeEventListener('resize', this._resizeBound);
    }
}
