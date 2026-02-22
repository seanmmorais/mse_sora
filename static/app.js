const form = document.getElementById("batch-form");
const imagesInput = document.getElementById("images");
const promptsInput = document.getElementById("prompts_text");
const comboSummary = document.getElementById("combination-summary");
const submitBtn = document.getElementById("submit-btn");
const submitStatus = document.getElementById("submit-status");
const resultsPanel = document.getElementById("results-panel");
const batchIdEl = document.getElementById("batch-id");
const batchCountsEl = document.getElementById("batch-counts");
const jobsBody = document.getElementById("jobs-body");

let activeBatchId = null;
let pollTimer = null;

function parsePrompts(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function updateComboSummary() {
  const pictureCount = imagesInput.files ? imagesInput.files.length : 0;
  const promptCount = parsePrompts(promptsInput.value).length;
  comboSummary.textContent = `${pictureCount} picture${pictureCount === 1 ? "" : "s"} x ${promptCount} prompt${promptCount === 1 ? "" : "s"} = ${pictureCount * promptCount} job${pictureCount * promptCount === 1 ? "" : "s"}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderBatch(batch) {
  resultsPanel.hidden = false;
  batchIdEl.textContent = batch.id;

  const counts = batch.counts || {};
  batchCountsEl.innerHTML = `
    <span class="chip">Status: ${escapeHtml(batch.status)}</span>
    <span class="chip">Total: ${counts.total ?? 0}</span>
    <span class="chip ok">Done: ${counts.completed ?? 0}</span>
    <span class="chip warn">Failed: ${counts.failed ?? 0}</span>
    <span class="chip">Running: ${(counts.submitting ?? 0) + (counts.processing ?? 0)}</span>
  `;

  jobsBody.innerHTML = batch.jobs
    .map((job) => {
      const preview = job.preview_url
        ? `<img class="thumb" src="${job.preview_url}" alt="Generated image for job ${job.sequence}">`
        : "";
      const output = job.download_url
        ? `<a href="${job.download_url}">Download</a>`
        : (job.error ? `<span class="error" title="${escapeHtml(job.error)}">Error</span>` : "");

      return `
        <tr>
          <td>${job.sequence}</td>
          <td>${escapeHtml(job.image_filename)}</td>
          <td class="prompt">${escapeHtml(job.prompt)}</td>
          <td><span class="status status--${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></td>
          <td>${escapeHtml(job.api_status || "")}</td>
          <td>${preview}</td>
          <td>${output}</td>
        </tr>
      `;
    })
    .join("");
}

async function fetchBatch(batchId) {
  const res = await fetch(`/api/batches/${encodeURIComponent(batchId)}`);
  if (!res.ok) {
    throw new Error(`Failed to load batch (${res.status})`);
  }
  return res.json();
}

function shouldStopPolling(status) {
  return status === "completed" || status === "completed_with_errors";
}

async function pollBatch(batchId) {
  try {
    const batch = await fetchBatch(batchId);
    renderBatch(batch);
    submitStatus.textContent = `Batch ${batch.id} is ${batch.status}`;

    if (shouldStopPolling(batch.status)) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  } catch (err) {
    submitStatus.textContent = err.message;
  }
}

async function createBatch(formData) {
  const res = await fetch("/api/batches", {
    method: "POST",
    body: formData,
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(payload.detail || `Batch creation failed (${res.status})`);
  }
  return payload;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  updateComboSummary();

  if ((imagesInput.files?.length || 0) === 0) {
    submitStatus.textContent = "Select at least one image.";
    return;
  }
  if (parsePrompts(promptsInput.value).length === 0) {
    submitStatus.textContent = "Enter at least one prompt.";
    return;
  }

  submitBtn.disabled = true;
  submitStatus.textContent = "Submitting batch...";

  try {
    const formData = new FormData(form);
    const payload = await createBatch(formData);
    activeBatchId = payload.batch_id;
    renderBatch(payload.batch);
    submitStatus.textContent = `Batch ${activeBatchId} submitted. Polling for updates...`;

    if (pollTimer) {
      clearInterval(pollTimer);
    }
    pollTimer = setInterval(() => {
      if (activeBatchId) {
        pollBatch(activeBatchId);
      }
    }, 3000);

    await pollBatch(activeBatchId);
  } catch (err) {
    submitStatus.textContent = err.message;
  } finally {
    submitBtn.disabled = false;
  }
});

imagesInput.addEventListener("change", updateComboSummary);
promptsInput.addEventListener("input", updateComboSummary);
updateComboSummary();
