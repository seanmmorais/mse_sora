const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));

const form = document.getElementById("batch-form");
const imagesInput = document.getElementById("images");
const promptsHiddenInput = document.getElementById("prompts_text");
const promptList = document.getElementById("prompt-list");
const addPromptBtn = document.getElementById("add-prompt-btn");
const comboSummary = document.getElementById("combination-summary");
const submitBtn = document.getElementById("submit-btn");
const submitStatus = document.getElementById("submit-status");
const resultsPanel = document.getElementById("results-panel");
const batchIdEl = document.getElementById("batch-id");
const batchCountsEl = document.getElementById("batch-counts");
const jobsBody = document.getElementById("jobs-body");

const renameForm = document.getElementById("rename-form");
const renameSubmitBtn = document.getElementById("rename-submit-btn");
const renameStatus = document.getElementById("rename-status");
const browseFolderBtn = document.getElementById("browse-folder-btn");
const renameFolderPathInput = document.getElementById("rename-folder-path");
const renameResultsPanel = document.getElementById("rename-results-panel");
const renameResultsTitle = document.getElementById("rename-results-title");
const renameResultsBody = document.getElementById("rename-results-body");

let activeBatchId = null;
let pollTimer = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function setActiveTab(tabName) {
  tabButtons.forEach((btn) => {
    const isActive = btn.dataset.tabTarget === tabName;
    btn.classList.toggle("is-active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  tabPanels.forEach((panel) => {
    panel.hidden = panel.dataset.tabPanel !== tabName;
  });
}

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => setActiveTab(btn.dataset.tabTarget));
});

function getPromptInputs() {
  return Array.from(promptList.querySelectorAll(".prompt-input"));
}

function getPrompts() {
  return getPromptInputs()
    .map((input) => input.value.trim())
    .filter(Boolean);
}

function syncPromptsHiddenField() {
  promptsHiddenInput.value = getPrompts().join("\n");
}

function updatePromptNumbers() {
  const rows = Array.from(promptList.querySelectorAll(".prompt-row"));
  rows.forEach((row, index) => {
    const label = row.querySelector(".prompt-index");
    if (label) {
      label.textContent = `Prompt ${index + 1}`;
    }
    const removeBtn = row.querySelector(".remove-prompt-btn");
    if (removeBtn) {
      removeBtn.disabled = rows.length <= 1;
    }
  });
}

function updateComboSummary() {
  syncPromptsHiddenField();
  const pictureCount = imagesInput.files ? imagesInput.files.length : 0;
  const promptCount = getPrompts().length;
  comboSummary.textContent = `${pictureCount} picture${pictureCount === 1 ? "" : "s"} x ${promptCount} prompt${promptCount === 1 ? "" : "s"} = ${pictureCount * promptCount} job${pictureCount * promptCount === 1 ? "" : "s"}`;
}

function createPromptRow(initialValue = "") {
  const row = document.createElement("div");
  row.className = "prompt-row";
  row.innerHTML = `
    <div class="prompt-row-head">
      <span class="prompt-index">Prompt</span>
      <button type="button" class="remove-prompt-btn tertiary-btn">Remove</button>
    </div>
    <input
      type="text"
      class="prompt-input"
      placeholder="Describe the image variation you want"
      value="${escapeHtml(initialValue)}"
    >
  `;

  const input = row.querySelector(".prompt-input");
  const removeBtn = row.querySelector(".remove-prompt-btn");

  input.addEventListener("input", updateComboSummary);
  removeBtn.addEventListener("click", () => {
    row.remove();
    if (getPromptInputs().length === 0) {
      promptList.appendChild(createPromptRow());
    }
    updatePromptNumbers();
    updateComboSummary();
  });

  return row;
}

function addPrompt(initialValue = "") {
  const row = createPromptRow(initialValue);
  promptList.appendChild(row);
  updatePromptNumbers();
  updateComboSummary();
  const input = row.querySelector(".prompt-input");
  if (input && !initialValue) {
    input.focus();
  }
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

function renderRenameResults(payload) {
  renameResultsPanel.hidden = false;
  renameResultsTitle.textContent = `${payload.renamed_count} file${payload.renamed_count === 1 ? "" : "s"} renamed`;
  renameResultsBody.innerHTML = (payload.files || [])
    .map((item, index) => `
      <tr>
        <td>${index + 1}</td>
        <td>${escapeHtml(item.old_name)}</td>
        <td>${escapeHtml(item.new_name)}</td>
      </tr>
    `)
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

async function renamePngs(formData) {
  const res = await fetch("/api/rename-pngs", {
    method: "POST",
    body: formData,
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(payload.detail || `Rename failed (${res.status})`);
  }
  return payload;
}

async function selectFolder() {
  const res = await fetch("/api/select-folder", { method: "POST" });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(payload.detail || `Folder picker failed (${res.status})`);
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
  if (getPrompts().length === 0) {
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

renameForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  renameStatus.textContent = "Renaming .png files...";
  renameSubmitBtn.disabled = true;

  try {
    const formData = new FormData(renameForm);
    const payload = await renamePngs(formData);
    renderRenameResults(payload);
    renameStatus.textContent = `Renamed ${payload.renamed_count} .png file${payload.renamed_count === 1 ? "" : "s"} in ${payload.folder_path}`;
  } catch (err) {
    renameStatus.textContent = err.message;
  } finally {
    renameSubmitBtn.disabled = false;
  }
});

browseFolderBtn.addEventListener("click", async () => {
  browseFolderBtn.disabled = true;
  renameStatus.textContent = "Opening folder picker...";
  try {
    const payload = await selectFolder();
    renameFolderPathInput.value = payload.folder_path || "";
    renameStatus.textContent = payload.folder_path ? "Folder selected." : "No folder selected.";
  } catch (err) {
    renameStatus.textContent = err.message;
  } finally {
    browseFolderBtn.disabled = false;
  }
});

addPromptBtn.addEventListener("click", () => addPrompt());
imagesInput.addEventListener("change", updateComboSummary);

setActiveTab("image-generation");
addPrompt("Please create a side image for this product.");
addPrompt("Please create a top image for this product.");
