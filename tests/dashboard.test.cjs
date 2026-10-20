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
    for (const id of ["voucher-code", "countdown", "live-pill", "picked"]) elements.set(id, make());
    elements.get("countdown").dataset.seconds = "10";
    for (const kind of ["pending", "ready", "tests"]) {
        const count = make(), body = make();
        elements.set(`lane-${kind}`, { count, body, querySelector: (selector) => selector === ".lane-count" ? count : body });
    }
    const timeouts = [], intervals = [], warnings = [];
    const state = { next: initial, requests: 0 };
    vm.runInNewContext(script, {
        document: {
            getElementById: (id) => elements.get(id),
            querySelector: () => elements.get("picked"),
        },
        fetch: async () => {
            state.requests++;
            if (state.next instanceof Error) throw state.next;
            return { ok: true, json: async () => state.next };
        },
        setTimeout: (callback) => timeouts.push(callback),
        setInterval: (callback) => intervals.push(callback),
        console: { warn: (...args) => warnings.push(args) },
    });
    return { elements, state, timeouts, intervals, warnings };
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
