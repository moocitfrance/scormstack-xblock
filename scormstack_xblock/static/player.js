function ScormStackPlayer(runtime, element, settings) {
  "use strict";
  const root = element.querySelector ? element : element[0];
  const status = root.querySelector(".ss-status");
  const slot = root.querySelector(".ss-frame");
  let frame, session, latest, pending, sequence = 0, timer, sending = false, stopped = false;
  const csrf = () => document.cookie.split("; ").find(c => c.startsWith("csrftoken="))?.split("=").slice(1).join("=") || "";
  async function post(handler, body, keepalive = false) {
    const response = await fetch(runtime.handlerUrl(element, handler), {
      method: "POST", credentials: "same-origin", keepalive,
      headers: {"Content-Type": "application/json", "X-CSRFToken": decodeURIComponent(csrf())}, body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || "Unable to save progress."); error.status = response.status; throw error; }
    return data;
  }
  function announce(message, failed) { status.textContent = message; status.classList.toggle("ss-error", !!failed); }
  async function flush() {
    clearTimeout(timer);
    if (stopped || sending || !session || session.preview || (!latest && !pending)) return;
    if (!pending) { pending = {revision: session.revision, session: session.session, sequence: ++sequence, data: latest}; latest = null; }
    sending = true;
    announce("Saving…");
    try {
      await post("save_progress", pending);
      pending = null;
      announce("Progress saved");
    } catch (error) {
      stopped = [400, 401, 403, 409].includes(error.status);
      announce(stopped ? error.message : "Progress not saved. Reconnecting…", true);
      if (!stopped) timer = setTimeout(flush, 3000);
    } finally {
      sending = false;
      if (!pending && latest) flush();
    }
  }
  const escape = s => String(s).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
  async function start() {
    if (!settings.revision) { announce("No published package yet."); return; }
    try {
      session = await post("launch", {revision: settings.revision});
      if (!root.isConnected) return;
      frame = document.createElement("iframe");
      frame.title = "ScormStack learning module";
      frame.style.height = settings.height + "px";
      frame.allow = "fullscreen; autoplay";
      // Local package code is trusted instructor content, like a native SCORM activity.
      // One outer frame per block prevents window.API collisions between activities.
      const bootstrap = JSON.stringify(session).replaceAll("<", "\\u003c");
      frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><style>html,body,iframe{margin:0;width:100%;height:100%;border:0;display:block}</style></head><body><script src="${escape(settings.runtimeUrl)}"></script><script>
        const config = ${bootstrap};
        const engine = new ScormStackEngine({...config, changed(data, immediate) {
          parent.postMessage({type:"scormstack:state", data, immediate}, ${JSON.stringify(window.location.origin)});
        }});
        window[config.version === "1.2" ? "API" : "API_1484_11"] = engine.api;
        const content = document.createElement("iframe"); content.title = "Course content";
        content.allow = "fullscreen; autoplay"; content.src = config.launch; document.body.appendChild(content);
        window.addEventListener("pagehide", () => { if(engine.initialized) engine.changed(engine.snapshot(), true); });
      </script></body></html>`;
      slot.appendChild(frame);
      announce(session.preview ? "Preview — results are not saved" : "Ready");
    } catch (error) { announce(error.message, true); }
  }
  function receive(event) {
    if (!frame || event.source !== frame.contentWindow || event.origin !== window.location.origin || event.data?.type !== "scormstack:state") return;
    if (session.preview || stopped) return;
    latest = event.data.data;
    clearTimeout(timer);
    if (event.data.immediate) flush(); else timer = setTimeout(flush, 500);
  }
  function hide() {
    if (document.visibilityState === "hidden") flush();
  }
  function unload() {
    if (session?.preview || stopped || !session) return;
    // Retry the same sequence first. Larger state is saved during normal operation.
    const body = pending || (latest && {revision: session.revision, session: session.session, sequence: sequence + 1, data: latest});
    if (body && new TextEncoder().encode(JSON.stringify(body)).length < 60000) post("save_progress", body, true).catch(() => {});
  }
  root.querySelector(".ss-fullscreen").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen().catch(() => announce("Fullscreen unavailable", true));
  });
  root.querySelector(".ss-save").addEventListener("click", flush);
  window.addEventListener("message", receive);
  window.addEventListener("pagehide", unload);
  document.addEventListener("visibilitychange", hide);
  // Learning MFE can remove a unit without navigating the document.
  const observer = new MutationObserver(() => {
    if (root.isConnected) return;
    unload(); stopped = true; clearTimeout(timer); observer.disconnect();
    window.removeEventListener("message", receive); window.removeEventListener("pagehide", unload);
    document.removeEventListener("visibilitychange", hide);
  });
  observer.observe(document.body, {childList: true, subtree: true});
  start();
}
