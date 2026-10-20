(() => {
    const POLL_MS = 3000;
    const FEATURED_COUNT = 6;
    const OFFLINE_AFTER = 2;
    const voucher = document.getElementById("voucher-code");
    const countdown = document.getElementById("countdown");
    const live = document.getElementById("live-pill");
    const picked = document.querySelector("#lane-ready .lane-picked");
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
            return `<div data-id="${escapeHtml(order.id)}" class="row-tile${featured}${collected ? " row-picked" : ""}">
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
