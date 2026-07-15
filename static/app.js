(() => {
  "use strict";

  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const fileCountHint = document.getElementById("file-count-hint");
  const uploadBtn = document.getElementById("upload-btn");
  const uploadError = document.getElementById("upload-error");

  const jobsList = document.getElementById("jobs-list");

  const jobSection = document.getElementById("job-section");
  const progressBarFill = document.getElementById("progress-bar-fill");
  const progressText = document.getElementById("progress-text");
  const statusBadges = document.getElementById("status-badges");

  const resultsSection = document.getElementById("results-section");
  const resultsBody = document.getElementById("results-body");
  const resultsCount = document.getElementById("results-count");
  const statusFilter = document.getElementById("status-filter");
  const downloadCsvBtn = document.getElementById("download-csv-btn");

  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  const lightboxClose = document.getElementById("lightbox-close");

  const taxonomyLink = document.getElementById("taxonomy-link");
  const taxonomyModal = document.getElementById("taxonomy-modal");
  const taxonomyClose = document.getElementById("taxonomy-close");
  const taxonomyTableBody = document.querySelector("#taxonomy-table tbody");

  let selectedFiles = [];
  let taxonomyByKey = {};
  let currentJobId = null;
  let pollTimer = null;
  let lastResults = [];

  // ---------------------------------------------------------------- utils

  function severityClass(severity) {
    if (severity === "ok") return "badge-ok";
    if (severity === "warning") return "badge-warning";
    return "badge-error";
  }

  function fmtDims(row) {
    const parts = [row.length_cm, row.width_cm, row.height_cm];
    if (parts.every((v) => v === null || v === undefined)) return "—";
    return parts.map((v) => (v === null || v === undefined ? "?" : v)).join(" × ");
  }

  function fmtDate(iso) {
    if (!iso) return "";
    // Handles both Python's isoformat() (has "T" and a +HH:MM offset) and
    // SQLite's datetime('now') (space-separated, naive, always UTC).
    let s = iso;
    if (!s.includes("T")) {
      s = s.replace(" ", "T") + "Z";
    } else if (!/[Zz]|[+-]\d\d:\d\d$/.test(s)) {
      s = s + "Z";
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? iso : d.toLocaleString();
  }

  async function apiFetch(url, options) {
    let resp;
    try {
      resp = await fetch(url, options);
    } catch (e) {
      throw new Error("Could not reach the server. Check your connection and try again.");
    }
    if (!resp.ok) {
      let message = `Request failed (${resp.status})`;
      try {
        const data = await resp.json();
        message = data.detail || data.error || message;
      } catch {
        /* ignore parse errors */
      }
      throw new Error(message);
    }
    return resp.json();
  }

  // ------------------------------------------------------------ taxonomy

  async function loadTaxonomy() {
    try {
      const data = await apiFetch("/api/taxonomy");
      taxonomyByKey = {};
      statusFilter.innerHTML = '<option value="">All statuses</option>';
      taxonomyTableBody.innerHTML = "";
      data.forEach((s) => {
        taxonomyByKey[s.key] = s;
        const opt = document.createElement("option");
        opt.value = s.key;
        opt.textContent = `${s.label} (${s.code})`;
        statusFilter.appendChild(opt);

        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${s.code}</td><td>${s.label}</td><td>${s.description}</td>`;
        taxonomyTableBody.appendChild(tr);
      });
    } catch (e) {
      console.error("Failed to load taxonomy", e);
    }
  }

  taxonomyLink.addEventListener("click", (e) => {
    e.preventDefault();
    taxonomyModal.classList.remove("hidden");
  });
  taxonomyClose.addEventListener("click", () => taxonomyModal.classList.add("hidden"));
  taxonomyModal.addEventListener("click", (e) => {
    if (e.target === taxonomyModal) taxonomyModal.classList.add("hidden");
  });

  // -------------------------------------------------------------- upload

  function updateFileHint() {
    if (selectedFiles.length === 0) {
      fileCountHint.textContent = "";
      uploadBtn.disabled = true;
    } else {
      fileCountHint.textContent = `${selectedFiles.length} file(s) selected`;
      uploadBtn.disabled = false;
    }
  }

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    selectedFiles = Array.from(fileInput.files || []);
    updateFileHint();
  });

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) {
      selectedFiles = files;
      updateFileHint();
    }
  });

  uploadBtn.addEventListener("click", async () => {
    uploadError.textContent = "";
    if (selectedFiles.length === 0) {
      uploadError.textContent = "Please select at least one image to upload.";
      return;
    }
    uploadBtn.disabled = true;
    uploadBtn.textContent = "Uploading…";

    const form = new FormData();
    selectedFiles.forEach((f) => form.append("files", f, f.name));

    try {
      const data = await apiFetch("/api/jobs", { method: "POST", body: form });
      selectedFiles = [];
      fileInput.value = "";
      updateFileHint();
      await refreshJobsList();
      loadJob(data.job_id);
    } catch (e) {
      uploadError.textContent = e.message;
    } finally {
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Upload & Start Scan";
    }
  });

  // ---------------------------------------------------------- jobs list

  async function refreshJobsList() {
    try {
      const jobsData = await apiFetch("/api/jobs");
      jobsList.innerHTML = "";
      if (jobsData.length === 0) {
        jobsList.innerHTML = '<p class="hint">No batches yet — upload some photos above to get started.</p>';
        return;
      }
      jobsData.forEach((j) => {
        const row = document.createElement("div");
        row.className = "job-row";
        row.innerHTML = `
          <div>
            <div>Batch <code>${j.id}</code></div>
            <div class="job-meta">${fmtDate(j.created_at)} · ${j.total} photo(s)</div>
          </div>
          <div class="job-state state-${j.state}">${j.processed}/${j.total} · ${j.state}</div>
        `;
        row.addEventListener("click", () => loadJob(j.id));
        jobsList.appendChild(row);
      });
    } catch (e) {
      jobsList.innerHTML = `<p class="error-text">Could not load recent batches: ${e.message}</p>`;
    }
  }

  // ------------------------------------------------------------- job view

  function setUrlJob(jobId) {
    const url = new URL(window.location.href);
    url.searchParams.set("job", jobId);
    window.history.replaceState({}, "", url);
  }

  function loadJob(jobId) {
    currentJobId = jobId;
    setUrlJob(jobId);
    jobSection.classList.remove("hidden");
    resultsSection.classList.remove("hidden");
    if (pollTimer) clearInterval(pollTimer);
    pollJob();
    pollTimer = setInterval(pollJob, 1500);
  }

  async function pollJob() {
    if (!currentJobId) return;
    let job;
    try {
      job = await apiFetch(`/api/jobs/${currentJobId}`);
    } catch (e) {
      progressText.textContent = `Could not load batch status: ${e.message}`;
      return;
    }

    const pct = job.total > 0 ? Math.round((job.processed / job.total) * 100) : 100;
    progressBarFill.style.width = `${pct}%`;
    progressText.textContent =
      job.state === "done"
        ? `Done — ${job.processed} of ${job.total} photos processed.`
        : `Processing… ${job.processed} of ${job.total} photos done.`;

    statusBadges.innerHTML = "";
    Object.entries(job.status_counts || {}).forEach(([key, count]) => {
      const meta = taxonomyByKey[key];
      const span = document.createElement("span");
      span.className = `badge ${severityClass(meta ? meta.severity : "error")}`;
      span.textContent = `${meta ? meta.label : key}: ${count}`;
      statusBadges.appendChild(span);
    });

    await refreshResults();

    if (job.state === "done" && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
      refreshJobsList();
    }
  }

  async function refreshResults() {
    if (!currentJobId) return;
    try {
      lastResults = await apiFetch(`/api/jobs/${currentJobId}/results`);
      renderResults();
    } catch (e) {
      resultsCount.textContent = `Could not load results: ${e.message}`;
    }
  }

  function renderResults() {
    const filter = statusFilter.value;
    const rows = filter ? lastResults.filter((r) => r.status_key === filter) : lastResults;
    resultsCount.textContent = `${rows.length} of ${lastResults.length} shown`;

    resultsBody.innerHTML = "";
    rows.forEach((r) => {
      const tr = document.createElement("tr");

      const thumbTd = document.createElement("td");
      if (r.state === "done" && r.status_key !== "CORRUPT_FILE" && r.status_key !== "UNSUPPORTED_FILE") {
        const img = document.createElement("img");
        img.className = "thumb";
        img.loading = "lazy";
        img.src = `/api/jobs/${currentJobId}/image/${r.id}`;
        img.alt = r.original_filename;
        img.addEventListener("click", () => openLightbox(img.src));
        thumbTd.appendChild(img);
      } else {
        thumbTd.textContent = "—";
      }

      const meta = taxonomyByKey[r.status_key];
      const statusHtml = r.status_key
        ? `<span class="badge ${severityClass(meta ? meta.severity : "error")}">${
            meta ? meta.label : r.status_key
          }</span>`
        : `<span class="badge badge-warning">processing…</span>`;

      tr.innerHTML = `
        <td></td>
        <td>${r.original_filename}</td>
        <td>${statusHtml}</td>
        <td>${r.tracking_number || "—"}</td>
        <td>${r.weight_kg !== null && r.weight_kg !== undefined ? r.weight_kg : "—"}</td>
        <td>${fmtDims(r)}</td>
        <td class="notes-cell">${r.notes || ""}</td>
      `;
      tr.children[0].replaceWith(thumbTd);
      resultsBody.appendChild(tr);
    });
  }

  statusFilter.addEventListener("change", renderResults);

  downloadCsvBtn.addEventListener("click", () => {
    if (!currentJobId) return;
    window.location.href = `/api/jobs/${currentJobId}/csv`;
  });

  function openLightbox(src) {
    lightboxImg.src = src;
    lightbox.classList.remove("hidden");
  }
  lightboxClose.addEventListener("click", () => lightbox.classList.add("hidden"));
  lightbox.addEventListener("click", (e) => {
    if (e.target === lightbox) lightbox.classList.add("hidden");
  });

  // --------------------------------------------------------------- init

  async function init() {
    await loadTaxonomy();
    await refreshJobsList();
    const params = new URLSearchParams(window.location.search);
    const jobParam = params.get("job");
    if (jobParam) {
      loadJob(jobParam);
    }
  }

  init();
})();
