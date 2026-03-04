"use strict";
class KeystrokeRecorder {
    constructor(el, onCountChange) {
        this.el = el;
        this.onCountChange = onCountChange;
        this.events = [];
        this.activeDown = new Map();
        this.lastUpTs = null;
        this.bind();
    }
    snapshot() {
        return this.events.map((event) => ({ ...event }));
    }
    reset() {
        this.events.length = 0;
        this.activeDown.clear();
        this.lastUpTs = null;
        this.onCountChange(0);
    }
    bind() {
        this.el.addEventListener("keydown", (event) => this.handleKeyDown(event));
        this.el.addEventListener("keyup", (event) => this.handleKeyUp(event));
        this.el.addEventListener("blur", () => {
            this.activeDown.clear();
        });
    }
    handleKeyDown(event) {
        if (!this.isTrackable(event) || event.repeat)
            return;
        const id = `${event.code}:${event.location}`;
        if (this.activeDown.has(id))
            return;
        const now = performance.now();
        const flightMs = this.lastUpTs === null ? 120.0 : Math.max(10.0, now - this.lastUpTs);
        this.activeDown.set(id, {
            downTs: now,
            flightMs,
            keyCode: this.toKeyCode(event),
            isBackspace: event.key === "Backspace",
        });
    }
    handleKeyUp(event) {
        if (!this.isTrackable(event))
            return;
        const id = `${event.code}:${event.location}`;
        const meta = this.activeDown.get(id);
        if (!meta)
            return;
        const now = performance.now();
        const dwellMs = Math.max(15.0, now - meta.downTs);
        this.events.push({
            dwellMs,
            flightMs: meta.flightMs,
            keyCode: meta.keyCode,
            isBackspace: meta.isBackspace,
        });
        this.activeDown.delete(id);
        this.lastUpTs = now;
        this.onCountChange(this.events.length);
    }
    isTrackable(event) {
        if (event.key === "Backspace")
            return true;
        if (event.key.length === 1)
            return true;
        return false;
    }
    toKeyCode(event) {
        if (event.key === "Backspace")
            return 8;
        if (event.key.length === 1)
            return event.key.toLowerCase().charCodeAt(0);
        return event.keyCode || 0;
    }
}
class ContinuousAuthDemo {
    constructor() {
        this.enrollmentSamples = [];
        this.points = [];
        this.cursor = 0;
        this.timerId = null;
        this.takeoverIndex = null;
        this.previousDecision = "ALLOW";
        this.previousRisk = "LOW";
        this.highThreshold = 0.45;
        this.mediumThreshold = 0.7;
        this.requiredEventCount = 24;
        this.speedMs = 350;
        this.loading = false;
        this.canvas = this.must("scoreChart");
        this.eventLogEl = this.must("eventLog");
        this.statusEl = this.must("engineStatus");
        this.scoreEl = this.must("metricScore");
        this.riskEl = this.must("metricRisk");
        this.decisionEl = this.must("metricDecision");
        this.windowEl = this.must("metricWindow");
        this.enrollInput = this.must("enrollInput");
        this.authInput = this.must("authInput");
        this.enrollEventCountEl = this.must("enrollEventCount");
        this.enrollSampleCountEl = this.must("enrollSampleCount");
        this.authEventCountEl = this.must("authEventCount");
        this.activeThresholdEl = this.must("activeThreshold");
        this.modelStatusEl = this.must("modelStatus");
        this.speedInput = this.must("streamSpeed");
        this.speedLabel = this.must("speedLabel");
        const ctx = this.canvas.getContext("2d");
        if (!ctx)
            throw new Error("Canvas context not available");
        this.ctx = ctx;
        this.enrollRecorder = new KeystrokeRecorder(this.enrollInput, (count) => {
            this.enrollEventCountEl.textContent = String(count);
        });
        this.authRecorder = new KeystrokeRecorder(this.authInput, (count) => {
            this.authEventCountEl.textContent = String(count);
        });
        this.bindButtons();
        this.bindSpeedControl();
        this.resetPlayback();
        this.addEvent("Ready", "1) Enrollment sampleを4件以上追加 2) Train RC Model 3) Authenticationを実行");
        void this.refreshEnrollmentStatus();
    }
    must(id) {
        const el = document.getElementById(id);
        if (!el)
            throw new Error(`Missing element: ${id}`);
        return el;
    }
    bindButtons() {
        this.must("addEnrollSample").addEventListener("click", () => {
            this.addEnrollmentSample();
        });
        this.must("clearEnrollInput").addEventListener("click", () => {
            this.clearEnrollInput();
        });
        this.must("trainModel").addEventListener("click", () => {
            void this.trainModel();
        });
        this.must("resetModel").addEventListener("click", () => {
            void this.resetModel();
        });
        this.must("runAuthenticate").addEventListener("click", () => {
            void this.runAuthentication();
        });
        this.must("clearAuthInput").addEventListener("click", () => {
            this.clearAuthInput();
        });
        this.must("pauseStream").addEventListener("click", () => {
            if (this.timerId === null)
                this.resume();
            else
                this.pause();
        });
        this.must("resetStream").addEventListener("click", () => {
            this.resetPlayback();
            this.addEvent("Chart reset", "スコア表示を初期化しました");
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
    async refreshEnrollmentStatus() {
        try {
            const status = await this.getJson("/api/enroll/status");
            this.applyEnrollmentStatus(status);
        }
        catch (error) {
            const detail = error instanceof Error ? error.message : "unknown error";
            this.addEvent("API warning", `status fetch failed: ${detail}`, "event-alert");
        }
    }
    applyEnrollmentStatus(status) {
        this.requiredEventCount = Math.max(8, Number(status.windowSize || 24));
        this.enrollSampleCountEl.textContent = String(this.enrollmentSamples.length);
        if (!status.enrolled || !status.thresholds) {
            this.modelStatusEl.textContent = "Not enrolled";
            this.activeThresholdEl.textContent = "--";
            return;
        }
        this.highThreshold = Number(status.thresholds.high);
        this.mediumThreshold = Number(status.thresholds.medium);
        this.activeThresholdEl.textContent = this.highThreshold.toFixed(3);
        this.modelStatusEl.textContent = `Enrolled (${status.sampleCount} samples)`;
    }
    addEnrollmentSample() {
        const sample = this.enrollRecorder.snapshot();
        if (sample.length < this.requiredEventCount) {
            this.addEvent("Sample rejected", `keys=${sample.length} は不足です (最低 ${this.requiredEventCount})`, "event-alert");
            return;
        }
        this.enrollmentSamples.push(sample);
        this.enrollSampleCountEl.textContent = String(this.enrollmentSamples.length);
        this.addEvent("Enrollment sample added", `sample=${this.enrollmentSamples.length}, keys=${sample.length}`);
        this.clearEnrollInput();
    }
    clearEnrollInput() {
        this.enrollInput.value = "";
        this.enrollRecorder.reset();
    }
    clearAuthInput() {
        this.authInput.value = "";
        this.authRecorder.reset();
    }
    async trainModel() {
        if (this.loading)
            return;
        if (this.enrollmentSamples.length < 4) {
            this.addEvent("Enrollment blocked", "4サンプル以上を追加してから学習してください", "event-alert");
            return;
        }
        this.loading = true;
        this.pause();
        this.statusEl.textContent = "Loading";
        try {
            const response = await this.postJson("/api/enroll", {
                samples: this.enrollmentSamples,
            });
            this.highThreshold = this.clamp(Number(response.thresholds.high), 0.0, 1.0);
            this.mediumThreshold = this.clamp(Number(response.thresholds.medium), 0.0, 1.0);
            this.activeThresholdEl.textContent = this.highThreshold.toFixed(3);
            this.modelStatusEl.textContent = `Enrolled (${response.sampleCount} samples)`;
            this.requiredEventCount = Math.max(8, Number(response.windowSize || this.requiredEventCount));
            this.addEvent("Enrollment completed", `threshold=${this.highThreshold.toFixed(3)}, gap=${response.quality.separationGap.toFixed(3)}`);
            this.enrollmentSamples = [];
            this.enrollSampleCountEl.textContent = "0";
        }
        catch (error) {
            const detail = error instanceof Error ? error.message : "unknown error";
            this.addEvent("Enrollment error", detail, "event-danger");
            this.statusEl.textContent = "API Error";
        }
        finally {
            this.loading = false;
            if (this.statusEl.textContent === "Loading")
                this.statusEl.textContent = "Idle";
        }
    }
    async resetModel() {
        if (this.loading)
            return;
        this.loading = true;
        this.pause();
        this.statusEl.textContent = "Loading";
        try {
            const status = await this.postJson("/api/enroll/reset", {});
            this.enrollmentSamples = [];
            this.enrollSampleCountEl.textContent = "0";
            this.applyEnrollmentStatus(status);
            this.addEvent("Model reset", "登録状態をクリアしました");
        }
        catch (error) {
            const detail = error instanceof Error ? error.message : "unknown error";
            this.addEvent("Reset error", detail, "event-danger");
            this.statusEl.textContent = "API Error";
        }
        finally {
            this.loading = false;
            if (this.statusEl.textContent === "Loading")
                this.statusEl.textContent = "Idle";
        }
    }
    async runAuthentication() {
        if (this.loading)
            return;
        const events = this.authRecorder.snapshot();
        if (events.length < this.requiredEventCount) {
            this.addEvent("Auth blocked", `keys=${events.length} は不足です (最低 ${this.requiredEventCount})`, "event-alert");
            return;
        }
        this.loading = true;
        this.pause();
        this.statusEl.textContent = "Loading";
        try {
            const response = await this.postJson("/api/authenticate", {
                events,
            });
            this.highThreshold = this.clamp(Number(response.thresholds.high), 0.0, 1.0);
            this.mediumThreshold = this.clamp(Number(response.thresholds.medium), 0.0, 1.0);
            this.activeThresholdEl.textContent = this.highThreshold.toFixed(3);
            this.points = this.normalizePoints(response.points);
            this.cursor = 0;
            this.takeoverIndex = null;
            this.previousDecision = "ALLOW";
            this.previousRisk = "LOW";
            this.render();
            this.resume();
            const detail =
                `accepted=${response.accepted ? "yes" : "no"}, ` +
                    `decision=${response.decision}, ` +
                    `avg=${response.summary.avgScore.toFixed(3)}, ` +
                    `windows=${response.summary.windows}`;
            this.addEvent("Authentication executed", detail, response.accepted ? undefined : "event-alert");
            this.clearAuthInput();
        }
        catch (error) {
            const detail = error instanceof Error ? error.message : "unknown error";
            this.addEvent("Auth error", detail, "event-danger");
            this.statusEl.textContent = "API Error";
        }
        finally {
            this.loading = false;
            if (this.statusEl.textContent === "Loading")
                this.statusEl.textContent = "Idle";
        }
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
    resetPlayback() {
        this.pause();
        this.points = [];
        this.cursor = 0;
        this.takeoverIndex = null;
        this.previousDecision = "ALLOW";
        this.previousRisk = "LOW";
        this.statusEl.textContent = "Idle";
        this.scoreEl.textContent = "--";
        this.riskEl.textContent = "--";
        this.decisionEl.textContent = "--";
        this.windowEl.textContent = "--";
        this.drawChart();
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
            this.addEvent("Takeover candidate", "操作フェーズが変更されました", "event-alert");
        }
        if (state.riskLevel !== this.previousRisk) {
            this.addEvent("Risk transition", `risk=${this.previousRisk} -> ${state.riskLevel} (score=${current.score.toFixed(3)})`, state.riskLevel === "HIGH" ? "event-alert" : undefined);
            this.previousRisk = state.riskLevel;
        }
        if (state.decision !== this.previousDecision) {
            const css = state.decision === "LOCK"
                ? "event-danger"
                : state.decision === "STEP-UP"
                    ? "event-alert"
                    : undefined;
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
            ctx.fillText("No stream yet. Enroll and run authentication.", left + 12, top + 28);
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
        const latest = visible[visible.length - 1];
        ctx.beginPath();
        ctx.arc(toX(latest.index), toY(latest.score), 4.7, 0, Math.PI * 2);
        ctx.fillStyle = latest.score < this.highThreshold
            ? "#b93d2f"
            : latest.score < this.mediumThreshold
                ? "#cf6a1f"
                : "#18865a";
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
    async getJson(path) {
        const response = await fetch(path, {
            method: "GET",
            headers: { Accept: "application/json" },
        });
        return this.parseJsonResponse(response);
    }
    async postJson(path, payload) {
        const response = await fetch(path, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Accept: "application/json",
            },
            body: JSON.stringify(payload),
        });
        return this.parseJsonResponse(response);
    }
    async parseJsonResponse(response) {
        const raw = await response.text();
        let body = {};
        if (raw.length > 0) {
            try {
                body = JSON.parse(raw);
            }
            catch {
                body = { error: raw };
            }
        }
        if (!response.ok) {
            const message = body &&
                typeof body === "object" &&
                "error" in body &&
                typeof body.error === "string"
                ? body.error
                : `HTTP ${response.status}`;
            throw new Error(message);
        }
        return body;
    }
    normalizePoints(pointsRaw) {
        if (!Array.isArray(pointsRaw))
            throw new Error("invalid response: points");
        return pointsRaw.map((pointRaw, idx) => {
            if (!pointRaw || typeof pointRaw !== "object") {
                throw new Error(`invalid response: point[${idx}]`);
            }
            const point = pointRaw;
            if (typeof point.score !== "number") {
                throw new Error(`invalid response: point[${idx}].score`);
            }
            const phase = point.phase === "takeover" ? "takeover" : "genuine";
            return {
                index: typeof point.index === "number" ? Number(point.index) : idx,
                score: this.clamp(point.score, 0.0, 1.0),
                phase,
            };
        });
    }
    addEvent(title, detail, className) {
        const li = document.createElement("li");
        if (className)
            li.classList.add(className);
        const now = new Date();
        const time = now.toLocaleTimeString("ja-JP", { hour12: false });
        li.textContent = `[${time}] ${title}: ${detail}`;
        this.eventLogEl.prepend(li);
        const max = 24;
        while (this.eventLogEl.children.length > max) {
            this.eventLogEl.removeChild(this.eventLogEl.lastChild);
        }
    }
    clamp(v, min, max) {
        return Math.max(min, Math.min(max, v));
    }
}
new ContinuousAuthDemo();
