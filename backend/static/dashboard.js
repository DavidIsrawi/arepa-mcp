(() => {
    const POLL_MS = 3000;
    const FEATURED_COUNT = 6;
    const OFFLINE_AFTER = 2;
    const voucher = document.getElementById("voucher-code");
    const countdown = document.getElementById("countdown");
    const live = document.getElementById("live-pill");
    const picked = document.querySelector("#lane-ready .lane-picked");
    const celebration = document.getElementById("order-celebration");
    const announcement = document.getElementById("order-announcement");
    const seenOrders = new Set();
    const highlighted = new Set();
    let initialized = false;
    let newestCreated = 0;
    let celebrationTimer;
    let seconds = Number(countdown.dataset.seconds);
    let failures = 0;

    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);

    setInterval(() => {
        seconds = Math.max(0, seconds - 1);
        countdown.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    }, 1000);

    const emptyStates = {
        tests: ["Try your first order", "Ask your agent to place a test order."],
        pending: ["The kitchen is clear", "New real orders will appear here."],
        ready: ["Nothing ready yet", "Your name will appear when your arepa is ready."],
    };

    function clearCelebration() {
        clearTimeout(celebrationTimer);
        highlighted.clear();
        celebration.classList.remove("is-celebrating");
        announcement.textContent = "";
        document.querySelectorAll(".row-new").forEach((row) => row.classList.remove("row-new"));
    }

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) clearCelebration();
    });

    function findArrivals(data) {
        const realOrders = [...data.pending, ...data.ready];
        const arrivals = initialized ? data.pending.filter((order) => {
            const created = Date.parse(order.created_at);
            return !seenOrders.has(order.id) && Number.isFinite(created) && created >= newestCreated;
        }) : [];
        for (const order of realOrders) {
            seenOrders.add(order.id);
            const created = Date.parse(order.created_at);
            if (Number.isFinite(created)) newestCreated = Math.max(newestCreated, created);
        }
        initialized = true;
        if (!arrivals.length || document.hidden) return;
        clearCelebration();
        for (const order of arrivals) highlighted.add(order.id);
        announcement.textContent = arrivals.length === 1 ? "New order in!" : `${arrivals.length} new orders in!`;
        celebration.classList.add("is-celebrating");
        celebrationTimer = setTimeout(clearCelebration, 2400);
    }

    function renderLane(kind, orders, total) {
        const section = document.getElementById(`lane-${kind}`);
        section.querySelector(".lane-count").textContent = total;
        const body = section.querySelector(".lane-body");
        if (!orders.length) {
            const [title, detail] = emptyStates[kind];
            body.innerHTML = `<div class="empty-state"><strong>${title}</strong><p>${detail}</p></div>`;
            return;
        }
        body.innerHTML = orders.map((order, index) => {
            const featured = index < FEATURED_COUNT ? " row-featured" : "";
            const collected = kind === "ready" && order.picked_up;
            const icon = order.picked_up || kind === "tests" ? "✓" : kind === "ready" ? "→" : "·";
            const fresh = kind === "pending" && highlighted.has(order.id) ? " row-new" : "";
            return `<div data-id="${escapeHtml(order.id)}" class="row-tile${featured}${collected ? " row-picked" : ""}${fresh}">
                <span class="row-icon" aria-hidden="true">${icon}</span>
                <div class="ticket-copy">
                    <p class="name-tt">${escapeHtml(order.customer_name)}</p>
                    <p class="flavor-tt">${escapeHtml(order.flavor_name)}${collected ? " · Collected" : ""}</p>
                </div>
            </div>`;
        }).join("");
    }

    async function poll() {
        try {
            const response = await fetch("/dashboard/data", { cache: "no-store" });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (typeof data.voucher !== "string" || !Number.isFinite(data.voucher_seconds)
                || !data.totals || !Number.isFinite(data.totals.picked)
                || !["pending", "ready", "tests"].every((kind) => Array.isArray(data[kind]) && Number.isFinite(data.totals[kind]))) {
                throw new Error("Invalid dashboard response");
            }
            voucher.textContent = data.voucher;
            seconds = data.voucher_seconds;
            findArrivals(data);
            for (const kind of ["pending", "ready", "tests"]) {
                renderLane(kind, data[kind], data.totals[kind]);
            }
            picked.textContent = data.totals.picked;
            failures = 0;
            live.classList.remove("offline");
            live.textContent = "Live orders · updates every 3 seconds";
        } catch (error) {
            failures += 1;
            if (failures >= OFFLINE_AFTER) {
                live.classList.add("offline");
                live.textContent = "Connection lost · showing last update · retrying";
                console.warn("Arepa Lab dashboard update failed:", error);
            }
        } finally {
            setTimeout(poll, POLL_MS);
        }
    }
    poll();
})();
