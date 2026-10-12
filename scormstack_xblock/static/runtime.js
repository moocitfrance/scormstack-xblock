/* Single-SCO SCORM adapter. Each player frame owns its APIs and in-memory state. */
(function (root) {
  "use strict";
  const readonly = new Set([
    "cmi.core.student_id", "cmi.core.student_name", "cmi.core.entry", "cmi.core.lesson_mode",
    "cmi.core.credit", "cmi.core.total_time", "cmi.learner_id", "cmi.learner_name", "cmi.entry",
    "cmi.mode", "cmi.credit", "cmi.total_time", "cmi.launch_data", "cmi._version"
  ]);
  const statuses = {
    "cmi.core.lesson_status": ["passed", "completed", "failed", "incomplete", "browsed", "not attempted"],
    "cmi.completion_status": ["completed", "incomplete", "not attempted", "unknown"],
    "cmi.success_status": ["passed", "failed", "unknown"],
    "cmi.core.exit": ["", "time-out", "suspend", "logout"],
    "cmi.exit": ["", "time-out", "suspend", "logout", "normal"]
  };
  class Engine {
    constructor(options) {
      this.version = options.version;
      this.data = Object.assign(Object.create(null), options.data || {});
      this.initialized = false;
      this.terminated = false;
      this.error = "0";
      this.changed = options.changed || function () {};
      const resumed = Object.keys(this.data).length > 0;
      this.defaults = {
        "cmi.core.student_id": options.learner_id || "", "cmi.core.student_name": options.learner_name || "",
        "cmi.learner_id": options.learner_id || "", "cmi.learner_name": options.learner_name || "",
        "cmi.core.lesson_status": "not attempted", "cmi.completion_status": "unknown", "cmi.success_status": "unknown",
        "cmi.core.entry": resumed ? "resume" : "ab-initio", "cmi.entry": resumed ? "resume" : "ab-initio",
        "cmi.core.credit": "credit", "cmi.credit": "credit", "cmi.core.lesson_mode": "normal", "cmi.mode": "normal",
        "cmi.core.total_time": options.total_time_12 || "0000:00:00.00", "cmi.total_time": options.total_time_2004 || "PT0S", "cmi._version": "1.0",
        "cmi.core.score.min": "0", "cmi.core.score.max": "100", "cmi.score.min": "0", "cmi.score.max": "100",
        "cmi.suspend_data": "", "cmi.core.lesson_location": "", "cmi.location": "", "cmi.launch_data": "",
        "cmi.student_data.mastery_score": "", "cmi.scaled_passing_score": "",
        "cmi.student_data.max_time_allowed": "", "cmi.max_time_allowed": "",
        "cmi.student_data.time_limit_action": "continue,no message", "cmi.time_limit_action": "continue,no message"
      };
      const api = this.version === "1.2" ? {
        LMSInitialize: (x) => this.initialize(x), LMSFinish: (x) => this.finish(x),
        LMSGetValue: (k) => this.get(k), LMSSetValue: (k, v) => this.set(k, v), LMSCommit: (x) => this.commit(x),
        LMSGetLastError: () => this.error, LMSGetErrorString: (e) => this.errorString(e), LMSGetDiagnostic: (e) => this.errorString(e || this.error)
      } : {
        Initialize: (x) => this.initialize(x), Terminate: (x) => this.finish(x),
        GetValue: (k) => this.get(k), SetValue: (k, v) => this.set(k, v), Commit: (x) => this.commit(x),
        GetLastError: () => this.error, GetErrorString: (e) => this.errorString(e), GetDiagnostic: (e) => this.errorString(e || this.error)
      };
      this.api = api;
    }
    fail(code) { this.error = String(code); return "false"; }
    success() { this.error = "0"; return "true"; }
    ready() {
      if (!this.initialized) { this.fail(this.version === "1.2" ? 301 : 123); return false; }
      if (this.terminated) { this.fail(this.version === "1.2" ? 301 : 133); return false; }
      return true;
    }
    initialize(value) {
      if (value !== "") return this.fail(201);
      if (this.initialized) return this.fail(this.version === "1.2" ? 101 : 103);
      this.initialized = true;
      return this.success();
    }
    finish(value) {
      if (value !== "") return this.fail(201);
      if (!this.ready()) return "false";
      this.changed(this.snapshot(), true);
      this.terminated = true;
      return this.success();
    }
    get(key) {
      if (!this.ready()) return "";
      if (Object.hasOwn(this.data, key)) { this.success(); return this.data[key]; }
      if (Object.hasOwn(this.defaults, key)) { this.success(); return this.defaults[key]; }
      if (/^cmi\..+\._count$/.test(key)) {
        const prefix = key.slice(0, -6);
        const indices = Object.keys(this.data).filter(k => k.startsWith(prefix)).map(k => Number(k.slice(prefix.length).split(".")[0])).filter(Number.isInteger);
        this.success(); return String(indices.length ? Math.max(...indices) + 1 : 0);
      }
      if (key === "cmi.core._children") { this.success(); return "student_id,student_name,lesson_location,credit,lesson_status,entry,score,total_time,lesson_mode,exit,session_time"; }
      if (key === "cmi.core.score._children" || key === "cmi.score._children") { this.success(); return "raw,min,max"; }
      this.fail(this.version === "1.2" ? 401 : 403);
      return "";
    }
    set(key, value) {
      if (!this.ready()) return "false";
      if (typeof key !== "string" || !/^cmi\.[A-Za-z0-9_.]+$/.test(key)) return this.fail(401);
      if (readonly.has(key) || /\._(count|children)$/.test(key)) return this.fail(this.version === "1.2" ? 403 : 404);
      value = String(value);
      if (statuses[key] && !statuses[key].includes(value)) return this.fail(this.version === "1.2" ? 405 : 406);
      if (/\.score\.(raw|min|max|scaled)$/.test(key) && (value.trim() === "" || !Number.isFinite(Number(value)))) return this.fail(this.version === "1.2" ? 405 : 406);
      if (key === "cmi.score.scaled" && (Number(value) < -1 || Number(value) > 1)) return this.fail(407);
      if (key === "cmi.core.session_time" && !/^\d{2,6}:[0-5]\d:[0-5]\d(?:\.\d{1,2})?$/.test(value)) return this.fail(405);
      if (key === "cmi.session_time" && (!/^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$/.test(value) || value === "PT")) return this.fail(406);
      const next = Object.assign(Object.create(null), this.data, { [key]: value });
      if (new TextEncoder().encode(JSON.stringify(next)).length > 1024 * 1024) return this.fail(this.version === "1.2" ? 405 : 407);
      this.data = next;
      this.changed(this.snapshot(), false);
      return this.success();
    }
    commit(value) {
      if (value !== "") return this.fail(201);
      if (!this.ready()) return "false";
      this.changed(this.snapshot(), true);
      return this.success();
    }
    snapshot() { return Object.assign({}, this.data); }
    errorString(error) {
      return ({"0": "No error", "101": "General error", "103": "Already initialized", "123": "Not initialized",
        "133": "Terminated", "201": "Invalid argument", "301": "Not initialized", "401": "Unsupported element",
        "403": "Read-only or uninitialized element", "404": "Read-only element", "405": "Incorrect data type",
        "406": "Incorrect data type", "407": "Value out of range"})[String(error)] || "SCORM error";
    }
  }
  root.ScormStackEngine = Engine;
  if (typeof module !== "undefined" && module.exports) module.exports = Engine;
})(typeof window !== "undefined" ? window : globalThis);
