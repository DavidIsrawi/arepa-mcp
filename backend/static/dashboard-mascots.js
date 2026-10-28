(() => {
    const strip = document.getElementById("board-mascots");
    const parade = document.getElementById("mascot-parade");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = false;
    let timer;

    function stop() {
        clearTimeout(timer);
        timer = undefined;
        parade.classList.remove("is-strolling");
    }

    function schedule(delay = 8000) {
        stop();
        if (!visible || document.hidden || reducedMotion.matches) return;
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
    document.addEventListener("visibilitychange", () => schedule());
    reducedMotion.addEventListener("change", () => schedule());
    const observer = new IntersectionObserver(([entry]) => {
        if (visible === entry.isIntersecting) return;
        visible = entry.isIntersecting;
        schedule();
    });
    observer.observe(strip);
})();
