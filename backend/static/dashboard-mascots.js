(() => {
    const strip = document.getElementById("board-mascots");
    const parade = document.getElementById("mascot-parade");
    const toggle = document.getElementById("mascot-toggle");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = false;
    let paused = false;
    let timer;

    function stop() {
        clearTimeout(timer);
        timer = undefined;
        parade.classList.remove("is-strolling");
    }

    function schedule(delay = 8000) {
        stop();
        if (!visible || paused || document.hidden || reducedMotion.matches) return;
        timer = setTimeout(() => {
            timer = undefined;
            // Keep the pace gentle on both a phone and a wide projector.
            const duration = Math.max(8, (strip.clientWidth + 180) / 70);
            parade.style.setProperty("--stroll-duration", `${duration}s`);
            parade.classList.add("is-strolling");
        }, delay);
    }

    parade.addEventListener("animationend", (event) => {
        if (event.target !== parade || event.animationName !== "mascot-crossing") return;
        schedule(35000 + Math.random() * 25000);
    });
    toggle.addEventListener("click", () => {
        paused = !paused;
        toggle.setAttribute("aria-pressed", String(paused));
        toggle.textContent = paused ? "Resume animation" : "Pause animation";
        schedule();
    });
    document.addEventListener("visibilitychange", () => schedule());
    reducedMotion.addEventListener("change", () => schedule());
    const observer = new IntersectionObserver(([entry]) => {
        visible = entry.isIntersecting;
        schedule();
    });
    observer.observe(strip);
})();
