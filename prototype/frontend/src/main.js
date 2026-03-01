"use strict";
class ContinuousAuthDemo {
    constructor() {
        this.points = [];
        this.cursor = 0;
        this.timerId = null;
        this.takeoverIndex = null;
        this.previousDecision = "ALLOW";
        this.previousRisk = "LOW";
        this.speedMs = 350;
        this.highThreshold = 0.45;
        this.mediumThreshold = 0.7;
        this.canvas = this.must("scoreChart");
        this.eventLogEl = this.must("eventLog");
        this.statusEl = this.must("engineStatus");
        this.scoreEl = this.must("metricScore");
        this.riskEl = this.must("metricRisk");
        this.decisionEl = this.must("metricDecision");
        this.windowEl = this.must("metricWindow");
        this.speedInput = this.must("streamSpeed");
        this.speedLabel = this.must("speedLabel");
        const ctx = this.canvas.getContext("2d");
        if (!ctx)
            throw new Error("Canvas context not available");
        this.ctx = ctx;
        this.bindButtons();
        this.bindSpeedControl();
        this.reset();
    }
    must(id) {
        const el = document.getElementById(id);
        if (!el)
            throw new Error(`Missing element: ${id}`);
        return el;
    }
    bindButtons() {
        this.must("startNormal").addEventListener("click", () => {
            this.start("normal");
        });
        this.must("startTakeover").addEventListener("click", () => {
            this.start("takeover");
        });
        this.must("pauseStream").addEventListener("click", () => {
            if (this.timerId === null) {
                this.resume();
            }
            else {
                this.pause();
            }
        });
        this.must("resetStream").addEventListener("click", () => {
            this.reset();
        });
    }
    bindSpeedControl() {
        this.speedInput.addEventListener("input", () => {
            this.speedMs = Number(this.speedInput.value);
            this.speedLabel.textContent = `${this.speedMs} ms`;
            if (this.timerId !== null) {
                this.pause();
                this.resume();
            }
        });
        this.speedLabel.textContent = `${this.speedMs} ms`;
    }
    start(mode) {
        var _a, _b;
        this.points = this.generateSession(mode);
        this.cursor = 0;
        this.takeoverIndex = (_b = (_a = this.points.find((p) => p.phase === "takeover")) === null || _a === void 0 ? void 0 : _a.index) !== null && _b !== void 0 ? _b : null;
        this.previousDecision = "ALLOW";
        this.previousRisk = "LOW";
        this.clearEvents();
        this.addEvent("Session initialized", "通常セッションから継続認証を開始");
        if (mode === "takeover") {
            this.addEvent("Scenario armed", "途中で操作主体が攻撃者へ切替されます");
        }
        this.render();
        this.resume();
    }
    resume() {
        if (this.timerId !== null || this.points.length === 0)
            return;
        this.statusEl.textContent = "Streaming";
        this.timerId = window.setInterval(() => this.tick(), this.speedMs);
    }
    pause() {
        if (this.timerId === null)
            return;
        window.clearInterval(this.timerId);
        this.timerId = null;
        this.statusEl.textContent = "Paused";
    }
    reset() {
        this.pause();
        this.points = [];
        this.cursor = 0;
        this.takeoverIndex = null;
        this.previousDecision = "ALLOW";
        this.previousRisk = "LOW";
        this.clearEvents();
        this.statusEl.textContent = "Idle";
        this.scoreEl.textContent = "--";
        this.riskEl.textContent = "--";
        this.decisionEl.textContent = "--";
        this.windowEl.textContent = "--";
        this.drawChart();
        this.addEvent("Ready", "シナリオを選択してストリームを開始してください");
    }
    tick() {
        if (this.cursor >= this.points.length) {
            this.pause();
            this.statusEl.textContent = "Completed";
            this.addEvent("Session complete", "全ウィンドウ処理が完了しました");
            return;
        }
        this.cursor += 1;
        const state = this.evaluate(this.cursor);
        this.renderMetrics(state);
        this.drawChart();
        this.emitStateEvents(state);
    }
    evaluate(length) {
        const recent = this.points.slice(0, length);
        const last = recent[recent.length - 1];
        let lowStreak = 0;
        for (let i = recent.length - 1; i >= 0; i -= 1) {
            if (recent[i].score < this.highThreshold)
                lowStreak += 1;
            else
                break;
        }
        let riskLevel = "LOW";
        if (last.score < this.highThreshold)
            riskLevel = "HIGH";
        else if (last.score < this.mediumThreshold)
            riskLevel = "MEDIUM";
        let decision = "ALLOW";
        if (lowStreak >= 5)
            decision = "LOCK";
        else if (lowStreak >= 3)
            decision = "STEP-UP";
        else if (riskLevel !== "LOW")
            decision = "MONITOR";
        return { riskLevel, decision, lowStreak };
    }
    emitStateEvents(state) {
        const current = this.points[this.cursor - 1];
        if (!current)
            return;
        if (this.takeoverIndex !== null && current.index === this.takeoverIndex) {
            this.addEvent("Takeover detected candidate", "操作フェーズが変更されました", "event-alert");
        }
        if (state.riskLevel !== this.previousRisk) {
            this.addEvent("Risk transition", `risk=${this.previousRisk} -> ${state.riskLevel} (score=${current.score.toFixed(3)})`, state.riskLevel === "HIGH" ? "event-alert" : undefined);
            this.previousRisk = state.riskLevel;
        }
        if (state.decision !== this.previousDecision) {
            const css = state.decision === "LOCK" ? "event-danger" : state.decision === "STEP-UP" ? "event-alert" : undefined;
            this.addEvent("Policy action", `decision=${state.decision} (low streak=${state.lowStreak})`, css);
            this.previousDecision = state.decision;
        }
    }
    render() {
        const initialState = { riskLevel: "LOW", decision: "ALLOW", lowStreak: 0 };
        const state = this.cursor > 0 ? this.evaluate(this.cursor) : initialState;
        this.renderMetrics(state);
        this.drawChart();
    }
    renderMetrics(state) {
        const current = this.points[this.cursor - 1];
        if (!current)
            return;
        this.scoreEl.textContent = current.score.toFixed(3);
        this.riskEl.textContent = state.riskLevel;
        this.decisionEl.textContent = state.decision;
        this.windowEl.textContent = String(current.index);
    }
    drawChart() {
        const ctx = this.ctx;
        const width = this.canvas.width;
        const height = this.canvas.height;
        const left = 48;
        const right = width - 24;
        const top = 24;
        const bottom = height - 30;
        const chartW = right - left;
        const chartH = bottom - top;
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = "#fcfffb";
        ctx.fillRect(0, 0, width, height);
        ctx.strokeStyle = "#d5e0d3";
        ctx.lineWidth = 1;
        for (let i = 0; i <= 5; i += 1) {
            const y = top + (chartH / 5) * i;
            ctx.beginPath();
            ctx.moveTo(left, y);
            ctx.lineTo(right, y);
            ctx.stroke();
        }
        this.drawThresholdLine(this.mediumThreshold, "#c6bb9f");
        this.drawThresholdLine(this.highThreshold, "#e4b48e");
        if (this.points.length === 0) {
            ctx.fillStyle = "#4f685d";
            ctx.font = "14px 'IBM Plex Mono', monospace";
            ctx.fillText("No stream yet. Start a scenario.", left + 12, top + 28);
            return;
        }
        const visible = this.points.slice(0, this.cursor);
        if (visible.length === 0)
            return;
        const toX = (index) => left + ((index - visible[0].index) / Math.max(1, this.points.length - 1)) * chartW;
        const toY = (score) => bottom - score * chartH;
        ctx.lineWidth = 2.4;
        ctx.beginPath();
        visible.forEach((point, i) => {
            const x = toX(point.index);
            const y = toY(point.score);
            if (i === 0)
                ctx.moveTo(x, y);
            else
                ctx.lineTo(x, y);
        });
        ctx.strokeStyle = "#0f766e";
        ctx.stroke();
        ctx.lineWidth = 2;
        for (let i = 1; i < visible.length; i += 1) {
            const prev = visible[i - 1];
            const cur = visible[i];
            if (prev.phase === cur.phase)
                continue;
            const x = toX(cur.index);
            ctx.strokeStyle = "#cf6a1f";
            ctx.beginPath();
            ctx.moveTo(x, top);
            ctx.lineTo(x, bottom);
            ctx.stroke();
            ctx.fillStyle = "#964e16";
            ctx.font = "12px 'IBM Plex Mono', monospace";
            ctx.fillText("Takeover", Math.min(x + 6, right - 70), top + 14);
        }
        const latest = visible[visible.length - 1];
        ctx.beginPath();
        ctx.arc(toX(latest.index), toY(latest.score), 4.7, 0, Math.PI * 2);
        ctx.fillStyle = latest.score < this.highThreshold ? "#b93d2f" : latest.score < this.mediumThreshold ? "#cf6a1f" : "#18865a";
        ctx.fill();
        ctx.fillStyle = "#4f685d";
        ctx.font = "12px 'IBM Plex Mono', monospace";
        ctx.fillText("score", 8, top + 4);
        ctx.fillText("1.0", 12, top + 18);
        ctx.fillText("0.0", 12, bottom);
    }
    drawThresholdLine(threshold, color) {
        const ctx = this.ctx;
        const width = this.canvas.width;
        const height = this.canvas.height;
        const left = 48;
        const right = width - 24;
        const top = 24;
        const bottom = height - 30;
        const chartH = bottom - top;
        const y = bottom - threshold * chartH;
        ctx.strokeStyle = color;
        ctx.setLineDash([6, 5]);
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(right, y);
        ctx.stroke();
        ctx.setLineDash([]);
    }
    addEvent(title, detail, className) {
        const li = document.createElement("li");
        if (className)
            li.classList.add(className);
        const now = new Date();
        const time = now.toLocaleTimeString("ja-JP", { hour12: false });
        li.textContent = `[${time}] ${title}: ${detail}`;
        this.eventLogEl.prepend(li);
        const max = 18;
        while (this.eventLogEl.children.length > max) {
            this.eventLogEl.removeChild(this.eventLogEl.lastChild);
        }
    }
    clearEvents() {
        this.eventLogEl.innerHTML = "";
    }
    generateSession(mode) {
        const points = [];
        const total = 60;
        const takeoverStart = 30;
        for (let i = 0; i < total; i += 1) {
            const genuine = i < takeoverStart || mode === "normal";
            let base = 0.75;
            let wobble = (Math.sin(i / 4.2) + Math.cos(i / 6.7)) * 0.018;
            if (!genuine && mode === "takeover") {
                base = 0.42 + Math.sin(i / 3.1) * 0.03;
                wobble += -0.03;
            }
            const noise = (Math.random() - 0.5) * 0.04;
            const score = this.clamp(base + wobble + noise, 0.18, 0.96);
            points.push({
                index: i,
                score,
                phase: genuine ? "genuine" : "takeover",
            });
        }
        return points;
    }
    clamp(v, min, max) {
        return Math.max(min, Math.min(max, v));
    }
}
new ContinuousAuthDemo();
