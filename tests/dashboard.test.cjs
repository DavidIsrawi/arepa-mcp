const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const vm = require("node:vm");

const script = readFileSync(resolve(__dirname, "../backend/static/dashboard.js"), "utf8");
const data = () => ({
    voucher: "ABC234", voucher_seconds: 150,
    pending: [], ready: [], tests: [],
    totals: { pending: 0, ready: 0, tests: 0, picked: 0 },
});
const settle = () => new Promise((done) => setImmediate(done));

function launch(initial) {
    const elements = new Map();
    const make = () => ({ textContent: "", innerHTML: "", dataset: {}, classList: {
        values: new Set(), add(name) { this.values.add(name); },
        remove(name) { this.values.delete(name); },
        contains(name) { return this.values.has(name); },
    } });
    for (const id of ["voucher-code", "countdown", "live-pill", "picked", "order-celebration", "order-announcement"]) elements.set(id, make());
    elements.get("countdown").dataset.seconds = "10";
    for (const kind of ["pending", "ready", "tests"]) {
        const count = make(), body = make();
        elements.set(`lane-${kind}`, { count, body, querySelector: (selector) => selector === ".lane-count" ? count : body });
    }
    const timeouts = [], intervals = [], warnings = [], effects = new Map(), listeners = {};
    const state = { next: initial, requests: 0, hidden: false };
    const rows = [make(), make()];
    vm.runInNewContext(script, {
        document: {
            getElementById: (id) => elements.get(id),
            querySelector: () => elements.get("picked"),
            querySelectorAll: () => rows,
            addEventListener: (name, callback) => { listeners[name] = callback; },
            get hidden() { return state.hidden; },
        },
        fetch: async () => {
            state.requests++;
            if (state.next instanceof Error) throw state.next;
            return { ok: true, json: async () => state.next };
        },
        setTimeout: (callback, delay) => {
            if (delay === 3000) return timeouts.push(callback);
            const id = Symbol();
            effects.set(id, callback);
            return id;
        },
        clearTimeout: (id) => effects.delete(id),
        setInterval: (callback) => intervals.push(callback),
        console: { warn: (...args) => warnings.push(args) },
    });
    return { elements, state, timeouts, intervals, warnings, effects, listeners, rows };
}

test("renders empty queues and updates voucher, counts and countdown", async () => {
    const run = launch(data());
    await settle();
    assert.equal(run.elements.get("voucher-code").textContent, "ABC234");
    assert.match(run.elements.get("lane-pending").body.innerHTML, /The kitchen is clear/);
    assert.match(run.elements.get("live-pill").textContent, /Live orders/);
    run.intervals[0]();
    assert.equal(run.elements.get("countdown").textContent, "2:29");
    assert.equal(run.timeouts.length, 1);
});

test("escapes order names, menu names and IDs in live updates", async () => {
    const payload = data();
    payload.ready = [{ id: '" onmouseover="bad', customer_name: "<img src=x>", flavor_name: "Beef & cheese", picked_up: true }];
    payload.totals.picked = 1;
    const run = launch(payload);
    await settle();
    const html = run.elements.get("lane-ready").body.innerHTML;
    assert.ok(!html.includes("<img"));
    assert.match(html, /&lt;img src=x&gt;/);
    assert.match(html, /&quot; onmouseover=&quot;bad/);
    assert.match(html, /Beef &amp; cheese/);
    assert.match(html, /row-picked/);
    assert.match(html, /Collected/);
    assert.equal(run.elements.get("picked").textContent, 1);
});

test("features only the first six orders without dropping the rest", async () => {
    const payload = data();
    payload.pending = Array.from({ length: 10 }, (_, id) => ({ id, customer_name: "Ana", flavor_name: "Arepa" }));
    payload.totals.pending = 10;
    const run = launch(payload);
    await settle();
    const html = run.elements.get("lane-pending").body.innerHTML;
    assert.equal((html.match(/row-featured/g) || []).length, 6);
    assert.equal((html.match(/data-id=/g) || []).length, 10);
});

test("test orders are not presented as physical food collections", async () => {
    const payload = data();
    payload.tests = [{ id: "test", customer_name: "Ana", flavor_name: "Arepa", picked_up: true }];
    const run = launch(payload);
    await settle();
    const html = run.elements.get("lane-tests").body.innerHTML;
    assert.ok(!html.includes("row-picked"));
    assert.ok(!html.includes("Collected"));
});

test("keeps last data, announces offline after two failures and recovers", async () => {
    const run = launch(data());
    await settle();
    run.state.next = new Error("Offline");
    run.timeouts.shift()();
    await settle();
    assert.ok(!run.elements.get("live-pill").classList.contains("offline"));
    run.timeouts.shift()();
    await settle();
    assert.ok(run.elements.get("live-pill").classList.contains("offline"));
    assert.equal(run.elements.get("voucher-code").textContent, "ABC234");
    assert.equal(run.warnings.length, 1);
    run.state.next = { ...data(), voucher: "XYZ789" };
    run.timeouts.shift()();
    await settle();
    assert.ok(!run.elements.get("live-pill").classList.contains("offline"));
    assert.equal(run.elements.get("voucher-code").textContent, "XYZ789");
});

test("rejects malformed data instead of clearing live queues", async () => {
    const run = launch(data());
    await settle();
    const before = run.elements.get("lane-pending").body.innerHTML;
    run.state.next = { ...data(), pending: null };
    for (let i = 0; i < 2; i++) {
        run.timeouts.shift()();
        await settle();
    }
    assert.equal(run.elements.get("lane-pending").body.innerHTML, before);
    assert.ok(run.elements.get("live-pill").classList.contains("offline"));
});

const order = (id, created_at = "2026-10-28T12:00:00Z") => ({
    id, created_at, customer_name: "Ana", flavor_name: "Arepa",
});
const board = (pending = [], ready = [], tests = []) => ({
    ...data(), pending, ready, tests,
    totals: { pending: pending.length, ready: ready.length, tests: tests.length, picked: 0 },
});
async function update(run, payload) {
    run.state.next = payload;
    run.timeouts.shift()();
    await settle();
}

test("initial orders are silent; a new real order celebrates and expires", async () => {
    const run = launch(board([order("existing")]));
    await settle();
    const message = run.elements.get("order-announcement");
    const celebration = run.elements.get("order-celebration");
    assert.equal(message.textContent, "");
    await update(run, board([order("new", "2026-10-28T12:01:00Z"), order("existing")]));
    assert.equal(message.textContent, "New order in!");
    assert.ok(celebration.classList.contains("is-celebrating"));
    assert.equal((run.elements.get("lane-pending").body.innerHTML.match(/row-new/g) || []).length, 1);
    run.rows.forEach((row) => row.classList.add("row-new"));
    [...run.effects.values()][0]();
    assert.equal(message.textContent, "");
    assert.ok(!celebration.classList.contains("is-celebrating"));
    assert.ok(run.rows.every((row) => !row.classList.contains("row-new")));
    assert.equal(run.effects.size, 0);
});

test("duplicate polls keep the same expiry and do not replay after expiry", async () => {
    const run = launch(board());
    await settle();
    const payload = board([order("new")]);
    await update(run, payload);
    const timer = [...run.effects.keys()][0];
    await update(run, payload);
    assert.deepEqual([...run.effects.keys()], [timer]);
    assert.match(run.elements.get("lane-pending").body.innerHTML, /row-new/);
    run.effects.get(timer)();
    await update(run, payload);
    assert.equal(run.effects.size, 0);
    assert.ok(!run.elements.get("order-celebration").classList.contains("is-celebrating"));
    assert.ok(!run.elements.get("lane-pending").body.innerHTML.includes("row-new"));
});

test("practice orders and real status transitions never celebrate", async () => {
    const existing = order("real");
    const run = launch(board([existing]));
    await settle();
    await update(run, board([], [existing], [order("test")]));
    await update(run, board([], [{ ...existing, picked_up: true }], [order("test")]));
    await update(run, board([existing]));
    assert.equal(run.effects.size, 0);
    assert.equal(run.elements.get("order-announcement").textContent, "");
});

test("new arrivals coalesce, but older capped-lane backfill is silent", async () => {
    const run = launch(board([order("existing")]));
    await settle();
    await update(run, board([order("older", "2026-10-28T11:00:00Z")]));
    assert.equal(run.effects.size, 0);
    await update(run, board([order("one", "2026-10-28T12:01:00Z"), order("two", "2026-10-28T12:01:00Z")]));
    assert.equal(run.elements.get("order-announcement").textContent, "2 new orders in!");
    assert.equal(run.effects.size, 1);
    assert.equal((run.elements.get("lane-pending").body.innerHTML.match(/row-new/g) || []).length, 2);
});

test("ready-lane baseline and invalid timestamps do not create false arrivals", async () => {
    const run = launch(board([], [order("ready", "2026-10-28T12:02:00Z")]));
    await settle();
    await update(run, board([order("backfill"), order("invalid", "invalid")]));
    assert.equal(run.effects.size, 0);
});

test("connection recovery celebrates new real orders as a single batch", async () => {
    const run = launch(board([order("existing")]));
    await settle();
    await update(run, new Error("Offline"));
    await update(run, new Error("Offline"));
    assert.equal(run.effects.size, 0);
    await update(run, board([order("one", "2026-10-28T12:01:00Z"), order("two", "2026-10-28T12:02:00Z")]));
    assert.equal(run.elements.get("order-announcement").textContent, "2 new orders in!");
    assert.equal(run.effects.size, 1);
    assert.ok(!run.elements.get("live-pill").classList.contains("offline"));
});

test("hidden arrivals are consumed without replay, and hiding clears active effects", async () => {
    const run = launch(board());
    await settle();
    run.state.hidden = true;
    await update(run, board([order("hidden")]));
    run.state.hidden = false;
    await update(run, board([order("hidden")]));
    assert.equal(run.effects.size, 0);
    await update(run, board([order("visible", "2026-10-28T12:01:00Z")]));
    assert.equal(run.effects.size, 1);
    run.state.hidden = true;
    run.listeners.visibilitychange();
    assert.equal(run.effects.size, 0);
    assert.equal(run.elements.get("order-announcement").textContent, "");
});

const mascotScript = readFileSync(resolve(__dirname, "../backend/static/dashboard-mascots.js"), "utf8");

function launchMascots({ hidden = false, reduced = false, width = 1280 } = {}) {
    const listeners = {};
    const timers = new Map();
    const classes = new Set();
    const properties = new Map();
    const attributes = new Map();
    const strip = { clientWidth: width };
    const parade = {
        classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name) },
        style: { setProperty: (name, value) => properties.set(name, value) },
        addEventListener: (name, callback) => { listeners[name] = callback; },
    };
    const toggle = {
        textContent: "Pause animation",
        setAttribute: (name, value) => attributes.set(name, value),
        addEventListener: (name, callback) => { listeners[name] = callback; },
    };
    const document = {
        hidden,
        getElementById: (id) => ({
            "board-mascots": strip, "mascot-parade": parade, "mascot-toggle": toggle,
        })[id],
        addEventListener: (name, callback) => { listeners[name] = callback; },
    };
    const motion = {
        matches: reduced,
        addEventListener: (name, callback) => { listeners.motionchange = callback; },
    };
    vm.runInNewContext(mascotScript, {
        document,
        window: { matchMedia: () => motion },
        IntersectionObserver: class {
            constructor(callback) { listeners.intersection = callback; }
            observe(element) { assert.equal(element, strip); }
        },
        setTimeout: (callback, delay) => {
            const id = Symbol();
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout: (id) => timers.delete(id),
    });
    const enter = (visible = true) => listeners.intersection([{ isIntersecting: visible }]);
    const start = () => {
        assert.equal(timers.size, 1);
        const [id, timer] = [...timers][0];
        timers.delete(id);
        timer.callback();
    };
    const finish = (target = parade, animationName = "mascot-crossing") => {
        listeners.animationend({ target, animationName });
    };
    return { document, motion, listeners, timers, classes, properties, attributes, toggle, enter, start, finish };
}

test("mascots wait, cross at a steady pace, and leave a quiet gap between crossings", () => {
    const run = launchMascots();
    assert.equal(run.timers.size, 0);
    run.enter();
    assert.equal([...run.timers.values()][0].delay, 8000);
    run.start();
    assert.ok(run.classes.has("is-strolling"));
    assert.equal(run.properties.get("--stroll-duration"), `${1460 / 70}s`);
    run.finish({}, "cat-step");
    assert.equal(run.timers.size, 0);
    run.finish();
    assert.ok(!run.classes.has("is-strolling"));
    const delay = [...run.timers.values()][0].delay;
    assert.ok(delay >= 35000 && delay <= 60000);
    run.start();
    assert.ok(run.classes.has("is-strolling"));
});

test("mascots can be paused and resumed without duplicate timers", () => {
    const run = launchMascots({ width: 375 });
    run.enter();
    run.start();
    assert.equal(run.properties.get("--stroll-duration"), "8s");
    run.listeners.click();
    assert.equal(run.attributes.get("aria-pressed"), "true");
    assert.equal(run.toggle.textContent, "Resume animation");
    assert.equal(run.timers.size, 0);
    assert.ok(!run.classes.has("is-strolling"));
    run.enter();
    assert.equal(run.timers.size, 0);
    run.listeners.click();
    assert.equal(run.attributes.get("aria-pressed"), "false");
    assert.equal(run.toggle.textContent, "Pause animation");
    run.enter();
    assert.equal(run.timers.size, 1);
});

test("mascots stop while hidden, offscreen, or reduced motion is enabled", () => {
    const run = launchMascots({ reduced: true });
    run.enter();
    assert.equal(run.timers.size, 0);
    run.motion.matches = false;
    run.listeners.motionchange();
    run.start();
    run.document.hidden = true;
    run.listeners.visibilitychange();
    assert.equal(run.timers.size, 0);
    assert.ok(!run.classes.has("is-strolling"));
    run.document.hidden = false;
    run.listeners.visibilitychange();
    run.enter(false);
    assert.equal(run.timers.size, 0);
    run.enter();
    run.start();
    run.motion.matches = true;
    run.listeners.motionchange();
    assert.equal(run.timers.size, 0);
    assert.ok(!run.classes.has("is-strolling"));
});

test("mascot strip is decorative, clipped, and separate from the live order lanes", () => {
    const template = readFileSync(resolve(__dirname, "../backend/templates/dashboard.html"), "utf8");
    const css = readFileSync(resolve(__dirname, "../backend/static/brand.css"), "utf8");
    assert.match(template, /<\/main>\s*<div id="board-mascots"/);
    assert.match(template, /class="mascot-track" aria-hidden="true"/);
    assert.match(template, /id="mascot-toggle"[^>]+type="button" aria-pressed="false"/);
    assert.match(template, /path='dashboard-mascots\.js'/);
    assert.match(css, /\.mascot-track \{[^}]*overflow: hidden;[^}]*pointer-events: none;/);
    assert.match(css, /\.mascot-track, \.mascot-toggle \{ display: none; \}/);
});

test("celebration presentation has a polite announcement and reduced-motion fallback", () => {
    const template = readFileSync(resolve(__dirname, "../backend/templates/dashboard.html"), "utf8");
    const css = readFileSync(resolve(__dirname, "../backend/static/brand.css"), "utf8");
    assert.match(template, /id="order-announcement" role="status" aria-live="polite" aria-atomic="true"/);
    assert.match(template, /class="order-pop"[^>]+aria-hidden="true" focusable="false"/);
    assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\s*[^}]+animation: none;/);
    assert.match(css, /\.order-confetti \{ display: none; \}/);
});

test("celebration reuses the existing arepa brand asset instead of redrawing it", () => {
    const template = readFileSync(resolve(__dirname, "../backend/templates/dashboard.html"), "utf8");
    const arepa = template.match(/<g class="order-arepa">([\s\S]*?)<\/g>/);
    assert.ok(arepa);
    assert.match(arepa[1], /<image href="\{\{ url_for\('static', path='arepa-lab\.svg'\)\.path \}\}"/);
    assert.doesNotMatch(arepa[1], /<(?:ellipse|path)\b/);
});
