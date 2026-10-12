function ScormStackStudio(runtime, element) {
  "use strict";
  const root = element.querySelector ? element : element[0];
  const status = root.querySelector(".ss-status");
  const input = name => root.querySelector(`[name="${name}"]`);
  let page = 1, pages = 1, busy = false;
  const csrf = () => decodeURIComponent(document.cookie.split("; ").find(c => c.startsWith("csrftoken="))?.split("=").slice(1).join("=") || "");
  async function post(handler, data = {}) {
    const form = data instanceof FormData;
    const response = await fetch(runtime.handlerUrl(element, handler), {
      method: "POST", credentials: "same-origin", headers: {"X-CSRFToken": csrf(), ...(form ? {} : {"Content-Type": "application/json"})},
      body: form ? data : JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The operation failed. Your previous package is unchanged.");
    return result;
  }
  function message(text, failed = false) { status.textContent = text; status.classList.toggle("ss-error", failed); }
  function editor(url) { const link = root.querySelector(".ss-edit"); link.href = url; link.hidden = false; }
  async function save() {
    const result = await post("save_settings", {display_name: input("display_name").value,
      course_id: input("course_id").value.trim(), height: Number(input("height").value), has_score: input("has_score").checked});
    editor(result.editor);
  }
  async function refresh() {
    const result = await post("connection");
    root.querySelector(".ss-connection").textContent = result.connected ? "Connected" : "Not connected";
    editor(result.editor);
  }
  async function courses() {
    const result = await post("courses", {page});
    pages = result.pagination.totalPages || 1;
    input("courses").replaceChildren(new Option("Choose a module", ""), ...result.courses.map(c => new Option(c.title, c.id)));
  }
  // The editor must use the currently selected module, not a previously saved ID.
  root.querySelector(".ss-edit").addEventListener("click", async event => {
    event.preventDefault();
    if (busy) return;
    const popup = window.open("about:blank", "_blank");
    try { await save(); if (popup) { popup.opener = null; popup.location.replace(root.querySelector(".ss-edit").href); } }
    catch (error) { if (popup) popup.close(); message(error.message, true); }
  });
  input("courses").addEventListener("change", () => { if (input("courses").value) input("course_id").value = input("courses").value; });
  const actions = {
    async connect() {
      const popup = window.open("about:blank", "scormstack-connect", "width=780,height=800");
      try { const result = await post("connect"); if (popup) popup.location.replace(result.url); else message("Allow pop-ups and try connecting again.", true); }
      catch (error) { if (popup) popup.close(); throw error; }
    }, refresh, courses,
    async previous() { if (page > 1) { page--; await courses(); } },
    async next() { if (page < pages) { page++; await courses(); } },
    async disconnect() { await post("disconnect"); await refresh(); },
    async create() {
      const result = await post("create_course", {title: input("new_title").value});
      input("course_id").value = result.id; editor(result.editor);
      message("Module created. Open the editor, then return here to publish.");
    },
    async save() { await save(); runtime.notify("save", {state: "end"}); },
    async close() { runtime.notify("cancel", {}); },
    async upload() {
      const file = input("package").files[0]; if (!file) throw new Error("Choose a SCORM ZIP file.");
      await save(); const form = new FormData(); form.append("package", file);
      message("Validating and importing package…"); await post("upload", form);
      message("Package imported into Studio. Publish the unit to make it available to learners.");
      runtime.notify("save", {state: "end"});
    },
    async publish() {
      await save(); message("Exporting module with media…");
      const job = await post("publish");
      const deadline = Date.now() + 30 * 60 * 1000;
      while (root.isConnected && Date.now() < deadline) {
        const result = await post("publication_status", {job_id: job.job_id});
        if (result.status === "failed") throw new Error("Export failed in ScormStack. Check the module before retrying.");
        if (result.status === "completed") {
          message("Downloading and importing into Studio…");
          await post("install_publication", {job_id: job.job_id});
          message("Version imported. Publish the unit when you are ready.");
          runtime.notify("save", {state: "end"}); return;
        }
        message("Export in progress" + (result.progress != null ? ` · ${result.progress}%` : "…"));
        await new Promise(resolve => setTimeout(resolve, 4000));
      }
      if (root.isConnected) throw new Error("Export is taking longer than expected. Check ScormStack before starting another export.");
    }
  };
  root.addEventListener("click", async event => {
    const button = event.target.closest("button[data-action]");
    if (!button || busy) return;
    busy = true; root.querySelectorAll("button").forEach(b => { b.disabled = true; }); message("");
    try { await actions[button.dataset.action](); } catch (error) { message(error.message, true); }
    finally { busy = false; root.querySelectorAll("button").forEach(b => { b.disabled = false; }); }
  });
  refresh().catch(error => message(error.message, true));
}
